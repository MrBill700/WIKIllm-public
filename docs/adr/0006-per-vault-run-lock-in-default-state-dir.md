# A per-vault run lock in the default state directory serializes alias-state writers

## Status

Accepted, 2026-09-30 (regression R161; acceptance is the merge of the PR that
carries it). Extends ADR-0005, which stands unchanged. Its accepted D1 residual
(`--break-lock` judge -> move-aside -> compare not atomic) is closed by ADR-0008
(regression R170).

## Context

`fix_wikilinks.py --alias-only --apply` / `--restore` had no mutual
exclusion. Two concurrent applies of one manifest both wrote pages and both
saved `apply-log.json`; the last save won, and in the R151/R153 reviewer's
two-process experiment a later restore left 50 pages differing from
pristine. The only mitigation was a docs rule ("one run per vault at a
time").

## Decision

**The lock protects the vault, not an individual state directory; therefore
its location must be derived solely from vault identity and cannot vary with
`--state-dir`.** It is `run.lock` in the vault's DEFAULT state directory
(ADR-0005's `<base>\alias-fix\<vault-folder-name>-<hash8>\`), created if
absent, whatever `--state-dir` the run uses.

- **Identity, and the sibling check.** The primary lock key remains the
  resolved normalized vault path per ADR-0005. Because Windows permits
  multiple path spellings to reach the same physical directory (review
  repro: `C:\...\Vault` and `\\localhost\c$\...\Vault` hash to two keys,
  and two `--apply` runs proceeded at once), writers also perform a
  conservative same-name sibling-lock check before proceeding: after
  acquiring its own lock, a writer checks same-folder-name sibling state
  keys for another `run.lock`; if any sibling lock's holder is live or its
  liveness cannot be disproven, the run releases its own lock and refuses
  (exit 2). Two simultaneous contenders may both refuse; safety is
  preferred to choosing a winner. A genuinely different vault with the same
  folder name is refused the same way while its run is live (a deliberate
  false positive), but nothing else of that key -- its runs, registry or
  lock -- is ever treated as this vault's. Physical file-system identity is
  not yet the lock key.
- **Scope.** This ADR guarantees mutual exclusion among alias-state writers
  only. Backtick-mode `--apply` does not yet participate; that cross-mode
  race is tracked separately in regression R168.
- **Who takes it:** every alias-mode run that writes operational state -- the
  dry run (it rewrites `manifest.json`), `--apply`, `--restore`,
  `--migrate-legacy-state --apply`, `--rebind-state`, `--forget-state-dir`.
  Plain `--verify` is lock-free; `--verify --state-dir` participates in
  locking because it may mutate the state-dir registry (it takes the lock
  before any registry read, then registers only if needed). The migrate dry
  run (`--migrate-legacy-state` without `--apply`) follows the same rule for
  the same reason.
- **Held lock -> refuse at once** (exit 2, nothing written), naming the
  holder, the lock path, whether the holder looks alive, and the
  `--break-lock` command when it is stale. No waiting: a collision is a
  mistake, not a queue.
- **Atomic acquisition.** The complete holder record (unique `lock_id`, pid,
  that process's creation time, host, mode, start time) is written to a
  uniquely named temp file in the same directory, flushed and fsynced, then
  `os.link(tmp, run.lock)`. The link is the acquisition point: if `run.lock`
  exists, this process did not acquire it. The temp name is deleted after
  the link either way; an orphan temp file is never a lock. If hard links
  are unsupported, the run refuses (exit 2) -- there is no `O_EXCL` +
  later-write fallback. Invariant: **a visible `run.lock` is always a
  complete holder record created in one atomic namespace operation.**
- **Lifecycle.** Acquired after resolving vault identity and the default
  state dir path, BEFORE any state read (unfinished-run check, `vault.json`,
  registry, manifest). One exception, refusal-only: ADR-0005's path
  validation and the identity check of an explicit `--state-dir` (its
  `vault.json`, and this vault's own `vault.json` for the `rebound_from`
  grant) run first, so an invalid or foreign `--state-dir` / `--manifest`
  is still refused with nothing written anywhere -- not even the lock or
  this vault's default dir (ADR-0005's one-state-dir-per-vault guarantee).
  The registry and run discovery are read only under the lock, and every
  check is repeated there. Released in `finally`: re-read, delete only if its
  bytes equal this process's record (deletion retried on WinError 5/32/33);
  otherwise warn and leave it. A failed release warns conspicuously and
  keeps the run's own exit code -- the operation may have completed
  correctly, and the leftover lock is stale by construction.
- **Stale lock, fail closed.** A holder is alive unless provably gone: pid
  absent, or present with a different process creation time (PIDs are
  reused). An unreadable creation time, a foreign host, or a malformed lock
  file is unprovable and counts as alive. `--break-lock` is a standalone
  action (combining it with any run mode is a usage error): it removes only
  a provably stale lock and exits; the next dry run then shows any
  unfinished run the dead holder left. **Accepted residual (the owner, D1,
  2026-10-01):** its judge -> move-aside -> compare is not atomic, so two
  concurrent `--break-lock` runs plus a new acquirer can displace a live
  lock. That case is loud (the displaced record is kept at
  `run.lock.broken-<hex>` and named, exit 2), not silent; `--break-lock`
  recovery must never be run concurrently. A Windows handle-based
  compare-and-delete that closes it is regression R170.

## Considered Options

- **`<state>/run.lock` in the run's own state dir** (the issue's literal
  shape): two runs on one vault with different `--state-dir` values would
  both proceed. Rejected -- the race is on the vault's pages, not on a dir.
- **Wait for the lock instead of refusing:** hides an unexpected second run.
- **`O_EXCL` create then write the record:** a crash between the two leaves
  an empty lock that, under fail-closed liveness, could never be broken by
  flag. The temp-file + link shape removes the window instead.
