#!/usr/bin/env python3
"""Content-addressed lazy/JIT cache for KERN-IL code pages."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import importlib.metadata
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
CODEC_VERSION = "kern-il/0.2"
BASELINE_GENERATOR = "kern-det/0.2"
DERIVATION_MARKER = "kern-derivation/semantic16-v1"
TSJS_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
ADDRESSABLE_SYMBOL_KINDS = {"function", "class", "component", "type", "enum", "namespace", "module", "export"}
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
    "min_ir_tokens": 600,
    "default_tier": "L2",
}

SECRET_VALUE = re.compile(
    r"(?i)(?:sk|rk|pk|s2)[_-][A-Za-z0-9_-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:aws|ghp|github_pat)_[A-Za-z0-9_-]{12,}"
)
SECRET_NAME = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|auth|bearer|credential|passwd|password|private[_-]?key|secret|token)"
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|auth|bearer|credential|passwd|password|private[_-]?key|secret|token)"
    r"['\"]?\s*[:=]\s*\S"
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


def decode_source_bytes(data: bytes, relative: str) -> str:
    """Decode source without normalizing or replacing any source bytes."""
    try:
        return data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Source is not valid UTF-8: {relative} (byte offset {exc.start}); "
            "fault the exact bytes with a byte-safe tool"
        ) from None


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    created = False
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        created = False
    finally:
        if created:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


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
        "log": cache / "log.jsonl",
    }


def assert_safe_cache_path(root: Path, path: Path) -> Path:
    """Reject cache paths that escape ``root`` or traverse a symlink.

    Repositories are input, so a pre-existing ``.kern`` tree is not trusted.
    Following one of its symlinks could write cache data outside the repository;
    render cleanup could also delete matching page files there.
    """
    root_abs = Path(os.path.abspath(root))
    cache_abs = root_abs / CACHE_DIRNAME
    target_abs = Path(os.path.abspath(path))
    try:
        relative = target_abs.relative_to(cache_abs)
    except ValueError as exc:
        raise ValueError(f"Cache path escapes repository .kern directory: {path}") from exc

    current = cache_abs
    if current.is_symlink():
        raise ValueError(f"Symlinked cache path is not allowed: {current}")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Symlinked cache path is not allowed: {current}")

    resolved_root = root_abs.resolve()
    resolved_cache = cache_abs.resolve(strict=False)
    expected_cache = resolved_root / CACHE_DIRNAME
    if resolved_cache != expected_cache:
        raise ValueError(f"Cache directory resolves outside repository: {cache_abs}")
    try:
        target_abs.resolve(strict=False).relative_to(resolved_cache)
    except ValueError as exc:
        raise ValueError(f"Cache path resolves outside repository .kern directory: {path}") from exc
    return target_abs


def initialize(root: Path) -> tuple[dict[str, Path], dict[str, Any]]:
    paths = cache_paths(root)
    for path in paths.values():
        assert_safe_cache_path(root, path)
    for key in ("cache", "ir", "images", "jobs", "staging"):
        paths[key].mkdir(parents=True, exist_ok=True)
        assert_safe_cache_path(root, paths[key])
        try:
            paths[key].chmod(0o700)
        except OSError:
            pass
    gitignore = paths["cache"] / ".gitignore"
    assert_safe_cache_path(root, gitignore)
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
                "derivation_marker": DERIVATION_MARKER,
                "repo_root": str(root),
                "updated_at": now_iso(),
                "files": {},
            },
        )
    else:
        with CacheLock(paths["lock"]):
            manifest = load_manifest(paths["manifest"], root)
            codec_changed = manifest.get("codec_version") != CODEC_VERSION
            derivation_changed = manifest.get("derivation_marker") != DERIVATION_MARKER
            legacy_records = [
                record
                for record in manifest["files"].values()
                if record.get("status") in {"ready", "baseline_ready"}
                and (
                    not record.get("ir_compiler_fingerprint")
                    or record.get("ir_derivation_marker") != DERIVATION_MARKER
                )
            ]
            if codec_changed or derivation_changed or legacy_records:
                invalidated = (
                    list(manifest["files"].values())
                    if codec_changed or derivation_changed
                    else legacy_records
                )
                for record in invalidated:
                    if record.get("status") not in {"deleted", "missing"}:
                        record["status"] = "stale"
                    record["image_status"] = "stale"
                manifest["codec_version"] = CODEC_VERSION
                manifest["derivation_marker"] = DERIVATION_MARKER
                if codec_changed:
                    manifest["codec_invalidated_at"] = now_iso()
                if derivation_changed or legacy_records:
                    manifest["derivation_invalidated_at"] = now_iso()
                manifest["updated_at"] = now_iso()
                atomic_json(paths["manifest"], manifest)
    return paths, config


def load_manifest(path: Path, root: Path) -> dict[str, Any]:
    assert_safe_cache_path(root, path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid KERN manifest {path}: {exc}") from exc
    if manifest.get("schema") != SCHEMA:
        raise RuntimeError(f"Unsupported manifest schema: {manifest.get('schema')!r}")
    if Path(manifest.get("repo_root", "")).resolve() != root.resolve():
        raise RuntimeError("Manifest repository root does not match --repo")
    files = manifest.setdefault("files", {})
    if not isinstance(files, dict):
        raise RuntimeError("Manifest files must be a JSON object")
    for relative, record in files.items():
        if not isinstance(relative, str) or not isinstance(record, dict):
            raise RuntimeError("Manifest contains an invalid file record")
        try:
            artifact_paths(cache_paths(root), relative)
        except ValueError as exc:
            raise RuntimeError(f"Manifest contains an unsafe source path: {relative!r}") from exc
    return manifest


def normalize_rel(root: Path, value: str, require_file: bool = True) -> tuple[str, Path]:
    candidate = Path(value).expanduser()
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        relative = path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Path escapes repository root: {path}") from exc
    if CACHE_DIRNAME in relative.parts:
        raise ValueError("Source path may not point inside .kern")
    if require_file and not path.is_file():
        raise ValueError(f"Source file does not exist: {path}")
    return relative.as_posix(), path


def artifact_paths(paths: dict[str, Path], relative: str) -> dict[str, Path]:
    rel_path = Path(relative)
    if (
        not relative
        or rel_path.is_absolute()
        or rel_path.as_posix() != relative
        or CACHE_DIRNAME in rel_path.parts
        or any(part in {"", ".", ".."} for part in rel_path.parts)
    ):
        raise ValueError(f"Invalid cache artifact source path: {relative!r}")
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
    for artifact in artifact_paths(paths, relative).values():
        assert_safe_cache_path(root, artifact)
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


GENERIC_KEEP = re.compile(
    r"^\s*(?:import\b|from\b|export\b|package\b|use\b|class\b|interface\b|type\b|enum\b|"
    r"(?:async\s+)?(?:def|function|fn|func)\b|(?:public|private|protected|static|final|abstract)\b|"
    r"if\b|else\b|for\b|while\b|switch\b|case\b|try\b|catch\b|except\b|finally\b|"
    r"return\b|throw\b|raise\b|defer\b|await\b)"
)


def redact_line(line: str) -> str:
    line = SECRET_VALUE.sub(lambda m: f"<REDACTED len={len(m.group(0))}>", line)
    if SECRET_NAME.search(line) and re.search(r"[:=]", line):
        digest = sha256_bytes(line.encode("utf-8", "replace"))[:12]
        left = re.split(r"[:=]", line, maxsplit=1)[0]
        return f"{left}=<REDACTED_LINE sha256={digest}>"
    if len(line) > 300:
        digest = sha256_bytes(line.encode("utf-8", "replace"))[:12]
        return line[:260] + f"…<sha256={digest}>"
    return line


def generic_ir(text: str, relative: str, digest: str, parse_note: str = "generic language fallback") -> str:
    kept = []
    # Match fault_source's \n-only line numbering (see comment there): text.splitlines()
    # also splits on \v, \f, \x1c-\x1e, \x85, U+2028, U+2029, etc., which would make the
    # "N|" refs point at the wrong source line for files containing those characters.
    for number, line in enumerate(text.split("\n"), 1):
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


def stub_ir(text: str, relative: str, digest: str) -> str:
    lines = [
        CODEC_VERSION.upper(),
        f"source_rel={relative}",
        f"source_sha256={digest}",
        f"generator={BASELINE_GENERATOR}",
        "mode=source-cheaper",
        f"QA source is ~{max(1, len(text) // 4)} tokens ({len(text.splitlines())} lines), below the IL floor; fault exact source.",
    ]
    return "\n".join(lines) + "\n"


def resolve_tier(config: dict[str, Any], tier: str | None) -> str:
    selected = tier or str(config.get("default_tier", "L2"))
    if selected not in {"L1", "L2", "L3"}:
        raise ValueError(f"Unsupported deterministic IL tier: {selected}")
    return selected


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "absent"
    except Exception:
        return "unknown"


def _module_source_fingerprint(module_name: str, fallback: Path | None = None) -> str:
    """Return a module source digest, or a deterministic availability marker.

    Optional compiler adapters are still fingerprint inputs when they cannot be
    imported: if their source is present, hash the source directly; otherwise
    record a stable marker instead of making cache validation fail.
    """
    path = fallback
    try:
        module = importlib.import_module(module_name)
        module_file = getattr(module, "__file__", None)
        if module_file:
            path = Path(module_file)
    except Exception:
        pass
    if path is None:
        return "unavailable"
    try:
        return sha256_file(path)
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError):
        return "unavailable"


def compiler_fingerprint(
    source: Path,
    config: dict[str, Any],
    selected_tier: str,
    mode: str,
) -> str:
    """Hash every effective input that can change deterministic IL bytes."""
    import kern_compile

    compiler_path = Path(kern_compile.__file__ or "")
    compiler_sha = sha256_file(compiler_path) if compiler_path.is_file() else "unknown"
    suffix = source.suffix.lower()
    capabilities: dict[str, Any] = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
    if suffix in TSJS_SUFFIXES:
        try:
            react_fallback = compiler_path.with_name("kern_react.py")
        except ValueError:
            react_fallback = None
        capabilities.update(
            {
                "tree-sitter": _distribution_version("tree-sitter"),
                "tree-sitter-javascript": _distribution_version("tree-sitter-javascript"),
                "tree-sitter-typescript": _distribution_version("tree-sitter-typescript"),
            }
        )
        try:
            capabilities["tsjs"] = kern_compile.tsjs_capability_fingerprint()
        except Exception:
            capabilities["tsjs"] = "unavailable"
        capabilities["kern-react-sha256"] = _module_source_fingerprint(
            "kern_react", react_fallback
        )
    payload = {
        "codec": CODEC_VERSION,
        "generator": BASELINE_GENERATOR,
        "derivation_marker": DERIVATION_MARKER,
        "compiler_sha256": compiler_sha,
        "source_suffix": suffix,
        "tier": selected_tier,
        "mode": mode,
        "min_ir_tokens": int(config.get("min_ir_tokens", 600)),
        "default_tier": str(config.get("default_tier", "L2")),
        "capabilities": capabilities,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def baseline_for(root: Path, source: Path, relative: str, digest: str,
                 config: dict[str, Any], tier: str | None = None) -> tuple[str, str, str]:
    """Return ``(il_text, resolved_tier, mode)`` for exact UTF-8 source bytes."""
    data = source.read_bytes()
    if sha256_bytes(data) != digest:
        raise RuntimeError("Source changed before baseline IR generation; retry ensure")
    text = decode_source_bytes(data, relative)
    selected = resolve_tier(config, tier)
    if max(1, len(text) // 4) < int(config.get("min_ir_tokens", 600)):
        return stub_ir(text, relative, digest), selected, "source-cheaper"
    note = "generic language fallback"
    try:
        import kern_compile
        suffix = source.suffix.lower()
        module = None
        if suffix == ".py":
            module = kern_compile.parse_python(text)
        elif suffix in TSJS_SUFFIXES:
            is_tsx = suffix == ".tsx"
            is_typescript = suffix in {".ts", ".tsx"}
            if kern_compile.tsjs_available(typescript=is_typescript, tsx=is_tsx):
                module = kern_compile.parse_tsjs(text, typescript=is_typescript, tsx=is_tsx)
        if module is not None:
            if module.parse_error:
                note = f"parse failed: {module.parse_error}"
            else:
                apply_handles = getattr(kern_compile, "apply_semantic_handles", None)
                if callable(apply_handles):
                    module = apply_handles(module)
                return (
                    kern_compile.emit_il(module, relative, digest, "none", selected),
                    selected,
                    f"structured:{module.frontend}",
                )
    except Exception as exc:
        note = f"deterministic compiler failed: {redact_line(str(exc))}"
    return generic_ir(text, relative, digest, note), selected, "generic-line-baseline"


def _record_usable(
    record: dict[str, Any],
    digest: str,
    ir_path: Path,
    selected_tier: str,
    expected_fingerprint: str,
) -> bool:
    if (
        record.get("status") not in {"ready", "baseline_ready"}
        or record.get("ir_source_sha256") != digest
        or record.get("ir_tier") != selected_tier
        or record.get("ir_derivation_marker") != DERIVATION_MARKER
        or record.get("ir_compiler_fingerprint") != expected_fingerprint
        or not ir_path.is_file()
    ):
        return False
    try:
        return record.get("ir_sha256") == sha256_file(ir_path)
    except OSError:
        return False


def _record_derivation_current(
    record: dict[str, Any], source: Path, config: dict[str, Any]
) -> bool:
    tier = record.get("ir_tier")
    mode = record.get("ir_mode")
    if (
        tier not in {"L1", "L2", "L3"}
        or not isinstance(mode, str)
        or not mode
        or record.get("ir_derivation_marker") != DERIVATION_MARKER
        or not record.get("ir_compiler_fingerprint")
    ):
        return False
    try:
        expected = compiler_fingerprint(source, config, tier, mode)
    except Exception:
        return False
    return record.get("ir_compiler_fingerprint") == expected


def ensure_file(root: Path, paths: dict[str, Path], relative: str, source: Path,
                config: dict[str, Any], tier: str | None = None) -> dict[str, Any]:
    selected = resolve_tier(config, tier)
    artifacts = artifact_paths(paths, relative)
    for artifact in artifacts.values():
        assert_safe_cache_path(root, artifact)
    digest, _ = refresh_one(root, paths, relative, source)
    cache_hit = False
    with CacheLock(paths["lock"]):
        manifest = load_manifest(paths["manifest"], root)
        record = manifest["files"].get(relative, {})
        mode = str(record.get("ir_mode", ""))
        expected_fingerprint = compiler_fingerprint(source, config, selected, mode)
        usable = (
            sha256_file(source) == digest
            and _record_usable(record, digest, artifacts["ir"], selected, expected_fingerprint)
        )
        cache_hit = usable
    if not usable:
        ir, tier_used, mode = baseline_for(root, source, relative, digest, config, tier)
        payload = ir.encode("utf-8")
        payload_sha = sha256_bytes(payload)
        expected_fingerprint = compiler_fingerprint(source, config, tier_used, mode)
        if sha256_file(source) != digest:
            raise RuntimeError("Source changed while baseline IR was generated; retry ensure")
        with CacheLock(paths["lock"]):
            manifest = load_manifest(paths["manifest"], root)
            current = manifest["files"].get(relative, {})
            if current.get("source_sha256") != digest or sha256_file(source) != digest:
                raise RuntimeError("Source changed before baseline IR commit; retry ensure")
            current_mode = str(current.get("ir_mode", ""))
            current_fingerprint = compiler_fingerprint(source, config, selected, current_mode)
            if _record_usable(
                current, digest, artifacts["ir"], selected, current_fingerprint
            ):
                cache_hit = True
            else:
                atomic_write(artifacts["ir"], payload)
                after_sha = sha256_file(source)
                if after_sha != digest:
                    current.update(
                        {
                            "source_sha256": after_sha,
                            "status": "stale",
                            "image_status": "stale",
                            "changed_at": now_iso(),
                        }
                    )
                    manifest["files"][relative] = current
                    manifest["updated_at"] = now_iso()
                    atomic_json(paths["manifest"], manifest)
                    raise RuntimeError("Source changed during baseline IR commit; retry ensure")
                current.update(
                    {
                        "status": "baseline_ready",
                        "ir_source_sha256": digest,
                        "ir_sha256": payload_sha,
                        "ir_generator": BASELINE_GENERATOR,
                        "ir_codec_version": CODEC_VERSION,
                        "ir_derivation_marker": DERIVATION_MARKER,
                        "ir_tier": tier_used,
                        "ir_mode": mode,
                        "ir_compiler_fingerprint": expected_fingerprint,
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
        "tier": record.get("ir_tier"),
        "mode": record.get("ir_mode"),
        "cache_hit": cache_hit,
    }


def prepare_file(root: Path, paths: dict[str, Path], relative: str, source: Path,
                 config: dict[str, Any]) -> dict[str, Any]:
    ensured = ensure_file(root, paths, relative, source, config)
    digest = ensured["source_sha256"]
    job_id = uuid.uuid4().hex
    staging = paths["staging"] / Path(relative + f".{digest[:12]}.{job_id}.kern-il.txt")
    assert_safe_cache_path(root, staging)
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
    assert_safe_cache_path(root, artifact["job"])
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
    lines = text.splitlines()
    if not lines or lines[0].strip() != CODEC_VERSION.upper():
        raise ValueError(f"Staging IR must start with {CODEC_VERSION.upper()}")
    headers: dict[str, str] = {}
    for line in lines[1:8]:
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"source_rel", "source_sha256", "generator"} and key not in headers:
            headers[key] = value
    if headers.get("source_sha256") != expected_sha:
        raise ValueError("Staging IR does not declare the expected source_sha256")
    if headers.get("source_rel") != relative:
        raise ValueError("Staging IR does not declare the expected source_rel")
    if not headers.get("generator"):
        raise ValueError("Staging IR does not declare a generator")
    artifacts = artifact_paths(paths, relative)
    assert_safe_cache_path(root, artifacts["ir"])
    assert_safe_cache_path(root, artifacts["job"])
    with CacheLock(paths["lock"]):
        baseline_path = artifacts["ir"]
        if not baseline_path.is_file():
            raise ValueError("No deterministic baseline IL exists; run ensure before commit")
        baseline_text = baseline_path.read_text(encoding="utf-8")
        if text == baseline_text:
            appended = ""
        elif text.startswith(baseline_text):
            appended = text[len(baseline_text):].strip("\n")
        else:
            raise ValueError("Enrichment must preserve the deterministic IL verbatim as a prefix")
        if appended:
            appended_lines = appended.splitlines()
            if not appended_lines[0].startswith("ENRICHMENT model="):
                raise ValueError("Appended section must start with 'ENRICHMENT model=<name>'")
            for line in appended_lines:
                if SECRET_VALUE.search(line) or SECRET_ASSIGNMENT.search(line):
                    raise ValueError("Enrichment line contains a likely credential")
            for line in appended_lines[1:]:
                if line.strip() and not line.startswith("INTENT "):
                    raise ValueError(
                        f"Enrichment may only append INTENT lines, found: {redact_line(line)[:60]!r}"
                    )
        payload = payload.rstrip(b"\n") + b"\n"
        atomic_write(artifacts["ir"], payload)
        if sha256_file(source) != expected_sha:
            raise RuntimeError("Source changed during KERN IL commit; rerun prepare")
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


def _mark_render_failed(
    root: Path,
    paths: dict[str, Path],
    relative: str,
    render_id: str,
    error: str,
) -> None:
    """Best-effort transition out of ``rendering`` without masking the cause."""
    try:
        with CacheLock(paths["lock"]):
            manifest = load_manifest(paths["manifest"], root)
            current = manifest["files"].get(relative, {})
            if current.get("image_render_id") != render_id:
                return
            current["image_status"] = "stale"
            current["image_error"] = redact_line(error)
            current.pop("image_render_id", None)
            manifest["files"][relative] = current
            manifest["updated_at"] = now_iso()
            atomic_json(paths["manifest"], manifest)
    except Exception:
        pass


def _validate_render_artifacts(
    metrics: Any,
    artifacts: dict[str, Path],
    pinned_ir_sha: str,
    selected_profile: str,
) -> None:
    if not isinstance(metrics, dict):
        raise RuntimeError("IR renderer metrics must be a JSON object")
    if metrics.get("schema") != "kern-render/0.1":
        raise RuntimeError("IR renderer metrics have an unsupported schema")
    if metrics.get("input_sha256") != pinned_ir_sha:
        raise RuntimeError("IR renderer metrics do not match the pinned IR digest")
    profile = metrics.get("profile")
    if not isinstance(profile, dict) or profile.get("name") != selected_profile:
        raise RuntimeError("IR renderer metrics do not match the requested profile")
    pages = metrics.get("pages")
    page_count = metrics.get("page_count")
    if (
        not isinstance(pages, list)
        or not pages
        or not isinstance(page_count, int)
        or isinstance(page_count, bool)
        or page_count != len(pages)
    ):
        raise RuntimeError("IR renderer metrics contain an invalid page list")

    metrics_path = artifacts["images"] / "metrics.json"
    try:
        stored_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("IR renderer did not create a valid metrics.json artifact") from exc
    if stored_metrics != metrics:
        raise RuntimeError("IR renderer stdout does not match metrics.json")

    image_root = artifacts["images"].resolve()
    total_bytes = 0
    seen: set[Path] = set()
    for index, page in enumerate(pages, 1):
        if not isinstance(page, dict) or page.get("page") != index:
            raise RuntimeError("IR renderer metrics contain an invalid page record")
        raw_path = page.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise RuntimeError("IR renderer metrics contain an invalid page path")
        page_path = Path(raw_path).resolve()
        if page_path.parent != image_root or page_path in seen or page_path.suffix.lower() != ".webp":
            raise RuntimeError("IR renderer page path is outside the expected output set")
        seen.add(page_path)
        if not page_path.is_file():
            raise RuntimeError("IR renderer did not create every declared page artifact")
        actual_bytes = page_path.stat().st_size
        declared_bytes = page.get("bytes")
        if (
            actual_bytes <= 0
            or not isinstance(declared_bytes, int)
            or isinstance(declared_bytes, bool)
            or declared_bytes != actual_bytes
        ):
            raise RuntimeError("IR renderer page artifact size does not match metrics")
        total_bytes += actual_bytes
    if metrics.get("bytes_total") != total_bytes:
        raise RuntimeError("IR renderer total byte count does not match page artifacts")


def render_file(
    root: Path,
    paths: dict[str, Path],
    config: dict[str, Any],
    relative: str,
    source: Path,
    profile: str | None,
) -> dict[str, Any]:
    artifacts = artifact_paths(paths, relative)
    assert_safe_cache_path(root, artifacts["ir"])
    assert_safe_cache_path(root, artifacts["images"])
    artifacts["images"].mkdir(parents=True, exist_ok=True)
    assert_safe_cache_path(root, artifacts["images"])
    render_lock = artifacts["images"] / ".render.lock"
    assert_safe_cache_path(root, render_lock)
    with CacheLock(render_lock, timeout=300.0, stale_after=900.0):
        return _render_file_locked(root, paths, config, relative, source, profile)


def _render_file_locked(
    root: Path,
    paths: dict[str, Path],
    config: dict[str, Any],
    relative: str,
    source: Path,
    profile: str | None,
) -> dict[str, Any]:
    digest, _ = refresh_one(root, paths, relative, source)
    artifacts = artifact_paths(paths, relative)
    render_id = uuid.uuid4().hex
    with CacheLock(paths["lock"]):
        manifest = load_manifest(paths["manifest"], root)
        record = manifest["files"].get(relative, {})
        if (
            record.get("status") not in {"ready", "baseline_ready"}
            or record.get("source_sha256") != digest
            or record.get("ir_source_sha256") != digest
            or sha256_file(source) != digest
            or not artifacts["ir"].is_file()
            or record.get("ir_sha256") != sha256_file(artifacts["ir"])
            or not _record_derivation_current(record, source, config)
        ):
            raise RuntimeError(
                "IR or its compiler derivation is missing or stale; run ensure and, "
                "when available, KERN commit first"
            )
        pinned_ir_sha = str(record["ir_sha256"])
        record["image_status"] = "rendering"
        record["image_render_id"] = render_id
        record.pop("image_error", None)
        manifest["files"][relative] = record
        manifest["updated_at"] = now_iso()
        atomic_json(paths["manifest"], manifest)
    selected = profile or str(config.get("image_profile", "dense"))
    renderer = Path(__file__).resolve().with_name("render_ir.py")
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(renderer),
                "--input",
                str(artifacts["ir"]),
                "--output",
                str(artifacts["images"]),
                "--cache-root",
                str(paths["cache"]),
                "--profile",
                selected,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "IR renderer failed"
            raise RuntimeError(redact_line(message))
        try:
            metrics = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("IR renderer produced invalid metrics JSON") from exc
        _validate_render_artifacts(metrics, artifacts, pinned_ir_sha, selected)
    except Exception as exc:
        _mark_render_failed(root, paths, relative, render_id, str(exc))
        raise
    try:
        with CacheLock(paths["lock"]):
            manifest = load_manifest(paths["manifest"], root)
            current = manifest["files"].get(relative, {})
            source_matches = current.get("source_sha256") == digest and sha256_file(source) == digest
            ir_matches = (
                current.get("ir_source_sha256") == digest
                and current.get("ir_sha256") == pinned_ir_sha
                and artifacts["ir"].is_file()
                and sha256_file(artifacts["ir"]) == pinned_ir_sha
            )
            owns_render = current.get("image_render_id") == render_id
            if not source_matches or not ir_matches or not owns_render:
                current["image_status"] = "stale"
                current["image_error"] = "source changed" if not source_matches else "IR changed"
                current.pop("image_render_id", None)
                manifest["files"][relative] = current
                manifest["updated_at"] = now_iso()
                atomic_json(paths["manifest"], manifest)
                if not source_matches:
                    raise RuntimeError("Source changed while IR images were rendered")
                raise RuntimeError("IR changed while IR images were rendered")
            current.update(
                {
                    "image_source_sha256": digest,
                    "image_ir_sha256": pinned_ir_sha,
                    "image_profile": selected,
                    "image_status": "ready",
                    "image_metrics_rel": (artifacts["images"] / "metrics.json").relative_to(root).as_posix(),
                    "images_updated_at": now_iso(),
                }
            )
            current.pop("image_render_id", None)
            current.pop("image_error", None)
            manifest["files"][relative] = current
            manifest["updated_at"] = now_iso()
            atomic_json(paths["manifest"], manifest)
    except Exception as exc:
        _mark_render_failed(root, paths, relative, render_id, str(exc))
        raise
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
                result = ensure_file(root, paths, relative, source, config)
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
    artifacts = artifact_paths(paths, relative)
    for artifact in artifacts.values():
        assert_safe_cache_path(root, artifact)
    digest, record = refresh_one(root, paths, relative, source)
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
    text = decode_source_bytes(data, relative)
    # Split on "\n" only, matching ast/tree-sitter line numbering; str.splitlines()
    # also breaks on \v, \f, \x1c-\x1e, \x85, U+2028, U+2029, etc., which would
    # silently return the wrong bytes for a requested line range.
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    first = start or 1
    last = end or len(lines)
    if first < 1 or last < first or last > max(1, len(lines)):
        raise ValueError(f"Invalid line range {first}-{last}; file has {len(lines)} lines")
    body = "\n".join(lines[first - 1 : last])
    # "\n".join only inserts separators *between* the selected lines, so the
    # final selected line still needs its own terminator appended to match
    # the source bytes exactly -- unless that line is truly the file's last
    # line and the file itself has no trailing newline, in which case there
    # is no "\n" to reproduce.
    ends_at_file_end = last >= len(lines)
    if not (ends_at_file_end and not text.endswith("\n")):
        body += "\n"
    return (
        "--- KERN EXACT SOURCE FAULT ---\n"
        f"source_rel={relative}\nsource_sha256={digest}\nlines={first}-{last}\n"
        "--- SOURCE ---\n"
        + body
    )


def verify_symbol(root: Path, paths: dict[str, Path], relative: str, source: Path,
                  symbol: str, expected_hash: str, expected_span: str | None = None) -> dict[str, Any]:
    import kern_compile
    data = source.read_bytes()
    text = decode_source_bytes(data, relative)
    suffix = source.suffix.lower()
    if suffix == ".py":
        module = kern_compile.parse_python(text)
    elif suffix in TSJS_SUFFIXES:
        is_tsx = suffix == ".tsx"
        is_typescript = suffix in {".ts", ".tsx"}
        if not kern_compile.tsjs_available(typescript=is_typescript, tsx=is_tsx):
            raise ValueError(
                f"verify parser is unavailable for {suffix}; use fault with --expect-sha"
            )
        module = kern_compile.parse_tsjs(text, typescript=is_typescript, tsx=is_tsx)
    else:
        raise ValueError(f"verify does not support {suffix or 'this file type'}; use fault with --expect-sha")
    if module.parse_error:
        raise RuntimeError(f"current source does not parse ({module.parse_error}); fault exact source")
    apply_handles = getattr(kern_compile, "apply_semantic_handles", None)
    semantic_handles = callable(apply_handles)
    if semantic_handles:
        module = apply_handles(module)

    def handle_of(sym) -> str:
        if semantic_handles:
            handle = getattr(sym, "semantic8", "")
            if not handle:
                raise RuntimeError("deterministic compiler did not produce a semantic source handle")
            return handle
        return sym.slice8

    base = {"operation": "verify", "source_rel": relative, "symbol": symbol,
            "source_sha256": sha256_bytes(data),
            "verification_basis": "module-semantic-handle" if semantic_handles else "symbol-slice-hash"}

    def response(result: str, **fields: Any) -> dict[str, Any]:
        return {**base, "ok": result in {"ok", "moved"}, "result": result, **fields}

    matches = [
        s for s in module.symbols
        if s.kind in ADDRESSABLE_SYMBOL_KINDS and s.name == symbol
    ]
    if not matches:
        return response("stale", reason="symbol-not-found")

    def span_of(sym) -> str:
        return f"L{sym.span[0]}-{sym.span[1]}"

    hash_hit = next((m for m in matches if handle_of(m) == expected_hash), None)
    if hash_hit is not None:
        current_span = span_of(hash_hit)
        if expected_span is None or expected_span == current_span:
            return response("ok", current_span=current_span)
        return response("moved", current_span=current_span)

    span_hit = None
    if expected_span:
        span_hit = next((m for m in matches if span_of(m) == expected_span), None)
    if span_hit is not None:
        return response(
            "stale",
            reason="source-handle-changed",
            current_hash=handle_of(span_hit),
            current_span=span_of(span_hit),
        )

    found = matches[0]
    return response(
        "stale",
        reason="source-handle-changed",
        current_hash=handle_of(found),
        current_span=span_of(found),
        candidates=[{"span": span_of(m), "hash": handle_of(m)} for m in matches],
    )


def log_event(paths: dict[str, Path], entry: dict[str, Any]) -> None:
    """Append one JSON line to the operation log. Never raises."""
    try:
        assert_safe_cache_path(paths["cache"].parent, paths["log"])
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(paths["log"], flags, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except (OSError, TypeError, ValueError):
        pass


def log_fields_from_result(result: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in ("source_rel", "status", "result", "reason", "counts"):
        if key in result:
            fields[key] = result[key]
    return fields


def ensure_log_fields(
    paths: dict[str, Path], relative: str, source: Path, result: dict[str, Any]
) -> dict[str, Any]:
    """Extra telemetry for the ensure command: tier and cheap token estimates."""
    fields: dict[str, Any] = {}
    try:
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        record = manifest.get("files", {}).get(relative, {})
        tier = record.get("ir_tier")
        if tier is not None:
            fields["tier"] = tier
    except (OSError, json.JSONDecodeError):
        pass
    try:
        fields["source_tokens"] = source.stat().st_size // 4
    except OSError:
        pass
    try:
        artifacts = artifact_paths(paths, relative)
        fields["il_tokens"] = artifacts["ir"].stat().st_size // 4
    except OSError:
        pass
    return fields


def read_log_entries(paths: dict[str, Path], tail: int, op_filter: str | None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        assert_safe_cache_path(paths["cache"].parent, paths["log"])
        with paths["log"].open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if op_filter and entry.get("op") != op_filter:
                    continue
                entries.append(entry)
    except (OSError, ValueError):
        return []
    if tail is not None and tail >= 0:
        entries = entries[-tail:] if tail > 0 else []
    return entries


def print_log(entries: list[dict[str, Any]], as_json: bool) -> None:
    if not entries:
        print("no log entries")
        return
    if as_json:
        for entry in entries:
            print(json.dumps(entry, sort_keys=True))
        return
    print(f"{'TS':<21} {'OP':<8} {'FILE':<32} {'STATUS/RESULT':<14} {'MS':>8}")
    for entry in entries:
        ts = str(entry.get("ts", "-"))
        op = str(entry.get("op", "-"))
        file_ = str(entry.get("source_rel", "-"))
        status = entry.get("status") or entry.get("result") or "-"
        duration = entry.get("duration_ms")
        ms = str(duration) if isinstance(duration, int) else "-"
        print(f"{ts:<21} {op:<8} {file_:<32} {str(status):<14} {ms:>8}")


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
    ensure = sub.add_parser("ensure")
    ensure.add_argument("file")
    ensure.add_argument("--tier", choices=("L1", "L2", "L3"))
    prepare = sub.add_parser("prepare")
    prepare.add_argument("file")
    paths_cmd = sub.add_parser("paths")
    paths_cmd.add_argument("file")
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
    verify = sub.add_parser("verify")
    verify.add_argument("file")
    verify.add_argument("--symbol", required=True)
    verify.add_argument("--hash", required=True)
    verify.add_argument("--span")
    log_cmd = sub.add_parser("log")
    log_cmd.add_argument("--tail", type=int, default=20)
    log_cmd.add_argument("--op")
    log_cmd.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths: dict[str, Path] | None = None
    started = time.monotonic()
    try:
        root = repo_root(args.repo)
        paths, config = initialize(root)
        relative: str | None = None
        source: Path | None = None
        if args.command == "init":
            result = {"ok": True, "operation": "init", "repo": str(root), "cache": str(paths["cache"])}
        elif args.command == "scan":
            result = scan(root, paths, config)
        elif args.command == "status":
            result = status(root, paths)
        elif args.command == "sync":
            result = sync_cache(root, paths, config, args.eager, args.limit)
        elif args.command == "log":
            entries = read_log_entries(paths, args.tail, args.op)
            print_log(entries, args.json)
            return 0
        else:
            relative, source = normalize_rel(root, args.file)
            if args.command == "ensure":
                result = ensure_file(root, paths, relative, source, config, tier=getattr(args, "tier", None))
            elif args.command == "prepare":
                result = prepare_file(root, paths, relative, source, config)
            elif args.command == "paths":
                result = paths_for(root, paths, relative, source)
            elif args.command == "commit":
                result = commit_file(root, paths, relative, source, args.ir_file.expanduser().resolve(), args.source_sha)
            elif args.command == "render":
                result = render_file(root, paths, config, relative, source, args.profile)
            elif args.command == "fault":
                sys.stdout.write(fault_source(source, relative, args.start, args.end, args.expect_sha))
                log_event(
                    paths,
                    {
                        "ts": now_iso(),
                        "op": "fault",
                        "source_rel": relative,
                        "start": args.start,
                        "end": args.end,
                        "ok": True,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                )
                return 0
            elif args.command == "verify":
                result = verify_symbol(root, paths, relative, source, args.symbol, args.hash, args.span)
            else:  # pragma: no cover
                raise RuntimeError(f"Unknown command: {args.command}")
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        operation_ok = bool(result.get("ok", True))
        entry = {
            "ts": now_iso(),
            "op": args.command,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "ok": operation_ok,
        }
        entry.update(log_fields_from_result(result))
        if args.command == "ensure" and relative is not None and source is not None:
            entry.update(ensure_log_fields(paths, relative, source, result))
        log_event(paths, entry)
        return 0 if operation_ok else 1
    except Exception as exc:
        if paths is not None:
            error_entry: dict[str, Any] = {
                "ts": now_iso(),
                "op": getattr(args, "command", "?"),
                "ok": False,
                "error": redact_line(str(exc)),
            }
            source_rel = getattr(args, "file", None)
            if source_rel is not None:
                error_entry["source_rel"] = source_rel
            log_event(paths, error_entry)
        json.dump({"ok": False, "error": str(exc), "type": exc.__class__.__name__}, sys.stderr, indent=2)
        sys.stderr.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
