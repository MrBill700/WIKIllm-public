---
title: Open Loops — deferred-propagation ledger
description: The single place deferred work lives. When a session resolves something but can't finish rippling it across every downstream page, the unfinished ripple goes HERE as a dated, specific line — never buried in a log entry. Every session reads this at start; check_stale.py reports it (section A) and flags overdue items.
tags: [meta, process]
---

# Open Loops — deferred-propagation ledger

> [!abstract] What this file is for
> **Deferred *propagation debt*** — ripples from a decision, ingest, or re-key that couldn't be finished in the session that created them. The forcing function that stops "I'll update the other pages later" from silently becoming "never."
>
> **Read this at the start of every session.** Write to it at close-out per the session close-out protocol in `CLAUDE.md`. `scripts/check_stale.py` surfaces it (section A) and flags any item whose `trigger:` date has passed.

## What belongs here vs elsewhere

| Goes here | Goes elsewhere |
|---|---|
| Unfinished *propagation* of an already-made decision ("re-key a date on N pages", "soften framing to match the new answer") | **Open questions** awaiting a human's input → a dedicated questions page |
| A re-key / terminology pass that's started but not swept everywhere | **Research gaps / things to investigate** → an `analysis/blind-spots.md` |
| "Did X, but Y downstream still references the old state" | **Dated future decisions** → an `analysis/decision-calendar.md` |

If it's a *question*, a *research gap*, or a *future decision*, it lives on those pages — not here. This file is only for **work already decided that just needs finishing**.

## Format

```
- [ ] <specific task> — trigger: YYYY-MM-DD (opened YYYY-MM-DD, from <decision/session>)
```

- `trigger:` = a real date by which it should be done, so `check_stale.py` can flag it overdue. Near date for quick edits; a prep-window date for larger work.
- `[ ]` open · `[~]` partially done · `[x]` done (move to the Resolved archive below).
- One line per loop. If it needs a paragraph, it's probably a research-agenda item, not an open loop.
- **Closing a loop means flipping the marker to `[x]`**, not only annotating the line. `check_stale.py` section A reads the marker and nothing else, so a dated CLOSED note left on a `[ ]` or `[~]` line still reports open and overdue at every close-out. (regression R74; the same rule is in the synced `_meta/fleet-conventions.md`, which is where the existing vaults get it.)

## Recurring items

Machine-watched by `check_stale.py` section D: it parses `Last run: … Next due: ~YYYY-MM`
and flags the item DUE at close-out once that month arrives. Re-stamp **both** dates after
each run. Uncomment the audit below once the wiki has real content (~20+ pages with
citations) and set a due month a quarter out. Until then section D prints
`claim audit: NOT INSTALLED` at every close-out. If the audit genuinely does not apply
(the vault has no source-citing claims -- `audit_claims.py` finds none), record that
instead of leaving the nag: a line `claim audit: exempt -- <reason>` anywhere in
`_meta/` or `wiki/` turns the status into `EXEMPT` (regression R79).

<!--
- **Quarterly: claim audit** — run `python scripts/audit_claims.py`, verify each sampled
  claim against its `sources/` page and the document in `raw/`, judge FAITHFUL / DRIFTED /
  UNSUPPORTED / STALE, fix drift immediately, log it as the script's NEXT STEPS block prints.
  **Last run: never. Next due: ~YYYY-MM.**
-->

## Open

- _none yet_

## Resolved archive

Closed loops, kept for traceability (date closed — what / where).

- _none yet_
