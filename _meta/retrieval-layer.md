---
title: The Retrieval Layer — semantic search over the unread corpus
description: An optional fourth layer beneath the wiki. When your raw collection grows past what you can hand-ingest, add a semantic-search index over the *unread* remainder and drive two new operations from it — Sweep (corpus-wide thematic query → analysis page) and Promote (a source that keeps surfacing → full wiki ingest). The wiki stays curated; the retrieval layer is reconnaissance.
tags: [meta, pattern, retrieval]
---

# The Retrieval Layer

> [!abstract] What this file is for
> The base pattern (`_meta/llm-wiki.md`) assumes you read every source once and integrate it by hand. That breaks down when the raw collection is larger than you'll ever fully ingest — hundreds of PDFs, an archive, a corpus you inherited. This file describes the **retrieval layer**: a semantic-search index that sits *under* the wiki and over the **unread** bulk of your sources, plus the two operations that connect it back to the curated wiki — **Sweep** and **Promote**. It's optional. Add it only when hand-ingestion can no longer keep up with the inflow.

## Why a fourth layer

The base architecture has three layers: **raw sources** (immutable), **the wiki** (LLM-maintained synthesis), and **the schema** (`CLAUDE.md`). That model has a built-in assumption — every source crosses from "raw" to "integrated" through a human-in-the-loop ingest. One at a time. Read, discuss, file.

That assumption is the bottleneck the moment your corpus outgrows your reading time. If you have 250 PDFs and you ingest one a day, the wiki reflects the 30 you've read and is blind to the other 220 — not because they're irrelevant, but because they're queued. The knowledge is *present* in the raw layer and *absent* from the synthesis. Worse, you can't tell which of the 220 are worth jumping the queue for.

The retrieval layer fixes the blindness without pretending you've read everything. It is a **semantic search index over the sources you have NOT hand-ingested** — embeddings, BM25, or hybrid; on-device or hosted; exposed to the LLM as a CLI it shells out to or (better) an MCP tool it calls natively. The wiki remains the curated, integrated, trustworthy layer. The retrieval layer is the searchable, *un*-integrated remainder: lower-trust, citation-by-chunk, but instantly queryable across the whole corpus.

```
raw sources ──┬── hand-ingested ──► the wiki (read once, integrated, cross-linked, trusted)
              │
              └── not-yet-ingested ──► retrieval layer (semantic index; queryable but un-synthesized)
                                          │
                              ┌───────────┴───────────┐
                            Sweep                   Promote
                   (corpus-wide query →      (a source keeps surfacing →
                    new analysis page)        pull it up for full ingest)
```

The two operations are the bridges. **Sweep** pulls synthesis *out* of the unread corpus on demand. **Promote** moves a specific source *up* into the wiki when the retrieval layer shows it earning the read.

## Operation: Sweep

A **Sweep** is a corpus-wide thematic query whose answer is filed back as an `analysis/` page.

You ask a question that ranges across the whole collection — *"What does everything I haven't read say about X?"* The LLM queries the retrieval layer (often several phrasings of the query, to widen recall), reads the top-ranked chunks, and writes a synthesis page that:

- **answers the theme** from the unread corpus, in the wiki's normal prose-and-callout style;
- **cites by chunk/source** — every claim links to the source it came from, exactly as a normal page cites `sources/`, but flagged as retrieval-grade (see below);
- **marks its own confidence** — a Sweep reads *fragments*, not whole documents, so its claims are weaker than an ingested source's. Flag unverified or fragment-only claims with `==highlight==` (the base pattern's "needs-verification" marker) so a later full ingest can confirm or correct them;
- **records the query and the date** at the top, so the page is reproducible and its staleness is legible.

A Sweep page is reconnaissance, not gospel. It tells you what the corpus *probably* contains on a theme and which sources to read next — it does not replace reading them. Treat it like a literature scout's memo: valuable, directional, and explicitly provisional.

> [!tip] Sweep is also how you decide *what* to ingest next
> The sources a Sweep keeps pulling from are, by definition, the ones richest on themes you care about. That hit-frequency signal is the input to Promote.

## Operation: Promote

A **Promote** moves a single source from the retrieval layer up into the wiki as a full, hand-ingested page.

The trigger is **hit frequency**: when the same source keeps ranking high across multiple Sweeps and queries, the retrieval layer is telling you it's load-bearing for your topic — so stop reading it in fragments and ingest it properly. Promotion is just the base **Ingest** operation (read the whole source, discuss, write a `sources/` page, update entities/concepts/index/log) applied to a source the retrieval layer nominated.

After a Promote, two cleanups matter:

1. **Reconcile the Sweeps.** Any `analysis/` Sweep page that cited the now-ingested source on `==highlighted==` provisional claims should be revisited — confirm, correct, or delete those flags now that the source is fully read. This is ordinary close-out propagation; if it can't be done in-session, it goes to `_meta/open-loops.md` like any other deferred ripple.
2. **Avoid double-counting.** A promoted source is now in the wiki; depending on your indexer, either drop it from the retrieval index or accept that it lives in both layers. Be deliberate so the same document doesn't get cited as both "read" and "unread."

Promotion is the pressure valve that keeps the two layers honest: the wiki grows toward the sources that actually matter, and the retrieval layer shrinks toward the genuinely peripheral.

## How this changes the schema and the log

If you adopt the retrieval layer, reflect it in `CLAUDE.md` and the log:

- **Document the index** in `CLAUDE.md`: what tool backs it, what's indexed vs. not (e.g. PDF-only pipelines won't see `.epub`/`.docx`), and how the LLM reaches it (CLI command or MCP tool names). A session that doesn't know the retrieval layer exists won't use it.
- **Add `sweep` (and optionally `promote`) to the log-action vocabulary** so `log.md` stays greppable: `## [YYYY-MM-DD] sweep | <theme>`. A Promote can log as a normal `ingest` with a note that it was retrieval-nominated, or as its own `promote` action — pick one and be consistent.
- **Keep Sweep output in `analysis/`,** not `sources/`. A `sources/` page means "this document was read and integrated." A Sweep didn't read any single document fully — it read across many partially. Filing it as analysis preserves that distinction.

## When NOT to add this

The retrieval layer earns its complexity only past a scale threshold. Skip it when:

- your corpus is small enough that `index.md` + reading everything is tractable (the base pattern's sweet spot — roughly hundreds of pages, hand-ingested);
- you'd be indexing sources you actually intend to read soon anyway (just ingest them);
- you can't cite the retrieval layer's hits back to specific sources — un-citable search is worse than no search, because it launders provenance.

The wiki is the durable artifact; the retrieval layer is scaffolding for a corpus too big to read at once. Add it when the inflow outpaces the reading, and let Promote steadily convert the parts that matter into real wiki pages.
