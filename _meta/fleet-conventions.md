---
title: Fleet conventions
description: Fleet-synced shared conventions - close-out protocol, twin-artifact citations, partial-ingest ledger. Managed by sync_from_template.py; never edit locally.
auto_generated: true
---

# Fleet conventions (synced from the WIKIllm template)

> [!warning] This file is FLEET-SYNCED (`scripts/sync_from_template.py`, regression R27).
> Local edits are overwritten by the next `--apply`. Improve it in the template, via PR.
> Instance `CLAUDE.md` files point here instead of carrying their own copies, so a
> protocol fix lands everywhere in one sync instead of one hand edit per wiki.

## Session close-out protocol

Run this before ending **any ingest or question session** -- it is the forcing function
that keeps the wiki from drifting. (The failure mode it prevents: a decision gets made,
the page in front of you + `log.md` get updated, but the *downstream ripple* across other
pages is deferred to "a follow-up" that never comes -- so resolved questions read as open,
ingested sources as "pending," and `index.md` entries go stale.)

1. **Propagate in-session -- defer only into the ledger, never into prose.** A
   decision/answer/new fact usually touches more than the page in front of you. Before
   closing, Grep the changed subject across `wiki/` and update *every* page that
   referenced the old state: flip resolved `[!question]` / "needs-input" / "pending" /
   `[ ]` markers, refresh the **`index.md` catalog entry**, re-key dependent
   dates/figures. If a ripple genuinely can't be finished now, it goes to
   **`_meta/open-loops.md`** as a dated, specific line -- it may **not** live as "NOT yet
   done" prose in a `log.md` entry. When *closing* a ledger entry, anchor the Edit to the
   `-- trigger:` date, not the bolded title -- a title-anchored edit leaves the body and
   trigger attached to the `[x]` line, where no scanner will ever surface it again
   (failure mode + verification grep: the ingest skill's Lessons). **Closing a loop
   means flipping its marker to `[x]`** -- not only annotating the line.
   `check_stale.py` section A reads the marker and nothing else, so a dated
   CLOSED/RESOLVED note written under a `[ ]` or `[~]` line still reports as open, and still
   reports OVERDUE, at this and every later close-out. The closure note is evidence;
   the marker is the closure. (regression R74.)
2. **Bump `updated:`** on every page whose body changed.
3. **Write the `log.md` entry.**
4. **Run both scanners**; resolve or consciously defer everything they surface:
   - `python scripts/lint.py --summary` -- broken links / orphans / ghost links.
   - `python scripts/check_stale.py --summary` -- open-loops ledger (+ overdue),
     time-sensitive markers, invalid terminal `*(unverifiable:)*` labels,
     `central`-tagged pages gone stale, recurring items due, and any required
     instance-owned scaffold file the vault is missing.
   - Any nonzero count -> re-run that scanner without `--summary` for the detail. Never
     truncate-pipe them: an early-closed pipe can interrupt Python. Capture full output
     to a file when it is too long to read at once.
5. **One-line close summary** to the human -- what changed + what's deferred (with each
   deferral's trigger date).

**At session *start*:** read `_meta/open-loops.md` and clear anything due before opening
new work.

**The ledger is only for deferred *propagation*** -- work already decided that just needs
finishing. Open *questions* and *research gaps* belong on their own pages (e.g. an
`analysis/blind-spots.md`); mark those pages with `tracker_page: true` in their
frontmatter so `check_stale.py` reports their open markers as low-signal (frontmatter
beats editing the script -- the scripts stay identical across wikis and sync cleanly from
the template).

**Where `/wiki-maintenance` parks a question for a human** (regression R189): the vault's
*question tracker*, resolved by `scripts/maintenance_preflight.py` in this order --
`_meta/open-questions.md`, then `_meta/for-reviewer.md` (each only if it exists AND this
vault's `CLAUDE.md` names it), else exactly one page carrying `question_tracker: true` in
its frontmatter -- exactly the line `question_tracker: true`, unindented, value on the
same line (not `"true"`, not `yes`, not `question_tracker:true`). **Keep that page's
frontmatter simple:** one-line `key: value` lines, one-line `[a, b]` lists and `- item`
lists only -- no quoted values, `|`/`>` blocks or nested keys -- or preflight cannot prove
the flag and leaves the tracker unresolved (with a warning naming the page). `question_tracker: true` is an explicit vault-local fallback selector used
only when the legacy `_meta` tracker is absent; it does not override
`_meta/open-questions.md` or `_meta/for-reviewer.md`. To adopt it, add the line to the one page
where questions belong; flag nothing else. It is not `tracker_page: true` -- project pages
carry that too. Two flagged pages, any page preflight cannot read, any other top-level
`question_tracker:` key line, or any page whose frontmatter is not plain block YAML (flow
`{...}` / JSON style, YAML-invalid control characters, invalid UTF-8) leave the tracker
unresolved, and questions go to `_meta/open-loops.md` marked as needing a human. Prose
that merely quotes `question_tracker: true` inside a value (a description, say) is
harmless.

## Marker hygiene: temporary vs terminal epistemic states

`==...==` highlight markers (`==unverified==`, `==verify==`, `==reservation
pending==`, ...) are TEMPORARY by contract: each is a to-do some session is
expected to resolve, which is why `check_stale.py` section B surfaces lingering
ones at close-out (on the content pages it scans -- tracker and auto-generated
pages are bucketed or skipped, and `--summary` prints counts only, so absence
from a report is not proof of absence).

Some claims are **permanently unverifiable** -- the source was pulled or
paywalled, the recording is deleted, the provenance died with a defunct site.
Leaving `==unverified==` on those routes a forever-state into the
temporary-marker channel: section B nags it at every close-out in every vault
until operators learn to skim the one section where genuine drift lives, or
reach for `tracker_page:` stamps that hide the page's OTHER markers. (regression R46.)

**Terminal label:** downgrade such a claim to the literal inline label
`*(unverifiable: <reason>)*` where `<reason>` is one of a CLOSED vocabulary of
hyphenated slugs -- **`source-gone`**, **`paywalled`**, **`provenance-lost`** --
e.g. `*(unverifiable: source-gone)*`. Closed on purpose: `check_stale.py`
applies its marker regexes to every scanned line (fenced code, `~~`-struck
lines, and checked `[x]` lines are exempt), so a free-text reason ("figure
TBD", "awaiting review") would re-trigger the nag under a different marker
name -- the exact failure this section exists to end. Closed *and enforced*:
`check_stale.py` section B3 validates every label it finds, counts the valid
ones so the population stays visible, and reports anything outside the
vocabulary -- an invented slug, or a reason-less `*(unverifiable)*` -- as
INVALID with its `file:line`, in `--summary` too. It validates the label's
*shape* and never prose: a hit counts only if it is italic-wrapped as written
above, or carries a whitespace-free slug-shaped reason with the italics
forgotten -- an ordinary sentence using the word in brackets ("disputed
(unverifiable without the parish register)") is not a label. The convention
docs and `log.md` are exempt, `inline code` is stripped before matching, and a
`<placeholder>` reason is never a violation, so quoting the form costs
nothing. (regression R57.) The concrete story behind the
slug goes in the `log.md` downgrade entry, never inline. Italic, parenthesized,
deliberately NOT a scanner marker phrase, so it stops nagging while staying
visible beside the claim -- the same closed-vocabulary design as the
partial-ingest ledger's terminal states below.

**Downgrade rule.** A session may downgrade `==unverified==` to the terminal
label only when ALL of:

1. It names the concrete reason re-verification is impossible ("the cited
   video is deleted and no archive copy exists" -- never just "couldn't find
   it").
2. The cheap alternatives were attempted, this session or a prior logged one:
   an independent corroborating source, an archived copy, a /watch frame for
   claims that were on screen.
3. The downgrade is recorded in `wiki/log.md` as its own entry under the
   machine-findable heading
   `## [YYYY-MM-DD] meta | downgrade | <wiki-relative page path> | <slug>`
   -- e.g. `## [2026-09-27] meta | downgrade | concepts/dosing.md | source-gone`
   -- with the claim and the concrete reason in full prose below it (the log
   entry, not the inline label, is where the story lives). The path is
   forward-slashed, with or without the `wiki/` prefix and `.md` (a label on a
   `_meta/` page is named `_meta/<file>.md`); the slug is the one applied, and
   the heading ends at the slug -- prose goes below it, never after it. One
   entry per (page, slug) pair: two labels on one page with the same slug
   share an entry, a different slug needs its own. Enforced:
   `check_stale.py` section B3 cross-checks every valid label against these
   headings by page path + slug (never by line number, which shifts) and
   reports a label with no entry as `UNLOGGED` with its `file:line`, in
   `--summary` too (regression R99). Hygiene, not validity -- the label still
   counts as valid and the exit code is unchanged. A note that merely quotes
   this convention is not an entry: the `[YYYY-MM-DD]` placeholder date, a
   `<...>` field, or a heading inside a code fence never matches.

Never downgrade merely because a marker is old. The label does not upgrade the
claim's evidence tier: a machine-transcript quote keeps its *(machine
transcript)* label alongside the terminal one, and ANY load-bearing claim
(figure, dose, direct quote, name spelling) that is unverifiable-forever should
be softened in prose or dropped -- not kept load-bearing wearing the label.

The transcript tier rule later in this file points here for exactly this case:
once a downgrade is logged, the terminal label REPLACES `==unverified==` as the
keeps-it-visible mechanism. A later session enforcing the tier rule must not
re-add the marker over a logged downgrade -- the two rules compose; they do not
conflict.

## What belongs in `raw/` (and what an agent must never write there)

`raw/` is the provenance store, not a workspace. `_meta/raw-hashes.json` is its SHA-256
baseline, so anything unaccounted for is reported by `check_raw.py` as a pending ADDED
file **forever** -- accepting it launders a derived artifact into the provenance record,
and deleting it is a judgement call a later session has to make with no idea who wrote
it or why.

The boundary, in one line:

> raw/ holds only what a human, a capture script, or an ingest banking a source it
> recovered put there; an agent's derived render or scratch file goes to a temp dir.

"Derived" covers page renders, contact sheets, keyword maps, ad-hoc text extractions and
any other scratch file an agent makes while *reading* a source: all of it is reproducible
from the primary, so none of it belongs beside the primary. The banking clause is the
narrow exception and the test is reproducibility, not who typed the command: when an
ingest recovers a text the live web no longer serves (the three-probe Unpaywall / Wayback
/ PMC route in the ingest skill), the extraction is the only surviving copy, is NOT
reproducible from anything in the vault, and IS banked in `raw/` with its provenance
header. A render of a PDF that is sitting right there is not that case. Any prompt that
orders a render must name the temp dir at the instruction, not only in a "READ-ONLY"
preamble -- an agent told to edit nothing will still happily *create* a new file next to
the one it is reading.

The other half of the boundary is just as binding: **what a human or a capture script
banks in `raw/` belongs there by design** -- photographed page scans,
`scripts/screen_capture.ps1` screen captures, and a banked web/PDF extraction carrying its
provenance header. This is not a ban on writing to `raw/`; it is a rule about *who*
writes and *why*.

(regression R72, 2026-09-16: an audit subagent rendered page 314 of a scanned primary into
`raw/karvonen-1957/` beside the PDF. Nothing failed -- it surfaced days later in an
unrelated `check_raw.py` sweep as a 1 MB ADDED file no ingest would ever account for.)

## Citing a work held as more than one artifact (scan + digital twin)

The locator rule assumes one artifact per source. The moment a vault holds the *same
work* twice -- photographed page scans WITH print page numbers, plus an epub/PDF twin
that is searchable but has NO pagination -- the idioms are incompatible, and `p.NNN`
silently stops resolving for digital-derived claims. (regression R25; first hit: a book held
as five scanned chapters + a complete reflowed epub.)

**The form of the citation names the artifact.** State the mapping once on the artifact
page, not per claim:

| Citation form | Artifact | How to re-verify |
|---|---|---|
| `p.NNN` | the scan | render the page image |
| `ch.N, "Section Heading"` | the digital text | extract the chapter, then search |

Three rules:

1. Where both artifacts carry a load-bearing figure, **the scan's page number wins** --
   it is the artifact a human can point at.
2. **Never interpolate a page number** for digital-only content from a chapter's page
   range -- that is fabricated precision wearing the scan's idiom.
3. `ch.N + heading + verbatim quote` against a searchable artifact is a *stronger*
   locator than a page number against an image scan -- do not "upgrade" it to `p.NNN`.

A `sources/` page for a twinned work may list both artifacts in `source_file:`
(YAML list); say which artifact each section's locators resolve against.

## Partial ingest of one large file (the per-chapter ledger)

`check_raw.py --accept` is all-or-nothing per file, and that is correct for the normal
case. A single file too large to ingest at once (an 18-chapter epub, a full-year
statement export) forces a bad choice: accept it and the unread chapters go invisible,
or leave it pending and the report reads false forever. (regression R26.)

**Convention: accept the file, and keep a per-chapter ingest ledger on its artifact
page** -- one row per chapter/section:

| Chapter | State | Where it landed |
|---|---|---|
| ch.4 | ingested | a wikilink to the concept page that holds it |
| ch.9 | partial -- rest pending ingest | a wikilink to the anchored sources section |
| ch.12 | out of scope (deliberate skip) | -- |

**State vocabulary is scanner-load-bearing.** Every row that still needs work must
contain the literal phrase **`pending ingest`** -- that is a `check_stale.py` marker, so
accepted-but-unread chapters keep surfacing at every close-out even though the file has
left the `check_raw.py` queue. Nonterminal states: `pending ingest`,
`partial -- rest pending ingest`. Terminal states (deliberately NOT marker phrases, so
they stop nagging): `ingested`, `out of scope (deliberate skip)`,
`held via scans only`. Tag the artifact page `tracker_page: true` so its open rows
report in the low-signal tracker bucket rather than as content drift, and record the
acceptance in `log.md` as "accepted with ledger, N of M chapters ingested." The ledger,
not the pending report, is the record of what remains -- keep it honest at every ingest
that touches the work.

## Transcript-derived sources: provenance + evidence tiers

`/watch` is an optional third-party video-analysis skill, not bundled here; its labels are
listed so its reports can be normalized.

A `sources/` page built from a video/audio transcript (a `/watch` report, a podcast, a
lecture) must record WHAT KIND of transcript it stands on -- the kinds differ wildly in
reliability, and a page that hides the kind launders auto-caption guesses into
wiki-grade fact. (regression R19.)

**Frontmatter contract** for every transcript-derived `sources/` page:

```yaml
source_file: "raw/watched/<slug>/report.md"
transcript_kind: auto-captions | whisper-local | whisper-remote | manual-captions | official | human-verified
transcript_source: "captions (auto)"   # /watch only: copy its kind label verbatim
```

`source_file` names the exact transcript artifact used for ingest: a vault-relative path
when it is banked in `raw/`, or its URL when no local artifact exists. Use a YAML list
when the page stands on more than one artifact. On `/watch` pages it points to the full
`raw/watched/<slug>/report.md` path, never just `report.md`.

`transcript_source` already belongs to `/watch`: it is a KIND label, not a path. Carry
that label onto the `sources/` page verbatim so the producer and, for remote services,
the privacy boundary remain inspectable. Never replace an existing `transcript_source`
value with `source_file`. Normalize it into `transcript_kind` with this compatibility
map:

| `/watch` `transcript_source` label | Canonical `transcript_kind` |
|---|---|
| `captions (manual)` | `manual-captions` |
| `captions (auto)` | `auto-captions` |
| `captions` (bare, pre-upgrade) | `auto-captions` |
| `whisper (local)` or `local-whisper (<model>)` | `whisper-local` |
| `whisper (groq)` or `whisper (openai)` | `whisper-remote` |

Match labels as prefixes -- reports embellish them with model names. Bare legacy
`captions` maps to `auto-captions` on evidence, not caution: the pre-upgrade picker
preferred the AUTO track by an ASCII-sort accident (a since-fixed bug in the /watch tool), so even a video
carrying a human track got the machine transcript. Upgrade a bare-`captions` page to
`manual-captions` only by re-deriving the transcript from a confirmed uploader-authored
track, never from the label. `whisper-remote` records that audio left the local
machine; retain the verbatim `transcript_source` label to distinguish the provider. For a non-`/watch`
artifact, derive the canonical kind from the artifact itself. **Only when no mapping or
determination is possible, write `transcript_kind: auto-captions`** -- unknown
provenance gets the weakest tier, never the benefit of the doubt.

**The tier rule.** `auto-captions`, `whisper-local`, and `whisper-remote` are MACHINE
tier; `manual-captions`, `official`, and `human-verified` are VERIFIED tier.

**The tier question is WHO stands behind the words, not where they are hosted.** A
transcript the publisher or speaker posted themselves (show notes, publisher site,
uploader-authored captions) is VERIFIED tier -- someone accountable put their name on
those words. A free transcript generated by the hosting platform (YouTube's transcript
panel, a podcast host's auto-transcript page) is `auto-captions` no matter how clean it
reads or what the page calls it -- nobody checked it, the platform just ran a model.

- A **load-bearing claim** (figure, dose, threshold, direct quote, name spelling) may
  not rest on machine-tier transcription alone. Re-verify it -- against the recording
  at the timestamp, against a `/watch` frame when the claim was ON SCREEN (frames are
  scene-sampled stills: they show what was displayed, not what was said), or by
  corroborating it from an independent second source -- or mark it `==unverified==` so
  `check_stale.py` keeps it visible. Right-size the effort to the stake. (Exception:
  when re-verification is *permanently impossible*, the **Marker hygiene** section's
  terminal label `*(unverifiable: <reason>)*` replaces the marker under its downgrade
  rule -- do not re-add `==unverified==` over a logged downgrade. The label is not a
  disposition for the claim itself: a load-bearing claim in that state must still be
  softened in prose or dropped, per that section -- the label never licenses keeping
  it load-bearing.)
- A **direct quote** from a machine-tier transcript is labeled *(machine transcript)*
  on the sources page -- machine transcription regularly mangles exactly the tokens
  quotes exist to preserve (numbers, names, negations).

**Every video/audio-derived `sources/` page must contain a literal `## Locators`
section.** Each locator pairs a timestamp with the verbatim words at that point, for
example `- [12:34] "the words needed to verify the claim"`. The timestamp resolves
against the recording; the words show what was actually heard and make the evidence
searchable. A timestamp by itself is not a complete locator.

Every downstream claim must cite the `sources/` page with a `#Locators` anchor (or a
more specific evidence-heading anchor), because `audit_claims.py` counts only anchored
`sources/` wikilinks toward locator coverage. A prose `[12:34]` beside an unanchored
wikilink is still scored as no locator.

## Wikilinks name the file, not an alias

A wikilink names the target page's **filename (slug)**. Frontmatter `aliases:` serve
Obsidian's search, quick switcher and link suggestions only -- a bare `[[Alias]]` does
NOT resolve: it renders grey, clicking it creates a new empty note, and it earns the
intended page no inbound credit. (regression R151, click test 2026-09-29.)

To show an alternate name, write `[[slug|Alias]]` (inside a table cell `[[slug\|Alias]]`
-- escaped pipe, per `_meta/obsidian-syntax.md`) -- the slug resolves, the alias is
the display text. `lint.py` reports every violation under its **ALIAS-ONLY** section.
The backlog fixer is `python scripts/fix_wikilinks.py --alias-only`; its rollout (dry
run, smoke batch, click test, full run) is gated on the owner per the ingest skill's
close-out step -- never run its `--apply` without the owner's explicit go in the same session,
and the first apply in any vault is always `--apply --max-files 10` followed by the owner's click
test. Each `--apply` runs alone -- never chain another command after it in the same shell
invocation; read its result first -- in full: the exit code, REVIEW_REQUIRED and any
STOP / RESTORE / `RESTORE INCOMPLETE` line, never only a RESTORED count (regression R157: a
restore that printed `RESTORED = 297` had left 11 pages unrestored behind a transient cloud-sync lock).
`fix_wikilinks` operations that can mutate vault pages or shared operational state
serialize through one per-vault run lock (ADR-0006 / ADR-0007, regression R161): the
backtick-mode `--apply` and any `--alias-only` run that may write state (everything except plain
`--verify` and a plain `--migrate-legacy-state` dry run) refuses at once while another holds
the lock (exit 2, nothing written, the holder named) -- including the same vault reached
under another path spelling, and, conservatively, a different vault with the same folder
name while its run is live. A run that was hard-killed (crash,
power loss) leaves a STALE lock: the next run says so and, on Windows, prints the
`--break-lock` command -- run it, then a plain dry run before anything else (it reports any unfinished
run the dead holder left). On Windows, `--break-lock` is safe alongside other
`fix_wikilinks` runs: it judges and deletes the same `run.lock` object through one
exclusive handle, so it cannot delete a live or replacement lock that it did not judge
stale (regression R170, ADR-0008). Off Windows, automatic `--break-lock` refuses; remove
`run.lock` manually only after confirming no `fix_wikilinks` run is live. A lock whose holder cannot be proven gone is never broken by
flag; ask the owner. Backtick `--apply` refuses (exit 2) where it cannot take the lock --
`LOCALAPPDATA` unset or a Microsoft Store Python: use
`python -B`. The lock does not cover external editors (a human, Obsidian, sync): do not
edit pages while a fix_wikilinks `--apply` runs (backtick mode: regression R172).
Every `--apply` / `--restore` ends `LOCK RETRIES: N (max wait Xs)`; locks retry
automatically and a nonzero N is informational. The fixer's operational state (manifest, apply logs,
page backups) lives outside the vault and outside any sync service (ADR-0005:
`%LOCALAPPDATA%/WIKIllm/alias-fix/<vault>-<hash8>/`, override `--state-dir`); a vault
still holding the legacy in-vault layout (`.alias-fix-backup/`,
`_meta/alias-fix-manifest.json`) runs `--migrate-legacy-state` (then `--apply`) before its
next apply, and runs are settled or restored before a vault is moved. The same gate covers hand edits: no
session rewrites bare `[[Alias]]` links by hand or with its own script -- only the fixer,
only on the owner's go.
