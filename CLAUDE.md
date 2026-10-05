# <TOPIC> Wiki

> [!note] Starting a new topic? Fill in the four `<...>` placeholders: the title, the one-line description, *The subject*, and *Goals*; drop your sources in `raw/`, run `python scripts/check_raw.py --init`, then ask Claude to ingest the first source. Delete this note when done.

A persistent, LLM-maintained knowledge base about **<one-line description of the topic>**. The pattern this wiki follows is documented in `_meta/llm-wiki.md` — **read that first if you're a new session.** The Obsidian formatting catalog is in `_meta/obsidian-syntax.md`.

## The subject

<Who/what this wiki is about. People, entities, scope, key facts a session needs up front. For a research topic: the thesis and boundaries. For a book: the work and why you're reading it. Keep it short — detail lives on `entities/` pages.>

## Goals (in priority order)

1. <What is this wiki *for*? What decision or understanding is it driving toward?>
2. <…>
3. <…>

These are the tiebreakers when two strategies/pages conflict. Revisit as the topic evolves.

## Directory layout

```
raw/                  source documents (PDFs, articles, notes, clips) — immutable; the LLM reads but never edits them.
                      scripts/check_raw.py detects changes against a SHA-256 baseline.
wiki/                 the LLM-maintained knowledge base
  index.md            catalog of all pages, organized by category
  log.md              append-only chronological record of every operation
  entities/           the concrete nouns of the topic (people, orgs, accounts, places, objects)
  concepts/           the ideas, rules, mechanisms, recurring themes
  sources/            one page per ingested raw source — summary + key takeaways + citation
  analysis/           synthesized pages: comparisons, projections, plans, decisions
  data/               OPTIONAL: one small note per datapoint (structured frontmatter) + Obsidian
                      `.base` files rendering them as live sortable/filterable tables. For anything
                      you'd otherwise hand-maintain as a growing markdown table. See "Structured data & Bases".
_meta/                pattern docs + reference about the wiki itself
  llm-wiki.md         the pattern (read first)
  fleet-conventions.md FLEET-SYNCED: close-out protocol, twin-artifact citations,
                      partial-ingest ledger. Never edit locally.
  book-scanning.md    FLEET-SYNCED: getting books into raw/ cleanly -- physical scans and owned-ebook screen capture.
  retrieval-layer.md  OPTIONAL fourth layer for corpora too big to hand-ingest: semantic search over the unread remainder + the Sweep/Promote operations
  obsidian-syntax.md  the formatting catalog
  open-loops.md       deferred-propagation ledger (see the close-out protocol)
  raw-hashes.json     SHA-256 baseline of raw/ (managed by scripts/check_raw.py; created on first --init)
scripts/              automation — see scripts/README.md
```

(Rename `entities` / `concepts` if your topic wants different buckets — e.g. a book wiki might use `characters` / `themes` / `chapters`. Keep `sources/`, `index.md`, `log.md`.)

## Conventions

For the full Obsidian syntax catalog (callouts, mermaid, math, block links, etc.) see `_meta/obsidian-syntax.md`. Core rules:

- **Links:** Obsidian-native `[[wikilinks]]`, not standard markdown `[text](file.md)`.
  - **CRITICAL: never wrap wikilinks in backticks** — `` `[[foo]]` `` renders as code and Obsidian won't parse it as a link, breaking the graph. Write `[[foo]]` bare.
  - **Use the richer forms when they help.** Heading link `[[page#Heading]]`; same-note heading `[[#Heading]]`; block link `[[page#^block-id]]`; display override `[[page|Display text]]`. A link names the target's filename, never an alias: frontmatter `aliases:` serve search and the quick switcher only -- Obsidian leaves a bare `[[Alias]]` grey (regression R151) -- so show an alternate name as `[[page|Alias]]`.
  - **`[[...]]` inside YAML frontmatter values DO create graph edges.** `sources: ["[[sources/foo]]"]` makes an edge; `sources: [foo]` does not. Be careful with placeholder values from clipper tools.
  - **Inside table cells**, escape pipes in wikilink display text / image-resize syntax: `[[concepts/foo\|Foo]]`, `![[chart.png\|200]]`.
- **Filenames:** `kebab-case.md`. Avoid `# | ^ : %% [ ]` — Obsidian treats them as link syntax.
- **Frontmatter:** every wiki page gets YAML frontmatter (`title`, `description`, optional `tags`, `updated`, `sources`, `aliases`). The `updated:` date is load-bearing — `check_stale.py` and reviews key off it. Tag genuinely-central pages `central`.
- **Citations:** when a page makes a claim from a source, link the `sources/<slug>` page with a bare `[[...]]` wikilink.
  - **Locator rule:** when the claim is load-bearing (a figure, a date, a threshold, a direct quote), the `sources/` page must carry a *locator* for it — a verbatim quote (`> [!quote]`) and/or a chapter/page/heading reference — so a future session can re-verify against `raw/` without re-reading the whole document. Prefer anchored links from the citing page (`[[sources/foo#heading]]`) over bare page links. `scripts/audit_claims.py` reports vault-wide locator coverage and samples claims for the quarterly faithfulness audit.
  - **A work held as two artifacts** (page scans + a searchable digital twin) uses citation forms that name the artifact -- `p.NNN` = the scan, `ch.N, "Heading"` = the digital text; never interpolate page numbers across idioms. Full rules: `_meta/fleet-conventions.md`.
- **Dates:** absolute (`2026-05-24`), never relative ("last week").
- **Callouts** for non-flowing content: `> [!warning]` risks; `> [!question]` / `[!faq]-` open questions; `> [!todo]` pending items; `> [!success]` / `[!done]` resolved decisions; `> [!abstract]` page-top TL;DR; `> [!quote]` verbatim source.
- **Highlights / strikethrough carry meaning:** `==text==` flags an unverified claim or value-needing-update (caught by `check_stale.py`); `~~text~~` preserves a superseded claim visibly rather than deleting it (use sparingly).
- **Mermaid** (` ```mermaid `) for diagrams; **MathJax** (`$inline$`, `$$block$$`) where a formula matters more than prose.

## Structured data & Bases (optional layer)

When a topic accumulates **repeating, comparable records** — races, experiments, releases, transactions, readings, tastings, anything you'd otherwise keep re-editing as a growing markdown table — promote them out of prose into the `wiki/data/` layer. The payoff: the table sorts/filters live in Obsidian and **updates itself** when you add a record, which is exactly the bookkeeping an LLM-maintained wiki should automate.

The pattern (syntax reference: the `obsidian-bases` skill in `.claude/skills/`):

1. **One note per datapoint** under `wiki/data/<type>/<slug>.md`, with the values as **typed frontmatter** (numbers as numbers so they sort) and `tags: [<type>, data]`. Keep a one-line body linking back to the `analysis/` page that interprets the set.
2. **One `.base` file** per collection at `wiki/data/<type>.base` — filter by tag (`file.hasTag("<type>")`, tag-based so it's robust to whether Obsidian opens at the repo root or `wiki/`), add `formulas:` for computed columns (e.g. pace from duration), define one or more `views:` (table/cards) with `order:` and `summaries:`.
3. **Embed the view** into the relevant `analysis/` page with `![[data/<type>.base]]`; the analysis page keeps the *narrative*, the Base is the *source-of-truth table*. (`lint.py` resolves `.base` embeds as non-`.md` assets — no broken-link noise.)

Copy-paste skeleton:

```yaml
# wiki/data/races.base
filters:
  and:
    - 'file.hasTag("race")'
    - 'file.hasTag("data")'
formulas:
  pace_min_km: 'if(duration_sec, (duration_sec / 10 / 60).round(2), "")'
views:
  - type: table
    name: "Races"
    order: [date, time, formula.pace_min_km, avg_hr, note]
    summaries: { avg_hr: Average }
```

**Status-by-tag trick:** a checklist (e.g. ingest progress) needs no manual status field — derive it, `status: 'if(file.hasTag("to-ingest"), "to-ingest", "ingested")'`, and flip state by adding/removing the tag. Bases render in Obsidian 1.9+ only (plain-markdown viewers show the embed line); open a `.base` once to confirm it renders. This layer is **instance content, not synced tooling** — each wiki grows its own `data/` records.

## Operations

The three workflows — **ingest**, **query**, **lint** — are described in `_meta/llm-wiki.md`.

> [!note] Large corpus? Two more operations are available.
> If your raw collection grows past what you can hand-ingest, add the **retrieval layer** (`_meta/retrieval-layer.md`): a semantic-search index over the *unread* sources, plus **Sweep** (corpus-wide thematic query → `analysis/` page) and **Promote** (a source that keeps surfacing → full ingest). It's optional and modular — ignore it until inflow outpaces your reading.

- **Ingest cadence:** one source at a time, human in the loop. Discuss key takeaways before writing pages. A single ingest may touch 10–15 pages (one new `sources/` page, updates to relevant `entities/` and `concepts/`, an `index.md` entry, a `log.md` entry).
- **Log entry format:** `## [YYYY-MM-DD] <op> | <subject>` where `<op>` is `ingest`, `query`, `lint`, or `meta`. Append-only.
- **Sensitive info:** keep specifics (numbers, IDs, private details) on `entities/` pages only; concept/analysis pages reference them via wikilink rather than restating, so updates propagate from one place.

## Workflow conventions for Claude sessions

- **"Check raw" / "triage the inbox" / ingestion requests → the `/ingest` skill** (`.claude/skills/ingest/`) runs the whole loop: `python scripts/check_raw.py` first (never `ls` timestamps or memory — it hashes `raw/` against `_meta/raw-hashes.json` and reports added/modified/removed deterministically), ingest, accept, close out. Accept per-file (`--accept "exact-name.pdf"`) during multi-source corpus work; `--accept-all` only when every detected change has been ingested. (Bare `--accept` is invalid — pass filenames or `--accept-all`.) "Inbox" here always means `raw/`, never email.
- **Never truncate script output with `| head` or `| Select-Object -First N`.** Use `--summary` or save complete stdout and stderr to a file. An early-closed pipe can interrupt Python. Keep content writes in separate commands and verify their results.
- **Script-generated pages** carry `auto_generated: true` in frontmatter — don't hand-edit; they're overwritten on the next run.
- **New tooling lives in `scripts/`** with docs in `scripts/README.md`. Update both.

## Session close-out protocol

Run before ending **any ingest or question session**. **The protocol itself lives in
`_meta/fleet-conventions.md`** -- a fleet-synced file (regression R27), so a protocol fix
lands in every instance via `sync_from_template.py` instead of one hand edit per wiki; do not
copy its text back into this file. In one line: propagate every ripple in-session or
into `_meta/open-loops.md` (never into prose), bump `updated:`, write the `log.md`
entry, run both scanners, close with a one-line summary. **At session start:** read
`_meta/open-loops.md` and clear anything due. `_meta/fleet-conventions.md` also carries
the scan+digital-twin citation rules and the partial-ingest ledger convention.

## The positions register (the epistemics layer)

Once the wiki starts taking *positions* — working conclusions a wrong answer would be costly on — create **`wiki/analysis/positions-register.md`**: one row per load-bearing belief, each with:

- **Status:** 🟢 settled-after-challenge (survived an adversarial source; don't relitigate without the named evidence kind) / 🟡 working-default (lightly tested, expected to move) / 🟠 assumption (a modeling placeholder that hardens through repetition — the labeling stops restatement from masquerading as evidence).
- **Strongest counter-argument on file** — an empty cell is a flagged reading assignment (you're inside a bubble on that position; commission the opposing source deliberately).
- **Pre-registered falsification criteria** — *what evidence would change our mind*, written while calm, before markets/salespeople/sunk costs supply motivated answers.

Pair it with the **steelman-at-ingest rule**: when a source *agrees* with a working position, its `sources/` page must still record the strongest claim against the position found in it — or note explicitly that it contained none. New evidence lands on the register before it counts as mind-changing. At the annual review, sweep the register: statuses still honest? falsification criteria silently fired? and tally the year's genuine reversals — the scoreboard for whether the wiki is *updating* or just accumulating. (Inclusion test for rows: would being wrong reshape the project? Keep it to ~10; if everything's a position, nothing is.)

## Recurring maintenance (the quarterly claim audit)

Once the wiki has real content (~20+ pages with citations), start the **quarterly claim audit**: run `python scripts/audit_claims.py`, verify each sampled claim against its sources page + raw/ document, judge FAITHFUL / DRIFTED / UNSUPPORTED / STALE, fix drift immediately, then log it exactly as the script's NEXT STEPS block says (the log-line shape and the cadence rule live there, not here -- regression R63). This catches the drift class no scanner can: a page asserting something its cited source doesn't quite say.

Track it (and any other recurring task) with a stamp line on a recurring-items page:

```
- **Quarterly: claim audit** — run `python scripts/audit_claims.py`, judge the queue, fix drift, log it. **Last run: never. Next due: ~YYYY-MM.**
```

**The `Last run: … Next due: ~YYYY-MM` phrasing is machine-watched** — `check_stale.py` section D parses it and flags the item DUE at every session close-out once that month arrives, so the cadence survives without anyone remembering. Re-stamp both dates after each run. Whether to stretch the cadence or raise `--n` is decided by the rule the script prints under NEXT STEPS (regression R63).
