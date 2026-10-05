---
title: Scanning bound books into raw/
description: Fleet-synced guide - physical setup, vFlat, chapter batching, page-number locator rule, ebook screen capture. Managed by sync_from_template.py; never edit locally.
auto_generated: true
---

# Scanning bound books into raw/ (synced from the WIKIllm template)

> [!warning] This file is FLEET-SYNCED (`scripts/sync_from_template.py`, regression R28).
> Local edits are overwritten by the next `--apply`; improve it in the template, via PR.
> Physical books: the sections below. Ebooks already owned in a desktop reader: the
> "Owned ebooks" section (`scripts/screen_capture.ps1`). DRM-free epubs need no capture
> at all: `scripts/extract_chapter.py`.

## Physical setup (does most of the work)

- **Shoot one page at a time, not the spread.** Open the book ~100 degrees, hold the
  shooting page flat, let the other side curl. Most gutter distortion comes from forcing
  a flat 180-degree spread.
- **A sheet of picture-frame glass** pressed over the page kills the curve entirely --
  the cheap trick that beats any software dewarp.
- **Camera parallel to the page** (perspective skew is the other half of the problem),
  even side light, no phone shadow.

## App

Use **vFlat Scan** (iOS/Android) instead of the built-in scanner: built for bound
books -- automatic gutter/curve dewarping, auto-capture as pages turn, two-page-spread
splitting, hundreds of pages per document, and an **embedded OCR text layer** in the
exported PDF. Adobe Scan / Microsoft Lens are fallbacks; their dewarp is tuned for flat
documents, like the iOS built-in.

## The three pipeline rules (these matter more than scan aesthetics)

1. **Batch by chapter, not by book.** One PDF per chapter sidesteps every page limit,
   matches `check_raw.py --accept` per-file semantics and a per-chapter ingest ledger
   (see `_meta/fleet-conventions.md`), and means a bad scan only costs one chapter's
   redo.
2. **Keep the printed page number in frame on every shot.** Scans are the vault's
   `p.NNN` locator artifact -- a scan whose page numbers are cropped off cannot anchor
   claims. (Real cost on record: one vault's ch.16 ingest is missing pp. 260-278, a
   ledgered gap.)
3. **The OCR layer is worth having.** The "index a large PDF by keyword" workflow only
   works when there is text to grep -- OCR makes scans searchable the way an epub is,
   while the page images stay authoritative for verification. A scan WITHOUT a text
   layer is still usable via the contact-sheet render pass (ingest skill, Lessons).

## Owned ebooks: screen capture beats photographing paper

When the book is already owned in a desktop reader (Example Reader or similar), skip the
camera entirely: `scripts/screen_capture.ps1` captures the reader window page by page into
`page_NNN.png` frames plus a `run-manifest.json`. Run it chapter-at-a-time into a fresh
folder; capture the TOC and copyright pages for edition provenance first; and inspect a
middle frame for clipped right-margin text before trusting a long run. Current flags
and capture guards are in the script's own header via
`python scripts/help.py screen_capture`.

`completed: true` is not a completeness certificate: it records only that
`frames_written` equals the user-supplied `pages_requested` value (`-Pages`). Before
ingest, verify the captured footer counters form the expected `1/y` through `y/y`
sequence, inspect the first and last frames, and trim any overshoot by footer text. The
reader can leave the chapter or book while still producing distinct frames. A maximized
reader on a 4K display fits a whole book in roughly 60 screens (~5-7 per chapter).
Maximized, and NOT minimized: a minimized reader aborts the run by name (its geometry is
the taskbar button) and the script will not restore it for you (regression R102). Keep
the reader footer (`N CHAPTER -- x/y`, e.g. `3 THE GOOD NEWS -- 4/5`) inside every
captured frame -- the same framing rule as pipeline rule 2 above. Cite by screen file +
footer (`page_010.png, ch.2 screen 3/5`): the `x/y` is a screen counter that shifts
with window and font size, so never mint a print-style `p.NNN` from it (the
twin-artifact rule in `_meta/fleet-conventions.md` forbids interpolated page numbers).

## At scale

A CZUR-style overhead book scanner does laser dewarping in hardware if a vault turns
into a book-scanning operation; for a few chapters per book, phone + glass + vFlat is
the right cost.
