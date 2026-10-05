# WIKIllm — a starter template for LLM-maintained wikis

A reusable scaffold for building **persistent, compounding knowledge bases** with an LLM (Claude Code, etc.) doing the bookkeeping. Instead of re-deriving understanding from raw documents on every question (RAG-style), the LLM reads each source once, integrates it into an interlinked Obsidian-style wiki, and *keeps it current* — so the synthesis, cross-references, and contradictions accumulate over time.

This template is the distilled pattern — structure, conventions, automation, and a session discipline — with no subject content. Copy it, point your LLM at it, pick a topic, and start ingesting. The pattern itself is described in [`_meta/llm-wiki.md`](_meta/llm-wiki.md).

## Quick start

1. **Copy this template** to a new folder named for your topic (or use GitHub's "Use this template").
2. **Fill in `CLAUDE.md`** — the four `<...>` placeholders: the title, the one-line description, *The subject*, and *Goals*. This is the schema your LLM reads every session; it's what turns a generic chatbot into a disciplined wiki maintainer.
3. **Drop sources into `raw/`** — PDFs, articles, clipped web pages, notes, `.epub` books. These are immutable; the LLM reads them but never edits them. Source documents under `raw/` are ignored by Git by default (see `.gitignore`); review any file before deliberately versioning it.
4. **Initialize the change baseline:** `python scripts/check_raw.py --init`
5. **Open the folder in your LLM agent** (and Obsidian alongside, if you like — point Obsidian at `wiki/`). Ask it to ingest the first source. Browse the results, follow links, guide the synthesis.

You curate sources and ask questions; the LLM does the summarizing, cross-referencing, filing, and maintenance.

## Layout

```
CLAUDE.md             the schema — conventions + workflows + the session close-out protocol (FILL THIS IN)
SETUP.md              requirements, trusted sync source, how to run the tests
AGENTS.md             contributor and agent instructions
CONTEXT.md            glossary of the maintenance/audit terms
.claude/              skills + workflows (files in SYNC_SET are synced from the template; do not edit those locally)
docs/adr/             architecture decision records
raw/                  your source documents (immutable)
wiki/                 the LLM-maintained knowledge base
  index.md            catalog of every page
  log.md              append-only record of ingests / queries / lint passes
  entities/  concepts/  sources/  analysis/
  analysis/positions-register.md   the epistemics layer: load-bearing beliefs, each with status,
                      strongest counter-argument on file, and pre-registered "what would change
                      our mind" criteria (skeleton included — fill once the wiki takes positions)
  data/               optional: one note per datapoint + Obsidian `.base` files rendering them as
                      live sortable tables (races, experiments, a to-ingest checklist...). See the
                      "Structured data & Bases" section of CLAUDE.md.
_meta/
  llm-wiki.md         the pattern, in the abstract (read first)
  retrieval-layer.md  optional: semantic search over an unread corpus + Sweep/Promote (for collections too big to hand-ingest)
  obsidian-syntax.md  Obsidian formatting catalog
  open-loops.md       deferred-work ledger
  fleet-conventions.md, book-scanning.md   shared conventions (synced from the template; do not edit locally)
scripts/              automation (see scripts/README.md)
```

## The scripts

Core scanners use Python 3.11+ and the standard library. Optional capture, OCR, model-assisted features, and workflow integrations have additional requirements; see [SETUP.md](SETUP.md). Run from the repo root.

| Script | Does |
|---|---|
| `check_raw.py` | Detects added/modified/removed files in `raw/` against a SHA-256 baseline, so source changes never slip by silently. |
| `lint.py` | Wiki health-check: broken wikilinks, orphan pages, ghost/placeholder links, inbound-link hubs. |
| `check_stale.py` | Staleness detector: the open-loops ledger (+ overdue), time-sensitive markers ("pending", "TODO", `==verify==`, placeholder/TBD) grouped by file, `central`-tagged pages gone stale, and **recurring-task stamps (`Next due: ~YYYY-MM`) flagged when due** — the reminder mechanism for the quarterly audit. Also validates terminal `*(unverifiable: <slug>)*` labels against their closed vocabulary, and reports any required instance-owned scaffold file the vault is missing. Advisory — surfaces candidates, never auto-fixes. |
| `audit_claims.py` | Claim-faithfulness sampler: deterministically picks N citation-bearing claims per quarter for the session to verify against their sources — catches the drift class nothing else can (a page asserting something its cited source doesn't quite say). Also reports locator coverage. |
| `extract_chapter.py` | Pre-chunks `.epub` chapters into files small enough for an LLM's read limit. |
| `sync_from_template.py` | Pulls shared tooling (`scripts/`, the `.claude/skills/` and `.claude/workflows/` files listed in `SYNC_SET`, and two shared `_meta/` docs, which need `--adopt-meta` if they differ locally) from this template into an existing wiki. Dry-run drift report by default; `--apply` copies template → instance. |

### This template is upstream

Improvements to the *shared* tooling land here first, then flow out to each wiki via
`python scripts/sync_from_template.py --apply`. Instance-owned content (`CLAUDE.md`, `wiki/`, `raw/`, and most of `_meta/`) is never synced. The two shared `_meta/` convention documents require explicit adoption if they differ locally.

Two habits keep that working:

- **After creating a wiki, run the dry run once** (`python scripts/sync_from_template.py`).
  First configure the trusted template checkout as described in [SETUP.md](SETUP.md). It should report everything in sync; inspect any drift before applying updates.
- **If a wiki improves a synced file, back-port it here before syncing that wiki.**
  The drift report says *that* a file differs, not *which side is newer* — so check for
  content present in the instance and absent here first. `--apply` overwrites the
  instance copy, and a lesson learned in one wiki is lost if it is pulled over before
  it is back-ported.
- **Review changes in a separate branch or worktree.** Keep the template checkout used for syncing on clean `main`, equal to its fetched `origin/main`.


Full docs in [`scripts/README.md`](scripts/README.md). The two you'll run most are `lint.py` and `check_stale.py` — both are part of the **session close-out protocol** in `CLAUDE.md`, the discipline that keeps the wiki from drifting as it grows.

## Scaling past what you can read

The base pattern assumes you hand-ingest every source. When the raw collection outgrows your reading time — hundreds of PDFs, an inherited archive — add the optional **retrieval layer** ([`_meta/retrieval-layer.md`](_meta/retrieval-layer.md)): a semantic-search index over the sources you *haven't* ingested, plus two operations that bridge it back to the wiki. **Sweep** runs a corpus-wide thematic query and files the synthesis as an `analysis/` page; **Promote** takes a source that keeps surfacing in searches and pulls it up for a full, proper ingest. The wiki stays curated and trusted; the retrieval layer is searchable reconnaissance over the unread bulk. Modular — ignore it until you need it.

## What makes a wiki stay maintained

The hard part of a knowledge base isn't reading or thinking — it's the bookkeeping: updating cross-references, keeping summaries current, noting when new sources contradict old claims, propagating one decision across a dozen pages. Humans abandon wikis because that cost grows faster than the value. An LLM doesn't get bored and can touch fifteen files in one pass — *if* you give it the structure and the discipline to do so. That discipline is the close-out protocol + `check_stale.py`; that structure is everything else here.

## Credit

Built from the LLM-wiki pattern in [`_meta/llm-wiki.md`](_meta/llm-wiki.md). Related in spirit to Vannevar Bush's Memex — a personal, actively-curated knowledge store where the links between documents are as valuable as the documents. The part Bush couldn't solve was who does the maintenance. The LLM does.

## Public snapshot

This repository starts with fresh history. Historical `R<number>` labels in comments identify regression cases, not issues in this repository. Examples of operational incidents use anonymous vault names. See [SETUP.md](SETUP.md) for supported entry points and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for bundled material.
