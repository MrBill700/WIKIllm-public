#!/usr/bin/env python3
"""Gather every available transcript for the /watch-derived sources pages.

Four references per video, because each answers a different question:

  manual   a HUMAN-WRITTEN caption track. The strongest witness. Run the
           script to learn how many of this vault's videos have one -- do not
           assume, and do not infer it from the filename (see below).
  auto     YouTube ASR, re-downloaded now. Fluent and confidently wrong about
           proper nouns, figures and technical terms.
  contemp  the transcript already embedded in the staged report.md -- the exact
           text the page author was looking at on the day. No fresh download
           reconstructs it, and it is what separates "the author misread the
           source" from "the source has changed since".
  whisper  local faster-whisper large-v3, run fresh. An INDEPENDENT INSTRUMENT,
           not a ground truth: measured 2026-08-10 it wrote "microchondria" for
           "mitochondria" where a manual track had it right.

Whisper is skipped by default on videos that HAVE a manual track -- on those the
human track is the reference, so a Whisper run only adds a worse witness. A long
corpus (an hour-plus video, or many of them) makes `--whisper` a GPU-hours job:
budget it and run it detached. It earns its cost where the claims at risk are
tickers, figures and proper nouns -- exactly the class an ASR track mangles.

Tracks are classified by CONTENT, never by filename: both tracks arrive as
`video.en*.vtt`, and `en-orig` is not the auto marker it looks like.

Dependencies, all optional and all LOUD when absent (regression R75):
  yt-dlp on PATH      caption + audio download. Absent -> contemp only.
  the /watch skill    an OPTIONAL, separately installed third-party Claude Code
                      skill, NOT bundled with this template; expected at
                      ~/.claude/skills/watch/scripts. It provides caption_kind
                      (a same-rule fallback is built in) and whisper_local.py
                      (--whisper). Absent -> the built-in caption rule is used
                      and the Whisper pass is skipped with a WARNING.
The contemp reference needs neither -- it is read out of report.md.

Output goes to `.transcripts/` at the vault root by default (never raw/: these
are derived files; a dot-folder so Obsidian and the lint/stale scanners, which
walk wiki/ and _meta/, never see the adjudication packets as wiki pages).
Downloaded audio is deleted once its whisper.json lands.

    python scripts/gather_transcripts.py                # subtitles + contemp only
    python scripts/gather_transcripts.py --whisper      # also transcribe locally
    python scripts/gather_transcripts.py --out DIR      # somewhere other than .transcripts/

Companion: `verify_quotes.py`, which reads what this writes.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL = Path.home() / ".claude" / "skills" / "watch" / "scripts"
# Stable per-vault default shared with verify_quotes.py. Relative to the vault.
DEFAULT_REFS = ".transcripts"

TS = re.compile(r"^\s*\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*")


def default_vault():
    return Path(__file__).resolve().parents[1]


def caption_kind(path):
    """auto or manual, judged on content. Imported from the watch skill when
    available so the two never drift; the fallback is the same rule."""
    try:
        if str(SKILL) not in sys.path:
            sys.path.insert(0, str(SKILL))
        from download import caption_kind as skill_kind  # noqa: E402
        return skill_kind(path)
    except Exception:
        try:
            head = Path(path).read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            return "auto"
        return ("auto" if "align:start position:" in head or "<c>" in head
                else "manual")


def url_for(text):
    """The `source:` URL from report.md's header, or None."""
    for line in text.splitlines()[:40]:
        if line.startswith("source:"):
            return line.split(":", 1)[1].strip().strip('"') or None
    return None


def contemporaneous(text):
    """The transcript section of the staged report, timestamps stripped."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().lower().startswith("## transcript"):
            start = i + 1
            break
    if start is None:
        return ""
    body = []
    for ln in lines[start:]:
        if ln.startswith("## "):
            break
        if ln.strip().startswith("<!--"):
            continue
        body.append(TS.sub("", ln))
    return "\n".join(body).strip()


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=None,
                    help="vault root (default: the directory above scripts/)")
    ap.add_argument("--out", type=Path, default=None,
                    help="working directory (default: <vault>/%s/)" % DEFAULT_REFS)
    ap.add_argument("--whisper", action="store_true",
                    help="also run local faster-whisper on the auto-only videos")
    ap.add_argument("--whisper-all", action="store_true",
                    help="run Whisper even where a manual track exists")
    ap.add_argument("--no-download", action="store_true",
                    help="never call yt-dlp; write the contemp reference only")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args(argv)
    try:  # titles / warnings may not fit a cp1252 console
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    vault = (args.root or default_vault()).resolve()
    watched = vault / "raw" / "watched"
    out = args.out or (vault / DEFAULT_REFS)

    if not watched.exists():
        print("no raw/watched/ in this vault -- nothing to gather")
        return 0
    out.mkdir(parents=True, exist_ok=True)

    ytdlp = None if args.no_download else shutil.which("yt-dlp")
    if not args.no_download and ytdlp is None:
        print("WARNING: yt-dlp not found on PATH -- caption tracks NOT "
              "downloaded; only the contemp reference (report.md) is written",
              flush=True)
    wl = SKILL / "whisper_local.py"
    if (args.whisper or args.whisper_all) and not wl.exists():
        print("WARNING: --whisper needs %s (the /watch skill) -- it is absent; "
              "the Whisper pass is SKIPPED" % wl, flush=True)

    slugs = sorted(p.name for p in watched.iterdir() if p.is_dir())
    summary = {}

    for slug in slugs:
        rep = watched / slug / "report.md"
        if not rep.exists():
            print("WARNING: %s has no report.md -- skipped" % slug, flush=True)
            continue
        d = out / slug
        d.mkdir(parents=True, exist_ok=True)
        text = rep.read_text(encoding="utf-8", errors="replace")
        url = url_for(text)

        contemp = contemporaneous(text)
        (d / "contemporaneous.txt").write_text(contemp, encoding="utf-8")

        if url is None:
            print("WARNING: no `source:` url in %s/report.md -- captions not "
                  "downloaded" % slug, flush=True)
        elif ytdlp and not list(d.glob("video*.vtt")):
            r = run([ytdlp, "--skip-download",
                     "--write-subs", "--write-auto-subs",
                     "--sub-langs", "en,en-US,en-GB,en-orig",
                     "--sub-format", "vtt", "--convert-subs", "vtt",
                     "-o", str(d / "video.%(ext)s"), url])
            if r.returncode != 0:
                print("WARNING: yt-dlp failed for %s (rc %d): %s"
                      % (slug, r.returncode,
                         (r.stderr or "").strip()[-300:]), flush=True)

        tracks = {v.name: caption_kind(v) for v in sorted(d.glob("video*.vtt"))}
        (d / "tracks.json").write_text(json.dumps(tracks, indent=2),
                                       encoding="utf-8")
        summary[slug] = {"url": url, "tracks": tracks,
                         "contemp_words": len(contemp.split())}
        if not args.summary:
            print("%-64s %-14s contemp=%d w"
                  % (slug[:64], ",".join(sorted(set(tracks.values())))
                     or "NO-TRACKS", len(contemp.split())), flush=True)

    (out / "phase1.json").write_text(json.dumps(summary, indent=2),
                                     encoding="utf-8")

    if (args.whisper or args.whisper_all) and wl.exists():
        todo = [(s, m["url"]) for s, m in sorted(summary.items())
                if m["url"] and (args.whisper_all
                                 or "manual" not in set(m["tracks"].values()))]
        print("\nwhispering %d of %d%s" % (
            len(todo), len(summary),
            "" if args.whisper_all else " (manual-track videos skipped)"),
            flush=True)
        for slug, url in todo:
            d = out / slug
            wj = d / "whisper.json"
            if wj.exists() and wj.stat().st_size > 200:
                print("  skip (already have) %s" % slug, flush=True)
                continue
            audio = next((p for p in d.glob("audio.*")), None)
            err = ""
            if audio is None and ytdlp:
                r = run([ytdlp, "-f", "bestaudio/best", "-x",
                         "--audio-format", "m4a",
                         "-o", str(d / "audio.%(ext)s"), url])
                err = (r.stderr or "").strip()[-300:] if r.returncode else ""
                audio = next((p for p in d.glob("audio.*")), None)
            if audio is None:
                print("  !! no audio for %s%s" % (slug, (": " + err) if err
                                                   else ""), flush=True)
                continue
            print("  transcribing %s" % slug, flush=True)
            r = run([sys.executable, str(wl), str(audio)])
            if r.returncode != 0:
                print("  !! whisper failed: %s" % r.stderr[-400:], flush=True)
                continue
            try:
                n = len(json.loads(r.stdout)["segments"])
            except (ValueError, KeyError, TypeError):
                # Never persist a non-JSON result: a >200-byte bad file would
                # be taken as "already have" on every retry.
                print("  !! whisper output for %s is not segment JSON -- not "
                      "saved" % slug, flush=True)
                continue
            wj.write_text(r.stdout, encoding="utf-8")
            # Audio is re-derivable and large; the refs dir usually lives in a
            # cloud-synced vault. whisper.json above is what skips a re-run.
            try:
                audio.unlink()
            except OSError:
                pass
            print("  ok %s -> %d segments" % (slug, n), flush=True)

    print("\nDONE -> %s" % out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
