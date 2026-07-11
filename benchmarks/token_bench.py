#!/usr/bin/env python3
"""Token benchmark: source vs deterministic KERN-IL per tier, bucketed by size."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "kern" / "scripts"))
import kern_compile  # noqa: E402


FUNCTION_HEADER = re.compile(
    r"^(?P<async>ASYNC )?F (?P<name>[^(]+)\((?P<signature>.*)\) -> "
    r"(?P<returns>.*?) @L(?P<start>\d+)-(?P<end>\d+) "
    r"\^(?P<handle>[0-9a-f]+) ~(?P<tier>L[123])$"
)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def bucket(tokens: int) -> str:
    if tokens < 2_000:
        return "small(<2k)"
    if tokens < 10_000:
        return "medium(2k-10k)"
    return "large(>10k)"


def _function_cards(il: str) -> list[tuple[dict[str, str], str]]:
    """Parse emitted function cards without using the emitter implementation."""
    lines = il.splitlines()
    cards: list[tuple[dict[str, str], str]] = []
    for index, line in enumerate(lines):
        match = FUNCTION_HEADER.match(line)
        if match is None:
            continue
        body = []
        for following in lines[index + 1:]:
            if not following.startswith("  "):
                break
            body.append(following)
        cards.append((match.groupdict(), "\n".join(body)))
    return cards


def _contains_fact(text: str, fact: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(fact)}(?![\w.])", text) is not None


def apply_semantic_handles(module) -> None:
    """Populate semantic handles on compilers that expose the 0.2 helper."""
    apply_handles = getattr(kern_compile, "apply_semantic_handles", None)
    if apply_handles is not None:
        apply_handles(module)


def symbol_handle(symbol) -> str:
    return getattr(symbol, "semantic8", "") or symbol.slice8


def fidelity_missing(module, il: str, tier: str = "L2") -> list[str]:
    """Compare tier output with facts independently extracted from the source AST."""
    kern_compile.propagate(module)
    apply_semantic_handles(module)
    cards = _function_cards(il)
    missing: list[str] = []
    for symbol in module.symbols:
        if symbol.kind == "class":
            expected = (
                f"CLASS {symbol.name}({symbol.bases}) @L{symbol.span[0]}-{symbol.span[1]} "
                f"^{symbol_handle(symbol)}"
            )
            if expected not in il.splitlines():
                missing.append(f"{symbol.name}@L{symbol.span[0]}-{symbol.span[1]}:source-handle")
            continue
        if symbol.kind != "function":
            continue

        label = f"{symbol.name}@L{symbol.span[0]}-{symbol.span[1]}"
        candidates = [(header, body) for header, body in cards if header["name"] == symbol.name]
        if not candidates:
            missing.append(f"{label}:header")
            continue
        exact_span = [
            card for card in candidates
            if int(card[0]["start"]) == symbol.span[0] and int(card[0]["end"]) == symbol.span[1]
        ]
        header, body = (exact_span or candidates)[0]

        if bool(header["async"]) != bool(symbol.is_async):
            missing.append(f"{label}:async")
        if header["signature"] != symbol.signature:
            missing.append(f"{label}:signature")
        if header["returns"] != (symbol.returns or "Any"):
            missing.append(f"{label}:returns")
        if (
            int(header["start"]) != symbol.span[0]
            or int(header["end"]) != symbol.span[1]
            or header["handle"] != symbol_handle(symbol)
        ):
            missing.append(f"{label}:source-handle")
        if header["tier"] != tier:
            missing.append(f"{label}:tier")

        # Only CALLS records and executable flow lines are positive call
        # evidence. EFFECTS/RAISES provenance may mention a callee even when its
        # actual call fact has been dropped from the IL.
        call_evidence = "\n".join(
            line
            for line in body.splitlines()
            if line.startswith("  CALLS ") or line.startswith("    ")
        )
        for call in symbol.calls:
            if not _contains_fact(call_evidence, call):
                missing.append(f"{label}:call:{call}")

        effects = "\n".join(line for line in body.splitlines() if line.lstrip().startswith("EFFECTS "))
        for effect in symbol.effects:
            if not _contains_fact(effects, effect):
                missing.append(f"{label}:effect:{effect}")
        if symbol.unknown_calls and f"unknown-calls={symbol.unknown_calls}" not in effects:
            missing.append(f"{label}:unknown-calls")

        raises = "\n".join(line for line in body.splitlines() if line.lstrip().startswith("RAISES "))
        for exception in symbol.raises_all:
            if not _contains_fact(raises, exception):
                missing.append(f"{label}:raise:{exception}")

    return list(dict.fromkeys(missing))


def bench_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    module = kern_compile.parse_python(text)
    if module.parse_error:
        return {"file": str(path), "error": module.parse_error}
    kern_compile.propagate(module)
    apply_semantic_handles(module)
    source_tokens = estimate_tokens(text)
    row = {"file": str(path), "source_tokens": source_tokens,
           "bucket": bucket(source_tokens), "tiers": {}, "fidelity_missing": []}
    for tier in ("L1", "L2", "L3"):
        il = kern_compile.emit_il(module, path.name, "0" * 64, "none", tier)
        il_tokens = estimate_tokens(il)
        missing = fidelity_missing(module, il, tier)
        row["tiers"][tier] = {
            "tokens": il_tokens,
            "ratio": round(source_tokens / il_tokens, 2),
            "fidelity_missing": missing,
            "fidelity_ok": not missing,
        }
        if tier == "L2":
            # Retained for compatibility with the first kern-bench/0.2 result.
            row["fidelity_missing"] = missing
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, help="Write JSON report here as well as stdout")
    args = parser.parse_args()
    report = {"schema": "kern-bench/0.2", "estimator": "chars/4",
              "python": f"{sys.version_info.major}.{sys.version_info.minor}",
              "files": [bench_file(f) for f in args.files]}
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
