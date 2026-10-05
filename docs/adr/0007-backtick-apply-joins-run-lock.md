# Backtick-mode --apply joins the per-vault run lock

## Status

Accepted, 2026-10-01 (regression R168; acceptance is the merge of the PR that
carries it). **Extends ADR-0006.** ADR-0006 introduced the per-vault run
lock for alias-state operations. ADR-0007 extends participation in that
same lock to backtick-mode `--apply`. ADR-0006 itself is unchanged.

## Context

ADR-0006 guaranteed one alias-state writer per vault and explicitly left
backtick-mode `fix_wikilinks.py --apply` out. That mode reads each eligible
page, regex-replaces backtick-wrapped wikilinks and writes it back, with no
operational state and no re-hash. Run alongside an alias `--apply` on the
same vault, it could overwrite a page the alias run had just written; the
alias apply log's recorded bytes would then no longer match, and a later
`--restore` would report REVIEW_REQUIRED instead of restoring.

## Decision

**`fix_wikilinks` operations that can mutate vault pages or shared
operational state serialize through one per-vault run lock**: one
lock-participating fix_wikilinks writer per vault. Backtick-mode `--apply`
now takes the SAME `run.lock` as the alias writers (ADR-0006), through the
same `RunLock` code.

**Joining the run lock must not turn backtick mode into an alias-state
transaction. It borrows only mutual exclusion and lock recovery.** Backtick
mode gains no manifest, no page backups, no apply log, no registry and no
state-dir discovery; it touches the default state directory only to hold
`run.lock`.

- **Dry run** stays lock-free and behavior-identical: it writes nothing.
- **Lock unavailable -> refuse.** If `--apply` cannot reach the lock --
  `LOCALAPPDATA` unset on Windows, or a Microsoft Store Python (whose
  `%LOCALAPPDATA%` writes are virtualized; on a typical Windows install the python3 alias resolves to it) --
  it exits 2 with nothing written, with the same message alias mode gives.
  Applying unlocked would reopen the race this ADR closes, under exactly
  the interpreter most likely to be used by accident.
- **Held lock -> refuse** at once (exit 2, nothing written), naming the
  holder, exactly as alias runs do.
- **Recovery** is the existing `--alias-only --root <vault> --break-lock`,
  which the refusal prints; no second spelling.
- **Sibling check**: inherited unchanged (other path spellings of one
  vault; conservative refusal of a same-folder-name vault while its run is
  live).
- **Lifecycle**: acquire before any page is read; release in `finally`,
  deleting only this run's own record.
- **Parity constraint**: an uncontended backtick run's stdout, exit codes
  and page bytes stay identical to the pinned pre-alias-only tool (legacy fixture label c376aaa); only
  the contended / refused paths print anything new.

External editors (a human, Obsidian, sync, other tools) are outside the
guarantee. Backtick mode's own read-then-write overwrite risk against them
is regression R172, deliberately not addressed here.

## Considered Options

- **A separate vault-write lock both modes take:** alias runs would then
  hold two locks, needing an acquisition order and a second recovery story.
  Its one advantage (a location not needing LOCALAPPDATA) does not survive
  the requirement that both modes contend on the same file.
- **Warn and apply unlocked when the lock is unreachable:** rejected (see
  Lock unavailable above).
- **Bring backtick mode under the alias transaction (backups, re-hash):**
  out of scope; the re-hash part is regression R172.
