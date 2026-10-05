#!/usr/bin/env python
"""Extract chapter text from an EPUB into Read-friendly chunked files.

The Read tool has a ~25K-token ceiling (rejects files larger than this
even with offset/limit). Most book chapters are 70-200KB of text =
17-50K tokens, which exceeds the limit. This script pre-chunks chapter
text into files small enough to read in one call.

Use:
    python scripts/extract_chapter.py EPUB --list
    python scripts/extract_chapter.py EPUB --chapter 7
    python scripts/extract_chapter.py EPUB --file "*Chapter_5*"
    python scripts/extract_chapter.py EPUB --chapter 7 --max-chars 80000
    python scripts/extract_chapter.py EPUB --chapter 7 --out-dir other/

Defaults:
    --max-chars 80000  (~20K tokens at 4 chars/token; safely under Read's 25K limit)
    --out-dir scripts/extracted/{epub-stem}/

Output files are named:
    {xhtml-stem}.txt              (if chapter fits in one chunk)
    {xhtml-stem}-part-1.txt, etc. (if chunked)

At the end, prints absolute paths ready to paste into Read calls.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import zipfile
from pathlib import Path

DEFAULT_MAX_CHARS = 80_000  # ~20K tokens, comfortable under Read's 25K limit


def list_xhtml(epub: Path) -> list[zipfile.ZipInfo]:
    """Return content xhtml files in the epub, in spine order if possible.

    Falls back to alphabetical filename order if no OPF spine is found.
    """
    with zipfile.ZipFile(epub) as z:
        infos = z.infolist()
        # Try to read OPF for spine order; fall back to alphabetical xhtml files
        opf_paths = [i.filename for i in infos if i.filename.endswith(".opf")]
        content_files: list[zipfile.ZipInfo] = []
        if opf_paths:
            try:
                opf_text = z.read(opf_paths[0]).decode("utf-8", errors="replace")
                spine_ids = re.findall(r'itemref\s+idref="([^"]+)"', opf_text)
                items = dict(re.findall(r'item\s+id="([^"]+)"\s+href="([^"]+)"', opf_text))
                opf_dir = str(Path(opf_paths[0]).parent).replace("\\", "/")
                opf_dir = "" if opf_dir in (".", "") else opf_dir + "/"
                for sid in spine_ids:
                    href = items.get(sid)
                    if not href:
                        continue
                    full = opf_dir + href
                    for info in infos:
                        if info.filename == full and full.lower().endswith((".xhtml", ".html")):
                            content_files.append(info)
                            break
            except Exception:
                content_files = []
        if not content_files:
            content_files = sorted(
                [i for i in infos if i.filename.lower().endswith((".xhtml", ".html"))],
                key=lambda i: i.filename,
            )
    return content_files


def extract_text(epub: Path, internal_path: str) -> str:
    """Read one xhtml/html entry, strip tags, return clean text."""
    with zipfile.ZipFile(epub) as z:
        raw = z.read(internal_path).decode("utf-8", errors="replace")

    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r"</(p|div|h[1-6]|li|tr|br)>", "\n", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", raw)
    text = html.unescape(text)
    # Collapse internal whitespace per line but preserve paragraph breaks
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.split("\n")]
    lines = [l for l in lines if l]
    return "\n\n".join(lines)


def chunk(text: str, max_chars: int) -> list[str]:
    """Split text into chunks at paragraph boundaries, each <= max_chars.

    Single paragraphs larger than max_chars are kept whole rather than
    sliced mid-sentence; the caller may need to raise max_chars in that case.
    """
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        candidate = (current + "\n\n" + para) if current else para
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = para
    if current:
        parts.append(current)
    return parts


def select_chapter(infos: list[zipfile.ZipInfo], chapter_num: int | None, file_pattern: str | None) -> zipfile.ZipInfo:
    """Pick one ZipInfo based on chapter number or filename glob."""
    if chapter_num is not None:
        # Match "Chapter N" (one vault's convention) or "cNN" / "c0NN" (a common
        # publisher epub filename convention). (?!\d) prevents N=1 from
        # matching "Chapter 10" or "c10". Left-anchor on _ or / or start to
        # avoid spuriously matching mid-word.
        pat = re.compile(rf"(?:^|[/_])(?:chapter[_ ]*|c0*){chapter_num}(?!\d)", re.IGNORECASE)
        matches = [i for i in infos if pat.search(i.filename)]
        if not matches:
            raise SystemExit(f"No xhtml file matches chapter {chapter_num}. Use --list to see options.")
        if len(matches) > 1:
            names = "\n  ".join(i.filename for i in matches)
            raise SystemExit(f"Multiple matches for chapter {chapter_num}:\n  {names}\nUse --file to disambiguate.")
        return matches[0]
    if file_pattern is not None:
        import fnmatch
        matches = [i for i in infos if fnmatch.fnmatch(Path(i.filename).name, file_pattern)]
        if not matches:
            raise SystemExit(f"No xhtml file matches pattern {file_pattern!r}. Use --list to see options.")
        if len(matches) > 1:
            names = "\n  ".join(i.filename for i in matches)
            raise SystemExit(f"Multiple matches for pattern {file_pattern!r}:\n  {names}")
        return matches[0]
    raise SystemExit("Must specify --chapter N, --file PATTERN, or --list.")


def main() -> int:
    # Windows default stdout encoding is cp1252, which can't encode → or ≤
    # used in our output. Force UTF-8 so extractions don't crash mid-print.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, Exception):
        pass

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("epub", type=Path, help="Path to the .epub file")
    parser.add_argument("--list", action="store_true", help="List xhtml entries in the epub and exit")
    parser.add_argument("--chapter", type=int, metavar="N", help="Extract chapter N (matches 'Chapter N' in filename)")
    parser.add_argument("--file", type=str, metavar="PATTERN", help="Extract entry whose basename matches glob")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, metavar="N",
                        help=f"Max chars per chunk file (default {DEFAULT_MAX_CHARS:,}; ~20K tokens)")
    parser.add_argument("--out-dir", type=Path, metavar="DIR",
                        help="Output directory (default: scripts/extracted/{epub-stem}/)")
    parser.add_argument("--preview", type=int, default=0, metavar="N",
                        help="Print first N chars of extracted text to stdout (debug aid)")
    args = parser.parse_args()

    if not args.epub.is_file():
        raise SystemExit(f"Not a file: {args.epub}")

    infos = list_xhtml(args.epub)

    if args.list:
        print(f"{args.epub.name} — {len(infos)} content entries (spine order if available):")
        for i, info in enumerate(infos):
            size_kb = info.file_size / 1024
            print(f"  [{i:3d}]  {size_kb:7.1f} KB  {info.filename}")
        return 0

    target = select_chapter(infos, args.chapter, args.file)
    text = extract_text(args.epub, target.filename)

    if args.preview > 0:
        print(text[:args.preview])
        print(f"\n... [{len(text):,} chars total]")
        return 0

    chunks = chunk(text, args.max_chars)

    out_dir = args.out_dir or Path("scripts/extracted") / args.epub.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(target.filename).stem
    written: list[Path] = []
    if len(chunks) == 1:
        path = out_dir / f"{stem}.txt"
        path.write_text(chunks[0], encoding="utf-8")
        written.append(path)
    else:
        for i, c in enumerate(chunks, 1):
            path = out_dir / f"{stem}-part-{i}.txt"
            path.write_text(c, encoding="utf-8")
            written.append(path)

    print(f"Source: {args.epub.name} → {target.filename}")
    print(f"Total: {len(text):,} chars → {len(chunks)} chunk(s) of ≤ {args.max_chars:,} chars each")
    print()
    print("Ready to Read:")
    for p in written:
        # Print absolute path with forward slashes for cross-shell paste-ability
        ap = p.resolve()
        size = ap.stat().st_size
        print(f"  {ap}  ({size:,} chars)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
