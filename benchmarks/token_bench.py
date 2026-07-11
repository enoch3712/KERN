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


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def bucket(tokens: int) -> str:
    if tokens < 2_000:
        return "small(<2k)"
    if tokens < 10_000:
        return "medium(2k-10k)"
    return "large(>10k)"


def fidelity_missing(module, il: str) -> list[str]:
    missing = []
    for symbol in module.symbols:
        if symbol.kind != "function":
            continue
        tail = symbol.name.split(".")[-1]
        pattern = rf"^(?:ASYNC )?F .*{re.escape(tail)}\("
        if re.search(pattern, il, re.MULTILINE) is None:
            missing.append(symbol.name)
    return missing


def bench_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    module = kern_compile.parse_python(text)
    if module.parse_error:
        return {"file": str(path), "error": module.parse_error}
    source_tokens = estimate_tokens(text)
    row = {"file": str(path), "source_tokens": source_tokens,
           "bucket": bucket(source_tokens), "tiers": {}, "fidelity_missing": []}
    for tier in ("L1", "L2", "L3"):
        il = kern_compile.emit_il(module, path.name, "0" * 64, "none", tier)
        il_tokens = estimate_tokens(il)
        row["tiers"][tier] = {"tokens": il_tokens, "ratio": round(source_tokens / il_tokens, 2)}
        if tier == "L2":
            row["fidelity_missing"] = fidelity_missing(module, il)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, help="Write JSON report here as well as stdout")
    args = parser.parse_args()
    report = {"schema": "kern-bench/0.2", "estimator": "chars/4",
              "files": [bench_file(f) for f in args.files]}
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
