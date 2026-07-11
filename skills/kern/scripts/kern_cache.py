#!/usr/bin/env python3
"""Content-addressed lazy/JIT cache for KERN-IL code pages."""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "kern-cache/0.1"
CODEC_VERSION = "kern-il/0.1"
BASELINE_GENERATOR = "deterministic-baseline/0.1"
CACHE_DIRNAME = ".kern"
DEFAULT_CONFIG: dict[str, Any] = {
    "schema": SCHEMA,
    "include_extensions": [
        ".c", ".cc", ".cpp", ".cs", ".css", ".ex", ".exs", ".go",
        ".h", ".hpp", ".html", ".java", ".js", ".jsx", ".kt", ".kts",
        ".lua", ".php", ".py", ".rb", ".rs", ".scala", ".sh", ".sql",
        ".svelte", ".swift", ".toml", ".ts", ".tsx", ".vue", ".yaml", ".yml",
    ],
    "include_names": ["Dockerfile", "Makefile", "Rakefile", "Gemfile"],
    "exclude_dirs": [
        ".git", ".hg", ".svn", CACHE_DIRNAME, ".cache", ".idea", ".next",
        ".pytest_cache", ".tox", ".venv", ".vscode", "__pycache__", "build",
        "coverage", "dist", "node_modules", "target", "vendor", "venv",
    ],
    "max_file_bytes": 2_000_000,
    "image_profile": "dense",
}

SECRET_NAME = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|auth|bearer|credential|passwd|password|private[_-]?key|secret|token)"
)
SECRET_VALUE = re.compile(
    r"(?i)(?:sk|rk|pk|s2)[_-][A-Za-z0-9_-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:aws|ghp|github_pat)_[A-Za-z0-9_-]{12,}"
)
SPACE = re.compile(r"\s+")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def atomic_json(path: Path, value: object) -> None:
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


class CacheLock:
    def __init__(self, path: Path, timeout: float = 15.0, stale_after: float = 120.0):
        self.path = path
        self.timeout = timeout
        self.stale_after = stale_after
        self.acquired = False

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, f"{os.getpid()} {time.time()}\n".encode())
                os.close(fd)
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale_after:
                        self.path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Timed out waiting for cache lock: {self.path}")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        if self.acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def repo_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Repository directory does not exist: {root}")
    return root


def cache_paths(root: Path) -> dict[str, Path]:
    cache = root / CACHE_DIRNAME
    return {
        "cache": cache,
        "config": cache / "config.json",
        "manifest": cache / "manifest.json",
        "lock": cache / ".lock",
        "ir": cache / "ir",
        "images": cache / "images",
        "jobs": cache / "jobs",
        "staging": cache / "staging",
    }


def initialize(root: Path) -> tuple[dict[str, Path], dict[str, Any]]:
    paths = cache_paths(root)
    for key in ("cache", "ir", "images", "jobs", "staging"):
        paths[key].mkdir(parents=True, exist_ok=True)
        try:
            paths[key].chmod(0o700)
        except OSError:
            pass
    gitignore = paths["cache"] / ".gitignore"
    if not gitignore.exists():
        atomic_write(gitignore, b"*\n!.gitignore\n")
    if paths["config"].exists():
        config = json.loads(paths["config"].read_text(encoding="utf-8"))
        merged = copy.deepcopy(DEFAULT_CONFIG)
        merged.update(config)
        config = merged
    else:
        config = copy.deepcopy(DEFAULT_CONFIG)
        atomic_json(paths["config"], config)
    if not paths["manifest"].exists():
        atomic_json(
            paths["manifest"],
            {
                "schema": SCHEMA,
                "codec_version": CODEC_VERSION,
                "repo_root": str(root),
                "updated_at": now_iso(),
                "files": {},
            },
        )
    else:
        with CacheLock(paths["lock"]):
            manifest = load_manifest(paths["manifest"], root)
            if manifest.get("codec_version") != CODEC_VERSION:
                for record in manifest["files"].values():
                    if record.get("status") not in {"deleted", "missing"}:
                        record["status"] = "stale"
                    record["image_status"] = "stale"
                manifest["codec_version"] = CODEC_VERSION
                manifest["codec_invalidated_at"] = now_iso()
                manifest["updated_at"] = now_iso()
                atomic_json(paths["manifest"], manifest)
    return paths, config


def load_manifest(path: Path, root: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid KERN manifest {path}: {exc}") from exc
    if manifest.get("schema") != SCHEMA:
        raise RuntimeError(f"Unsupported manifest schema: {manifest.get('schema')!r}")
    if Path(manifest.get("repo_root", "")).resolve() != root:
        raise RuntimeError("Manifest repository root does not match --repo")
    manifest.setdefault("files", {})
    return manifest


def normalize_rel(root: Path, value: str, require_file: bool = True) -> tuple[str, Path]:
    candidate = Path(value).expanduser()
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes repository root: {path}") from exc
    if CACHE_DIRNAME in relative.parts:
        raise ValueError("Source path may not point inside .kern")
    if require_file and not path.is_file():
        raise ValueError(f"Source file does not exist: {path}")
    return relative.as_posix(), path


def artifact_paths(paths: dict[str, Path], relative: str) -> dict[str, Path]:
    rel_path = Path(relative)
    return {
        "ir": paths["ir"] / Path(relative + ".kern-il.txt"),
        "images": paths["images"] / rel_path,
        "job": paths["jobs"] / Path(relative + ".job.json"),
    }


def git_files(root: Path) -> list[str] | None:
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            return None
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return [item.decode("utf-8", "surrogateescape") for item in result.stdout.split(b"\0") if item]
    except (OSError, subprocess.SubprocessError):
        return None


def walk_files(root: Path, excluded: set[str]) -> Iterable[str]:
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in excluded]
        base = Path(current)
        for name in files:
            path = base / name
            if not path.is_symlink():
                yield path.relative_to(root).as_posix()


def supported(relative: str, config: dict[str, Any]) -> bool:
    path = Path(relative)
    return path.name in set(config["include_names"]) or path.suffix.lower() in set(config["include_extensions"])


def discover(root: Path, config: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    excluded = set(config["exclude_dirs"])
    candidates = git_files(root)
    if candidates is None:
        candidates = list(walk_files(root, excluded))
    snapshot: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []
    max_bytes = int(config["max_file_bytes"])
    for relative in sorted(set(candidates)):
        if any(part in excluded for part in Path(relative).parts[:-1]) or not supported(relative, config):
            continue
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
            stat = path.stat()
        except (OSError, ValueError):
            continue
        if not path.is_file() or stat.st_size > max_bytes:
            skipped.append(relative)
            continue
        data = path.read_bytes()
        if b"\x00" in data[:8192]:
            skipped.append(relative)
            continue
        snapshot[relative] = {
            "source_sha256": sha256_bytes(data),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return snapshot, skipped


def scan(root: Path, paths: dict[str, Path], config: dict[str, Any]) -> dict[str, Any]:
    snapshot, skipped = discover(root, config)
    changed: list[str] = []
    added: list[str] = []
    deleted: list[str] = []
    with CacheLock(paths["lock"]):
        manifest = load_manifest(paths["manifest"], root)
        records = manifest["files"]
        for relative, current in snapshot.items():
            old = records.get(relative)
            if old is None:
                records[relative] = {
                    **current,
                    "status": "missing",
                    "discovered_at": now_iso(),
                }
                added.append(relative)
            elif old.get("source_sha256") != current["source_sha256"]:
                old.update(current)
                old["status"] = "stale"
                old["image_status"] = "stale"
                old["changed_at"] = now_iso()
                changed.append(relative)
            else:
                old.update(current)
                if old.get("status") == "deleted":
                    old["status"] = "stale"
                    changed.append(relative)
        for relative, old in list(records.items()):
            if relative not in snapshot and old.get("status") != "deleted":
                old["status"] = "deleted"
                old["deleted_at"] = now_iso()
                deleted.append(relative)
        manifest["updated_at"] = now_iso()
        atomic_json(paths["manifest"], manifest)
    counts: dict[str, int] = {}
    for record in manifest["files"].values():
        state = record.get("status", "unknown")
        counts[state] = counts.get(state, 0) + 1
    return {
        "ok": True,
        "operation": "scan",
        "repo": str(root),
        "manifest": str(paths["manifest"]),
        "counts": counts,
        "added": added,
        "changed": changed,
        "deleted": deleted,
        "skipped": skipped,
    }


def refresh_one(root: Path, paths: dict[str, Path], relative: str, source: Path) -> tuple[str, dict[str, Any]]:
    data = source.read_bytes()
    if b"\x00" in data[:8192]:
        raise ValueError(f"Binary source is not supported: {relative}")
    digest = sha256_bytes(data)
    stat = source.stat()
    with CacheLock(paths["lock"]):
        manifest = load_manifest(paths["manifest"], root)
        record = manifest["files"].get(relative, {})
        if record.get("source_sha256") != digest:
            record["status"] = "stale" if record else "missing"
            record["image_status"] = "stale"
            record["changed_at"] = now_iso()
        record.update({"source_sha256": digest, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
        manifest["files"][relative] = record
        manifest["updated_at"] = now_iso()
        atomic_json(paths["manifest"], manifest)
    return digest, record


def sanitize_string(value: str, secret_hint: bool = False) -> str:
    digest = sha256_bytes(value.encode("utf-8", "surrogatepass"))[:12]
    if secret_hint or SECRET_VALUE.search(value):
        return f"<REDACTED len={len(value)} sha256={digest}>"
    if len(value) > 160:
        return f"<STR len={len(value)} sha256={digest}>"
    return value


class LiteralSanitizer(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(sanitize_string(node.value)), node)
        return node


def expr(node: ast.AST | None, max_length: int = 260, secret_hint: bool = False) -> str:
    if node is None:
        return "None"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        value = sanitize_string(node.value, secret_hint)
        rendered = repr(value)
    else:
        clone = LiteralSanitizer().visit(copy.deepcopy(node))
        ast.fix_missing_locations(clone)
        try:
            rendered = ast.unparse(clone)
        except Exception:
            rendered = f"<{node.__class__.__name__}>"
    rendered = SPACE.sub(" ", rendered).strip()
    if secret_hint and rendered and not rendered.startswith("'<REDACTED"):
        digest = sha256_bytes(rendered.encode())[:12]
        rendered = f"<REDACTED_EXPR len={len(rendered)} sha256={digest}>"
    if len(rendered) > max_length:
        digest = sha256_bytes(rendered.encode())[:12]
        rendered = rendered[: max_length - 32] + f"…<sha256={digest}>"
    return rendered


def target_text(node: ast.AST) -> str:
    try:
        return SPACE.sub(" ", ast.unparse(node)).strip()
    except Exception:
        return f"<{node.__class__.__name__}>"


def call_name(node: ast.Call) -> str:
    try:
        return expr(node.func, 100)
    except Exception:
        return "<call>"


def outline(statements: list[ast.stmt], depth: int = 0, limit: int = 140) -> list[str]:
    result: list[str] = []

    def emit(node: ast.AST, opcode: str, detail: str = "") -> None:
        if len(result) >= limit:
            return
        prefix = "  " * depth
        line = getattr(node, "lineno", "?")
        result.append(f"{prefix}{line}|{opcode}" + (f" {detail}" if detail else ""))

    for statement in statements:
        if len(result) >= limit:
            break
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            emit(statement, "NESTED", getattr(statement, "name", "?"))
        elif isinstance(statement, ast.Assign):
            names = ",".join(target_text(item) for item in statement.targets)
            emit(statement, "SET", f"{names}={expr(statement.value, secret_hint=bool(SECRET_NAME.search(names)))}")
        elif isinstance(statement, ast.AnnAssign):
            name = target_text(statement.target)
            emit(statement, "SET", f"{name}:{expr(statement.annotation)}={expr(statement.value, secret_hint=bool(SECRET_NAME.search(name)))}")
        elif isinstance(statement, ast.AugAssign):
            emit(statement, "MUT", f"{target_text(statement.target)} {statement.op.__class__.__name__}= {expr(statement.value)}")
        elif isinstance(statement, ast.If):
            emit(statement, "IF", expr(statement.test))
            result.extend(outline(statement.body, depth + 1, max(0, limit - len(result))))
            if statement.orelse and len(result) < limit:
                emit(statement, "ELSE")
                result.extend(outline(statement.orelse, depth + 1, max(0, limit - len(result))))
        elif isinstance(statement, (ast.For, ast.AsyncFor)):
            emit(statement, "LOOP", f"{target_text(statement.target)} in {expr(statement.iter)}")
            result.extend(outline(statement.body, depth + 1, max(0, limit - len(result))))
        elif isinstance(statement, ast.While):
            emit(statement, "WHILE", expr(statement.test))
            result.extend(outline(statement.body, depth + 1, max(0, limit - len(result))))
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            emit(statement, "WITH", ", ".join(expr(item.context_expr) for item in statement.items))
            result.extend(outline(statement.body, depth + 1, max(0, limit - len(result))))
        elif isinstance(statement, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            emit(statement, "TRY")
            result.extend(outline(statement.body, depth + 1, max(0, limit - len(result))))
            for handler in statement.handlers:
                emit(handler, "CATCH", expr(handler.type))
                result.extend(outline(handler.body, depth + 1, max(0, limit - len(result))))
            if statement.finalbody:
                emit(statement, "FINALLY")
                result.extend(outline(statement.finalbody, depth + 1, max(0, limit - len(result))))
        elif isinstance(statement, ast.Return):
            emit(statement, "RET", expr(statement.value))
        elif isinstance(statement, ast.Raise):
            emit(statement, "ERR", expr(statement.exc))
        elif isinstance(statement, ast.Assert):
            emit(statement, "ASSERT", expr(statement.test))
        elif isinstance(statement, ast.Expr):
            value = statement.value
            if isinstance(value, ast.Await):
                emit(statement, "AWAIT", expr(value.value))
            elif isinstance(value, ast.Call):
                emit(statement, "CALL", expr(value))
            elif isinstance(value, (ast.Yield, ast.YieldFrom)):
                emit(statement, "YIELD", expr(getattr(value, "value", None)))
        elif isinstance(statement, ast.Match):
            emit(statement, "MATCH", expr(statement.subject))
            for case in statement.cases:
                emit(statement, "CASE", expr(case.pattern))
                result.extend(outline(case.body, depth + 1, max(0, limit - len(result))))
        elif isinstance(statement, ast.Delete):
            emit(statement, "DEL", ",".join(target_text(item) for item in statement.targets))
        elif isinstance(statement, ast.Break):
            emit(statement, "BREAK")
        elif isinstance(statement, ast.Continue):
            emit(statement, "CONTINUE")
    return result[:limit]


def function_card(node: ast.FunctionDef | ast.AsyncFunctionDef, qualified: str) -> list[str]:
    prefix = "ASYNC F" if isinstance(node, ast.AsyncFunctionDef) else "F"
    signature = expr(node.args, 300)
    returns = expr(node.returns, 140) if node.returns else "Any"
    decorators = [expr(item, 100) for item in node.decorator_list]
    calls: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = call_name(child)
            if name not in calls:
                calls.append(name)
    lines = [f"{prefix} {qualified}({signature})->{returns} @L{node.lineno}-{node.end_lineno or node.lineno}"]
    if decorators:
        lines.append("  DECORATORS " + ", ".join(decorators))
    doc = ast.get_docstring(node, clean=True)
    if doc:
        lines.append("  DOC " + sanitize_string(doc.splitlines()[0])[:180])
    if calls:
        shown = calls[:40]
        lines.append("  CALLS " + ", ".join(shown) + (f" …+{len(calls)-40}" if len(calls) > 40 else ""))
    flow = outline(node.body)
    lines.extend("  " + item for item in flow)
    if len(flow) >= 140:
        lines.append("  [...] flow outline capped; fault exact source")
    return lines


def python_ir(text: str, relative: str, digest: str) -> str:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return generic_ir(text, relative, digest, f"python parse failed at L{exc.lineno}: {exc.msg}")
    lines = [
        CODEC_VERSION.upper(),
        f"source_rel={relative}",
        f"source_sha256={digest}",
        f"generator={BASELINE_GENERATOR}",
        "mode=python-ast-baseline",
        "",
        f"MODULE @L1-{max(1, len(text.splitlines()))}",
    ]
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lines.append(f"IMPORT @L{node.lineno} {expr(node)}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = ",".join(target_text(target) for target in targets)
            value = node.value
            annotation = f":{expr(node.annotation)}" if isinstance(node, ast.AnnAssign) else ""
            lines.append(
                f"C @L{node.lineno} {names}{annotation}={expr(value, secret_hint=bool(SECRET_NAME.search(names)))}"
            )
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines.append("")
            lines.extend(function_card(node, node.name))
        elif isinstance(node, ast.ClassDef):
            bases = ",".join(expr(base, 100) for base in node.bases)
            lines.extend(["", f"CLASS {node.name}({bases}) @L{node.lineno}-{node.end_lineno or node.lineno}"])
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    lines.append("")
                    lines.extend(function_card(member, f"{node.name}.{member.name}"))
    lines.extend(
        [
            "",
            "DECLARED_OMISSIONS / REQUIRED PAGE-FAULTS",
            "  Comments, formatting, most docstring prose, and exact statement bodies are omitted.",
            "  Long strings are represented by length and digest; likely credentials are redacted.",
            "  Fault exact source before edits, exact literal claims, security, concurrency, math, regex, or exception matching.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


GENERIC_KEEP = re.compile(
    r"^\s*(?:import\b|from\b|export\b|package\b|use\b|class\b|interface\b|type\b|enum\b|"
    r"(?:async\s+)?(?:def|function|fn|func)\b|(?:public|private|protected|static|final|abstract)\b|"
    r"if\b|else\b|for\b|while\b|switch\b|case\b|try\b|catch\b|except\b|finally\b|"
    r"return\b|throw\b|raise\b|defer\b|await\b)"
)


def redact_line(line: str) -> str:
    if SECRET_VALUE.search(line) or (SECRET_NAME.search(line) and re.search(r"[:=]", line)):
        digest = sha256_bytes(line.encode("utf-8", "replace"))[:12]
        left = re.split(r"[:=]", line, maxsplit=1)[0]
        return f"{left}=<REDACTED_LINE sha256={digest}>"
    if len(line) > 300:
        digest = sha256_bytes(line.encode("utf-8", "replace"))[:12]
        return line[:260] + f"…<sha256={digest}>"
    return line


def generic_ir(text: str, relative: str, digest: str, parse_note: str = "generic language fallback") -> str:
    kept = []
    for number, line in enumerate(text.splitlines(), 1):
        if GENERIC_KEEP.search(line):
            kept.append(f"{number}|{redact_line(line.strip())}")
        if len(kept) >= 1200:
            break
    lines = [
        CODEC_VERSION.upper(),
        f"source_rel={relative}",
        f"source_sha256={digest}",
        f"generator={BASELINE_GENERATOR}",
        "mode=generic-line-baseline",
        f"QA {parse_note}",
        "",
        *kept,
        "",
        "DECLARED_OMISSIONS / REQUIRED PAGE-FAULTS",
        "  Generic fallback preserves only structural/control-looking lines and is not semantic authority.",
        "  Fault exact source for all behavior claims and before every edit.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def baseline_for(source: Path, relative: str, digest: str) -> str:
    text = source.read_text(encoding="utf-8", errors="replace")
    return python_ir(text, relative, digest) if source.suffix.lower() == ".py" else generic_ir(text, relative, digest)


def ensure_file(root: Path, paths: dict[str, Path], relative: str, source: Path) -> dict[str, Any]:
    digest, record = refresh_one(root, paths, relative, source)
    artifacts = artifact_paths(paths, relative)
    usable = (
        record.get("status") in {"ready", "baseline_ready"}
        and record.get("ir_source_sha256") == digest
        and artifacts["ir"].is_file()
    )
    if not usable:
        ir = baseline_for(source, relative, digest)
        if sha256_file(source) != digest:
            raise RuntimeError("Source changed while baseline IR was generated; retry ensure")
        atomic_write(artifacts["ir"], ir.encode("utf-8"))
        with CacheLock(paths["lock"]):
            manifest = load_manifest(paths["manifest"], root)
            current = manifest["files"].get(relative, {})
            if current.get("source_sha256") != digest or sha256_file(source) != digest:
                raise RuntimeError("Source changed before baseline IR commit; retry ensure")
            current.update(
                {
                    "status": "baseline_ready",
                    "ir_source_sha256": digest,
                    "ir_sha256": sha256_file(artifacts["ir"]),
                    "ir_generator": BASELINE_GENERATOR,
                    "image_status": "stale",
                    "ir_rel": artifacts["ir"].relative_to(root).as_posix(),
                    "images_rel": artifacts["images"].relative_to(root).as_posix(),
                    "ir_updated_at": now_iso(),
                }
            )
            manifest["files"][relative] = current
            manifest["updated_at"] = now_iso()
            atomic_json(paths["manifest"], manifest)
            record = current
    return {
        "ok": True,
        "operation": "ensure",
        "source_rel": relative,
        "source_sha256": digest,
        "status": record.get("status"),
        "ir": str(artifacts["ir"]),
        "images": str(artifacts["images"]),
        "needs_enrichment": record.get("status") != "ready",
        "generator": record.get("ir_generator"),
    }


def prepare_file(root: Path, paths: dict[str, Path], relative: str, source: Path) -> dict[str, Any]:
    ensured = ensure_file(root, paths, relative, source)
    digest = ensured["source_sha256"]
    job_id = uuid.uuid4().hex
    staging = paths["staging"] / Path(relative + f".{digest[:12]}.{job_id}.kern-il.txt")
    staging.parent.mkdir(parents=True, exist_ok=True)
    job = {
        "schema": "kern-job/0.1",
        "job_id": job_id,
        "created_at": now_iso(),
        "source": str(source),
        "source_rel": relative,
        "source_sha256": digest,
        "baseline_ir": ensured["ir"],
        "staging_ir": str(staging),
        "worker_contract": str(Path(__file__).resolve().parent.parent / "references" / "compiler-worker.md"),
        "status": "prepared",
    }
    artifact = artifact_paths(paths, relative)
    atomic_json(artifact["job"], job)
    return {"ok": True, "operation": "prepare", **job}


def commit_file(
    root: Path,
    paths: dict[str, Path],
    relative: str,
    source: Path,
    ir_file: Path,
    expected_sha: str,
) -> dict[str, Any]:
    if not ir_file.is_file():
        raise ValueError(f"Staging IR does not exist: {ir_file}")
    current_sha = sha256_file(source)
    if current_sha != expected_sha:
        raise RuntimeError(f"Stale compiler result: expected {expected_sha}, current source is {current_sha}")
    payload = ir_file.read_bytes()
    if not payload.strip():
        raise ValueError("Staging IR is empty")
    text = payload.decode("utf-8", "strict")
    if not text.splitlines() or text.splitlines()[0].strip() != CODEC_VERSION.upper():
        raise ValueError(f"Staging IR must start with {CODEC_VERSION.upper()}")
    if f"source_sha256={expected_sha}" not in text:
        raise ValueError("Staging IR does not declare the expected source_sha256")
    if f"source_rel={relative}" not in text:
        raise ValueError("Staging IR does not declare the expected source_rel")
    artifacts = artifact_paths(paths, relative)
    atomic_write(artifacts["ir"], payload)
    if sha256_file(source) != expected_sha:
        raise RuntimeError("Source changed during KERN IL commit; rerun prepare")
    with CacheLock(paths["lock"]):
        manifest = load_manifest(paths["manifest"], root)
        record = manifest["files"].get(relative, {})
        if record.get("source_sha256") != expected_sha:
            raise RuntimeError("Manifest changed during KERN IL commit; rerun prepare")
        record.update(
            {
                "status": "ready",
                "ir_source_sha256": expected_sha,
                "ir_sha256": sha256_bytes(payload),
                "ir_generator": "model-enrichment",
                "image_status": "stale",
                "ir_rel": artifacts["ir"].relative_to(root).as_posix(),
                "images_rel": artifacts["images"].relative_to(root).as_posix(),
                "ir_updated_at": now_iso(),
            }
        )
        manifest["files"][relative] = record
        manifest["updated_at"] = now_iso()
        atomic_json(paths["manifest"], manifest)
    job = artifacts["job"]
    if job.is_file():
        try:
            data = json.loads(job.read_text(encoding="utf-8"))
            data.update({"status": "committed", "committed_at": now_iso(), "ir_sha256": sha256_bytes(payload)})
            atomic_json(job, data)
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "ok": True,
        "operation": "commit",
        "source_rel": relative,
        "source_sha256": expected_sha,
        "status": "ready",
        "ir": str(artifacts["ir"]),
        "ir_sha256": sha256_bytes(payload),
    }


def render_file(
    root: Path,
    paths: dict[str, Path],
    config: dict[str, Any],
    relative: str,
    source: Path,
    profile: str | None,
) -> dict[str, Any]:
    digest, record = refresh_one(root, paths, relative, source)
    artifacts = artifact_paths(paths, relative)
    if (
        record.get("status") not in {"ready", "baseline_ready"}
        or record.get("ir_source_sha256") != digest
        or not artifacts["ir"].is_file()
    ):
        raise RuntimeError("IR is missing or stale; run ensure and, when available, KERN commit first")
    selected = profile or str(config.get("image_profile", "dense"))
    renderer = Path(__file__).resolve().with_name("render_ir.py")
    result = subprocess.run(
        [
            sys.executable,
            str(renderer),
            "--input",
            str(artifacts["ir"]),
            "--output",
            str(artifacts["images"]),
            "--profile",
            selected,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "IR renderer failed")
    metrics = json.loads(result.stdout)
    with CacheLock(paths["lock"]):
        manifest = load_manifest(paths["manifest"], root)
        current = manifest["files"].get(relative, {})
        if current.get("source_sha256") != digest:
            raise RuntimeError("Source changed while IR images were rendered")
        current.update(
            {
                "image_source_sha256": digest,
                "image_ir_sha256": sha256_file(artifacts["ir"]),
                "image_profile": selected,
                "image_status": "ready",
                "image_metrics_rel": (artifacts["images"] / "metrics.json").relative_to(root).as_posix(),
                "images_updated_at": now_iso(),
            }
        )
        manifest["files"][relative] = current
        manifest["updated_at"] = now_iso()
        atomic_json(paths["manifest"], manifest)
    return {"ok": True, "operation": "render", "source_rel": relative, **metrics}


def status(root: Path, paths: dict[str, Path]) -> dict[str, Any]:
    manifest = load_manifest(paths["manifest"], root)
    counts: dict[str, int] = {}
    pending = []
    for relative, record in manifest["files"].items():
        state = record.get("status", "unknown")
        counts[state] = counts.get(state, 0) + 1
        if state in {"missing", "stale", "baseline_ready"}:
            pending.append(relative)
    return {
        "ok": True,
        "operation": "status",
        "repo": str(root),
        "manifest": str(paths["manifest"]),
        "counts": counts,
        "pending_enrichment_or_refresh": sorted(pending),
    }


def sync_cache(
    root: Path,
    paths: dict[str, Path],
    config: dict[str, Any],
    eager: bool,
    limit: int | None,
) -> dict[str, Any]:
    scan_result = scan(root, paths, config)
    ensured: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    if eager:
        manifest = load_manifest(paths["manifest"], root)
        pending = [
            relative
            for relative, record in sorted(manifest["files"].items())
            if record.get("status") in {"missing", "stale"}
        ]
        if limit is not None:
            pending = pending[:limit]
        for relative in pending:
            source = root / relative
            try:
                result = ensure_file(root, paths, relative, source)
                ensured.append(
                    {
                        "source_rel": relative,
                        "source_sha256": result["source_sha256"],
                        "status": result["status"],
                    }
                )
            except Exception as exc:
                errors.append({"source_rel": relative, "error": str(exc)})
    return {
        "ok": not errors,
        "operation": "sync",
        "mode": "eager-baseline" if eager else "lazy",
        "scan": scan_result,
        "ensured": ensured,
        "errors": errors,
    }


def paths_for(root: Path, paths: dict[str, Path], relative: str, source: Path) -> dict[str, Any]:
    digest, record = refresh_one(root, paths, relative, source)
    artifacts = artifact_paths(paths, relative)
    return {
        "ok": True,
        "operation": "paths",
        "source": str(source),
        "source_rel": relative,
        "source_sha256": digest,
        "status": record.get("status"),
        "image_status": record.get("image_status", "missing"),
        "ir": str(artifacts["ir"]),
        "images": str(artifacts["images"]),
        "job": str(artifacts["job"]),
    }


def fault_source(source: Path, relative: str, start: int | None, end: int | None, expected: str | None) -> str:
    data = source.read_bytes()
    digest = sha256_bytes(data)
    if expected and expected != digest:
        raise RuntimeError(f"Source hash mismatch: expected {expected}, current {digest}")
    text = data.decode("utf-8", "strict")
    lines = text.splitlines(keepends=True)
    first = start or 1
    last = end or len(lines)
    if first < 1 or last < first or last > max(1, len(lines)):
        raise ValueError(f"Invalid line range {first}-{last}; file has {len(lines)} lines")
    body = "".join(lines[first - 1 : last])
    if body and not body.endswith("\n"):
        body += "\n"
    return (
        "--- KERN EXACT SOURCE FAULT ---\n"
        f"source_rel={relative}\nsource_sha256={digest}\nlines={first}-{last}\n"
        "--- SOURCE ---\n"
        + body
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Repository root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("scan")
    sub.add_parser("status")
    sync = sub.add_parser("sync")
    sync.add_argument("--eager", action="store_true", help="Generate deterministic IR for every stale/missing file")
    sync.add_argument("--limit", type=int, help="Maximum files to ensure in eager mode")
    for name in ("ensure", "prepare", "paths"):
        command = sub.add_parser(name)
        command.add_argument("file")
    commit = sub.add_parser("commit")
    commit.add_argument("file")
    commit.add_argument("--ir-file", required=True, type=Path)
    commit.add_argument("--source-sha", required=True)
    render = sub.add_parser("render")
    render.add_argument("file")
    render.add_argument("--profile", choices=("ultra", "dense", "balanced", "safe"))
    fault = sub.add_parser("fault")
    fault.add_argument("file")
    fault.add_argument("--start", type=int)
    fault.add_argument("--end", type=int)
    fault.add_argument("--expect-sha")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = repo_root(args.repo)
        paths, config = initialize(root)
        if args.command == "init":
            result = {"ok": True, "operation": "init", "repo": str(root), "cache": str(paths["cache"])}
        elif args.command == "scan":
            result = scan(root, paths, config)
        elif args.command == "status":
            result = status(root, paths)
        elif args.command == "sync":
            result = sync_cache(root, paths, config, args.eager, args.limit)
        else:
            relative, source = normalize_rel(root, args.file)
            if args.command == "ensure":
                result = ensure_file(root, paths, relative, source)
            elif args.command == "prepare":
                result = prepare_file(root, paths, relative, source)
            elif args.command == "paths":
                result = paths_for(root, paths, relative, source)
            elif args.command == "commit":
                result = commit_file(root, paths, relative, source, args.ir_file.expanduser().resolve(), args.source_sha)
            elif args.command == "render":
                result = render_file(root, paths, config, relative, source, args.profile)
            elif args.command == "fault":
                sys.stdout.write(fault_source(source, relative, args.start, args.end, args.expect_sha))
                return 0
            else:  # pragma: no cover
                raise RuntimeError(f"Unknown command: {args.command}")
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except Exception as exc:
        json.dump({"ok": False, "error": str(exc), "type": exc.__class__.__name__}, sys.stderr, indent=2)
        sys.stderr.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
