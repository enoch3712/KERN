#!/usr/bin/env python3
"""Render KERN-IL text as compact, lossless WebP image pages."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import textwrap
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, features
except ImportError:  # pragma: no cover - environment dependent
    Image = ImageDraw = ImageFont = features = None


@dataclass(frozen=True)
class Profile:
    name: str
    width: int
    height: int
    font_size: int
    columns: int
    margin: int
    gutter: int


PROFILES = {
    "ultra": Profile("ultra", 1600, 1600, 9, 5, 16, 12),
    "dense": Profile("dense", 1600, 1600, 10, 4, 18, 14),
    "balanced": Profile("balanced", 1600, 1600, 13, 3, 22, 18),
    "safe": Profile("safe", 1600, 1600, 16, 2, 26, 24),
}

FONT_CANDIDATES = (
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/cour.ttf",
)


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    created = False
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
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


def require_pillow() -> None:
    if Image is None or ImageDraw is None or ImageFont is None or features is None:
        raise SystemExit(
            "KERN image rendering requires Pillow. Keep using textual IR, or install "
            "Pillow in an approved environment with: python3 -m pip install Pillow"
        )


def load_font(size: int):
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Older Pillow.
        return ImageFont.load_default()


def line_color(text: str) -> tuple[int, int, int]:
    stripped = text.lstrip()
    if stripped.startswith(("KERN-IL", "MODULE", "CLASS", "F ")):
        return (9, 54, 115)
    if stripped.startswith(("QA ", "ERR ", "FAULT ")):
        return (145, 28, 28)
    if stripped.startswith(("C ", "IMPORT ", "EXPORT ")):
        return (70, 50, 115)
    if stripped.startswith(("DECLARED_OMISSIONS", "POLICY", "LOSS ")):
        return (120, 75, 0)
    return (20, 24, 31)


def wrap_lines(text: str, font, column_width: int) -> list[str]:
    sample = "MMMMMMMMMM"
    try:
        char_width = max(1.0, font.getlength(sample) / len(sample))
    except AttributeError:  # Older Pillow.
        bbox = font.getbbox(sample)
        char_width = max(1.0, (bbox[2] - bbox[0]) / len(sample))
    max_chars = max(20, int((column_width - 8) / char_width))
    result: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            result.append("")
            continue
        indent = len(line) - len(line.lstrip(" "))
        continuation = " " * min(indent + 2, max_chars // 3) + "· "
        result.extend(
            textwrap.wrap(
                line,
                width=max_chars,
                subsequent_indent=continuation,
                replace_whitespace=False,
                drop_whitespace=True,
                break_long_words=True,
                break_on_hyphens=False,
            )
            or [""]
        )
    return result


def assert_safe_output_path(output: Path, cache_root: Path) -> Path:
    """Return an absolute output path only when it stays in a symlink-free cache tree."""
    cache_abs = Path(os.path.abspath(cache_root))
    output_abs = Path(os.path.abspath(output))
    if cache_abs.name != ".kern":
        raise ValueError(f"Render cache root must be a .kern directory: {cache_root}")
    try:
        relative = output_abs.relative_to(cache_abs)
    except ValueError as exc:
        raise ValueError(f"Render output escapes cache root: {output}") from exc

    current = cache_abs
    if current.is_symlink():
        raise ValueError(f"Symlinked render cache path is not allowed: {current}")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Symlinked render output path is not allowed: {current}")

    resolved_cache = cache_abs.resolve(strict=False)
    try:
        output_abs.resolve(strict=False).relative_to(resolved_cache)
    except ValueError as exc:
        raise ValueError(f"Render output resolves outside cache root: {output}") from exc
    return output_abs


def safe_clear_output(output: Path, cache_root: Path) -> None:
    output = assert_safe_output_path(output, cache_root)
    output.mkdir(parents=True, exist_ok=True)
    assert_safe_output_path(output, cache_root)
    candidates: list[Path] = []
    for pattern in ("page-*.webp", "page-*.png"):
        candidates.extend(sorted(output.glob(pattern)))
    metrics = output / "metrics.json"
    if metrics.exists() or metrics.is_symlink():
        candidates.append(metrics)

    # Validate the complete deletion set before unlinking anything. A single
    # symlink or non-file entry leaves every existing artifact untouched.
    for path in candidates:
        assert_safe_output_path(path, cache_root)
        if not path.is_file():
            raise ValueError(f"Render cleanup candidate is not a regular file: {path}")
    for path in candidates:
        path.unlink()


def render(input_path: Path, output: Path, profile: Profile, cache_root: Path) -> dict:
    require_pillow()
    if not features.check("webp"):
        raise SystemExit(
            "This Pillow build lacks WebP support. Keep textual IR or install a Pillow build with WebP."
        )

    text = input_path.read_text(encoding="utf-8")
    font = load_font(profile.font_size)
    bbox = font.getbbox("Ag")
    line_height = max(profile.font_size + 2, bbox[3] - bbox[1] + 3)
    usable_width = profile.width - 2 * profile.margin - (profile.columns - 1) * profile.gutter
    column_width = usable_width // profile.columns
    rows_per_column = (profile.height - 2 * profile.margin) // line_height
    rows_per_page = rows_per_column * profile.columns
    visual_lines = wrap_lines(text, font, column_width)
    page_count = max(1, math.ceil(len(visual_lines) / rows_per_page))

    output = assert_safe_output_path(output, cache_root)
    safe_clear_output(output, cache_root)
    pages: list[dict] = []

    for page_index in range(page_count):
        canvas = Image.new("RGB", (profile.width, profile.height), "white")
        draw = ImageDraw.Draw(canvas)
        start = page_index * rows_per_page
        page_lines = visual_lines[start : start + rows_per_page]

        for local_index, line in enumerate(page_lines):
            column = local_index // rows_per_column
            row = local_index % rows_per_column
            x = profile.margin + column * (column_width + profile.gutter)
            y = profile.margin + row * line_height
            draw.text((x, y), line, font=font, fill=line_color(line))

        for column in range(1, profile.columns):
            x = (
                profile.margin
                + column * column_width
                + (column - 1) * profile.gutter
                + profile.gutter // 2
            )
            draw.line(
                (x, profile.margin, x, profile.height - profile.margin),
                fill=(225, 228, 234),
                width=1,
            )

        used_columns = max(1, math.ceil(len(page_lines) / rows_per_column))
        last_rows = len(page_lines) - (used_columns - 1) * rows_per_column
        used_width = (
            2 * profile.margin
            + used_columns * column_width
            + (used_columns - 1) * profile.gutter
        )
        used_height = (
            profile.height
            if used_columns > 1
            else 2 * profile.margin + max(1, last_rows) * line_height
        )
        used_width = min(profile.width, math.ceil(used_width / 32) * 32)
        used_height = min(profile.height, math.ceil(used_height / 32) * 32)
        image = canvas.crop((0, 0, used_width, used_height))

        filename = f"page-{page_index + 1:03d}-of-{page_count:03d}.webp"
        page_path = output / filename
        assert_safe_output_path(page_path, cache_root)
        temporary = page_path.with_name(
            f".{page_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        created = False
        try:
            fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            with os.fdopen(fd, "w+b") as handle:
                image.save(handle, format="WEBP", lossless=True, method=6)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, page_path)
            created = False
        finally:
            if created:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
        patches = math.ceil(image.width / 32) * math.ceil(image.height / 32)
        pages.append(
            {
                "page": page_index + 1,
                "path": str(page_path),
                "width": image.width,
                "height": image.height,
                "bytes": page_path.stat().st_size,
                "patch_tokens_estimate": patches,
            }
        )

    metrics = {
        "schema": "kern-render/0.1",
        "input": str(input_path),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "input_characters": len(text),
        "profile": asdict(profile),
        "source_lines": len(text.splitlines()),
        "visual_lines": len(visual_lines),
        "pages": pages,
        "page_count": len(pages),
        "bytes_total": sum(page["bytes"] for page in pages),
        "patch_tokens_estimate_total": sum(page["patch_tokens_estimate"] for page in pages),
    }
    metrics_path = output / "metrics.json"
    assert_safe_output_path(metrics_path, cache_root)
    atomic_json(metrics_path, metrics)
    return metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="KERN-IL text file")
    parser.add_argument("--output", required=True, type=Path, help="Page output directory")
    parser.add_argument(
        "--cache-root",
        required=True,
        type=Path,
        help="Containing .kern directory used for output containment checks",
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), default="dense")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.input.is_file():
        raise SystemExit(f"IR input does not exist: {args.input}")
    metrics = render(args.input.resolve(), args.output, PROFILES[args.profile], args.cache_root)
    json.dump(metrics, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
