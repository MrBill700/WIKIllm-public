# --break-lock judges and deletes a stale run lock through one handle

## Status

Proposed, 2026-10-01 (regression R170; full grill complete, Q1-Q8 ruled by
the owner; becomes Accepted on the merge of the PR that carries it).
**Extends ADR-0006.** It closes the residual ADR-0006 accepted under the owner's
D1 ruling (2026-10-01): `--break-lock`'s judge -> move-aside -> compare was
not atomic.

## Context

ADR-0006's `--break-lock` read `run.lock` by path, judged its holder stale,
then renamed whatever was at that path to `run.lock.broken-<hex>` and
compared. Between the read and the rename, a second `--break-lock` could
remove the stale lock and a new run C acquire; the first breaker then moved
C's LIVE lock aside. Its link-back could fail (a run D acquired in the gap),
leaving C and D both believing they held the vault -- loud since the ADR-0006 run lock
(exit 2, the displaced record named), but not prevented. Even a successful
link-back left a moment with no `run.lock` at all.

The root cause: the stale decision was made on one filesystem object and
the action taken on whatever later occupied the path.

## Decision

**The stale judgment and the deletion apply to the same filesystem object,
and the pathname stays occupied until the deciding handle closes.**

- **Q1 -- Windows delete primitive (the owner, 2026-10-01).**
  `CreateFileW(DELETE | GENERIC_READ, share mode 0)` -> read and judge the
  holder record through that handle -> `SetFileInformationByHandle(
  FileDispositionInfo, DeleteFile=TRUE)` -> `CloseHandle`. The handle-open
  window holds only those operations: no printing, logging or unrelated
  I/O while it is held. POSIX delete semantics (`FileDispositionInfoEx`)
  are not used.

  Evidence (scratch probe, NTFS, Python 3.14, 2026-10-01): while the handle
  is open -- before and after the disposition is set -- the name stays
  listed; a racing `os.link(tmp, run.lock)` fails `FileExistsError`
  (WinError 183), so no acquirer can slip into the name; a second
  `CreateFileW` fails WinError 32 (before disposition) or 5 (after); the
  name disappears at `CloseHandle`. The POSIX flag behaved identically
  (share mode 0 leaves no other handle for it to matter to) and needs
  Windows 10 1709+ on NTFS.

  Q1 solves: no read-by-path / delete-later race; no empty-name acquisition
  gap; a new holder cannot be displaced. Q1 does NOT solve (later rulings):
  an acquirer racing the break sees 183, then its by-path read hits the
  share-0 sharing violation (`PermissionError` errno 13, no winerror -- not
  retried by the R4 predicate) and prints a misleading "malformed /
  unprovable, delete by hand" refusal; a second breaker's 32/5 needs a
  retry/reporting policy; the other by-path readers (`_held_message`,
  `sibling_locks`, `RunLock.release`) need a deliberate treatment.

- **Q2 -- off-Windows: fail closed (the owner, 2026-10-01).** POSIX has no
  delete-by-handle and no share-0 open; `flock` is advisory, so an
  equivalent needs acquire, release AND break all to cooperate -- a second
  locking architecture this machine cannot exercise. On a non-Windows
  platform `--break-lock` exits 2 without touching `run.lock`, says
  automatic stale-lock removal is unsupported there, and prints the lock
  path with the manual-recovery guidance (delete it by hand only after
  independently confirming no fix_wikilinks run is live for the vault).
  Off-Windows acquire and release are otherwise unchanged: this is a
  Windows-only BREAK, not Windows-only locking. "Unchanged" means the
  locking and I/O semantics (by-path acquire, read, compare, unlink), NOT
  the message text: the platform-neutral diagnostics -- the acquirer's
  "released or broken while this run was inspecting it -- re-run" and
  "unreadable" rather than "malformed" for a record that could not be read
  (Q3) -- apply off Windows too (accepted deviation 2, the owner, 2026-10-01;
  diagnostics only).

  | Platform    | `--break-lock`                                    |
  |-------------|---------------------------------------------------|
  | Windows     | atomic same-object break (Q1)                     |
  | non-Windows | unsupported; fail closed; manual recovery only    |

  Future path: if a non-Windows vault or workflow needs automatic recovery,
  design cooperative `flock` participation for acquire + release + break
  as a separate change.
- **Q3 -- by-path run.lock reads use one Windows reader (the owner,
  2026-10-01).** Evidence (scratch probe B): under a breaker's share-0
  handle, CPython `open()` fails `PermissionError` errno 13 with NO
  winerror, which the R4 predicate deliberately never retries. A LIVE
  holder's `RunLock.release` re-read therefore gave up and LEFT `run.lock`
  behind (a clean run turned into a stale lock because a refused
  `--break-lock` was judging it); `sibling_locks` reported the sibling
  "malformed / unprovable, delete by hand"; the acquirer's held message
  said the same. Ruling: one helper reads `run.lock` by path through
  `CreateFileW(GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE |
  FILE_SHARE_DELETE)`, so a sharing violation surfaces as WinError 32 and
  takes the existing bounded `_with_lock_retry`. Its callers on Windows:
  `sibling_locks` and the acquirer's holder read for `_held_message`. On
  Windows the release ownership re-read is NOT a Q3 read: it is the read
  through the Q6 exclusive handle, so the bytes compared are the object
  deleted (accepted deviation 1, the owner, 2026-10-01). Off Windows, release's
  re-read is the plain by-path read (Q2). Generic errno 13 is NOT
  reinterpreted as transient, and
  ordinary vault-page / state-file reads keep their R4 behavior unchanged
  -- the helper is for `run.lock` reads only.

  Sub-rule: an acquirer whose `os.link` met an existing `run.lock` (183)
  and whose holder read then finds it GONE (a breaker removed it in
  between) refuses this invocation (exit 2) with "the run lock was
  released or broken while this run was inspecting it -- re-run". It does
  not retry the acquisition: a collision stays visible to the operator and
  never silently becomes a queued run (ADR-0006, no waiting).

  Note: a genuine access denial through `CreateFileW` is WinError 5, which
  is in the R4 set (5/32/33) -- as everywhere else in R4 it is retried and
  then fails with `LockExhausted` naming WinError 5; it is never reported
  as the holder record being malformed.
- **Q4 -- a breaker that cannot get its exclusive handle (the owner,
  2026-10-01).** Its share-0 open fails WinError 32 while another process
  has the file open (another breaker judging, or a Q3 reader) and 5 once a
  delete is pending; both are retried under the existing
  `_with_lock_retry` (WinError 5 stays retryable, as everywhere in R4 --
  32 vs 5 is a diagnostic difference, not a retry-eligibility one). The
  outcome is decided only through the breaker's own handle:

  | After the retry                          | Outcome                                                        |
  |------------------------------------------|----------------------------------------------------------------|
  | `run.lock` is gone                       | exit 0, "No run lock ... (it was removed meanwhile)"            |
  | a new holder's record is there           | judged through the handle; alive/unprovable -> exit 2, not deleted |
  | the same stale record is still there     | judged stale through the handle and deleted (Q1)               |
  | retries exhausted (still 5/32/33)        | exit 2, "could not open the run lock exclusively; nothing broken", lock untouched |
  | opened, judged stale, disposition refused | exit 2, "judged stale through the exclusive handle, but the delete was refused (...) -- not broken", lock untouched; never retried or reopened |

  Every breaker acts only on the exact file object it has exclusively
  opened and judged, so of any number of racing breakers at most one
  deletes, and only the record it judged stale. Failing at once on the
  first 32/5 was rejected: a millisecond overlap with a Q3 reader would
  become needless operator churn.

  **Only the exclusive OPEN is retried (option A, the owner, 2026-10-01).**
  Once the share-0 handle is held: read once, judge once, attempt the
  disposition once. Disposition succeeds -> close -> the stale lock is
  removed. Disposition fails (`SetFileInformationByHandle` refused) ->
  close, NO reopen, NO re-read, NO re-judge, `run.lock` left untouched:
  `--break-lock` exits 2 with the delete-refused message above; release
  keeps the operation's exit code and prints its WARNING naming the
  refused delete (Q6). Rationale: while the breaker owns a share-0 handle
  no other process can cause a sharing collision on that object, so a
  disposition failure is persistent (read-only attribute, ACL); retrying
  bought no concurrency tolerance and only produced a misleading 6.3 s
  wait followed by a "could not open ... exclusively" diagnosis.
  Invariant: a stale verdict never survives across handles, because there
  is never a second handle after the judgment.

  Design history: implementation deviation 3 (a retryable disposition
  failure retried the whole open -> read -> judge, re-judging the newly
  opened object) was first accepted, then **superseded by review finding
  J1** -- adversarial review showed the whole-sequence retry was the wrong
  policy. A read-only stale `run.lock` (which 8ec44d0 broke by rename) is
  therefore refused and left untouched: an accepted fail-closed difference
  (K13); regression R170 does not clear attributes or widen the Q1 access mask.
- **Q5 -- the move-aside mechanism is removed (the owner, 2026-10-01).** The
  `os.rename` to `run.lock.broken-<hex>`, the compare-after-move, the
  link-back and the "could NOT be put back" path are deleted: Q1/Q4 make
  the tombstone obsolete, and a successful `--break-lock` leaves no
  artifact. The `.broken-` tolerance in the lock-only state-key check is
  removed too (the default state dir held zero `run.lock*` files when
  checked, and the new code cannot create one); an unexplained `.broken-*` file now falls through to the ordinary
  fail-closed state checks instead of being skipped as harmless. The
  `.tmp` tolerance stays: acquisition still writes `run.lock.<id>.tmp`. No
  doc or help text may describe `run.lock.broken-*` as a recovery artifact.
- **Q6 -- one Windows compare-and-delete primitive for break AND release
  (the owner, 2026-10-01).** Any Windows lock deletion happens only when its
  condition was checked against the exact file object then deleted. One
  helper: exclusive share-0 open (retried under `_with_lock_retry`,
  5/32/33, as Q4) -> read through the handle -> caller's predicate -> on
  true, `FileDispositionInfo` on that handle; on false, close and leave the
  file untouched; a refused disposition closes the handle and fails at
  once -- no reopen, no re-read, no re-judge (Q4, option A). Predicates: `--break-lock` = holder provably stale;
  `RunLock.release` = the bytes equal this run's own holder record, read
  through this handle -- on Windows that IS release's ownership read
  (accepted deviation 1, see Q3) (a
  mismatch keeps the existing "no longer holds this run's record -- left
  untouched" WARNING). A release that cannot open or delete still keeps the
  operation's exit code and prints the three-line WARNING. This removes the
  last read-path-then-unlink-whatever-is-there shape (release's residual was
  reachable only through a forbidden hand-delete of a live lock). Off
  Windows, release stays the current read-compare-unlink and `--break-lock`
  fails closed (Q2).
- **Q7 -- the K11 successor proves the invariant, not the old mechanism
  (the owner, 2026-10-01).** The ADR-0006-era K11 patched `os.rename` / `os.link`, which
  the new break never calls. The successor forces the race at the one point
  both implementations share: the stale judgment (`_holder_status`, called
  once, immediately before the break acts). K11: B1 runs in-process with
  `_holder_status` wrapped; inside the judgment it launches a real
  `--break-lock` B2 and a real `--apply` C (hold hook), waits on an
  observable barrier, then returns the real verdict. Old code: B2 removes
  the lock, C acquires, B1 displaces C -> RED. New code: B2/C contend
  against B1's share-0 handle; B1 deletes only the object it judged; B2
  reports "removed meanwhile", C "released or broken while inspecting --
  re-run". C then re-runs and is the sole holder; a D `--apply` and a B3
  `--break-lock` both refuse against it; after C releases, no lock is left.
  One test, red on 8ec44d0 and green on this change.

  Synchronization uses observable barriers, not sleeps: a test-only
  retry-arrival marker (written when a `run.lock` operation is retried on
  5/32/33, under the existing temp-vault-only test hook) or, for the old
  code, B2 having exited and `run.lock` holding C's record -- each with a
  bounded timeout that fails with a diagnostic naming the condition never
  reached, comfortably inside the 6.3 s retry budget.

  K12 (real subprocesses, new code only) uses a narrowly test-only
  `break_judged` hook that pauses a breaker after its judgment with the
  exclusive handle still open (test-only I/O inside the Q1 window, inert
  outside temp vaults): a live holder's release during a refused break
  succeeds and leaves no lock; the sibling check sees no false
  malformed/unprovable; an acquirer gets the accurate re-run refusal;
  exclusive-open exhaustion leaves the lock untouched on break (the
  existing fault hook, moved to the open) and keeps the run's exit code +
  the WARNING on release -- K12's release-exhaustion case uses a real
  paused breaker holding the handle past the retry budget, while K6 keeps
  the fault-hook version (accepted deviation 4, the owner, 2026-10-01); a
  `_break_supported()` seam (never `os.name`)
  simulates off-Windows: rc 2, bytes unchanged, path + guidance, acquire
  and release still work. K3 asserts the dir is empty after a break; K6's
  fault injection moves to the exclusive open; K1/K4 stay the fail-closed
  guards. VERIFY red controls against 8ec44d0: K11, the release race, the
  acquirer's false "malformed".

  K15 (Q4 option A, review finding J1): ONE injected disposition failure
  (test-only `dispose` fault kind, temp vaults only) on a stale lock ->
  exactly one exclusive open, one read, one judgment, no retry sleep;
  `--break-lock` rc 2 at once with the delete-refused text (never "could
  not open ... exclusively"), `run.lock` byte-unchanged; the same failure
  on release keeps the run's exit code, leaves the lock and prints the
  delete-refused WARNING. K13 is the real-OS counterpart (read-only
  attribute). Red against 8ec44d0.
- **Q8 -- the prohibition becomes a guarantee (the owner, 2026-10-01).** The
  four operational rule locations (`fix_wikilinks.py` docstring and
  `--break-lock` help, `_meta/fleet-conventions.md`, `scripts/README.md`)
  drop "never run --break-lock concurrently" for: "On Windows,
  `--break-lock` is safe alongside other `fix_wikilinks` runs: it judges
  and deletes the same `run.lock` object through one exclusive handle, so
  it cannot delete a live or replacement lock that it did not judge stale
  (regression R170, ADR-0008). Off Windows, automatic `--break-lock` refuses;
  remove `run.lock` manually only after confirming no `fix_wikilinks` run
  is live." The order `--break-lock` -> plain dry run stays; "a holder
  that cannot be proven gone -> ask the owner" stays; every `run.lock.broken-*`
  reference goes. ADR-0006 gets a Status-line-only amendment (its body is
  history); CONTEXT `stale lock` gets one glossary clause. Live-fleet
  carriers are the synced `fix_wikilinks.py` and `_meta/fleet-conventions.md`
  (fleet-doc hash / `--adopt-meta` gate applies at SYNC); README, CONTEXT
  and the ADRs reach fresh bootstraps only.

## Considered Options

- **Keep move-aside + compare + link-back (ADR-0006 as accepted under D1):**
  the residual this ADR closes.
- **`FileDispositionInfoEx` with POSIX semantics:** same observed behavior
  under share mode 0, more platform requirements (Q1).
- **Off-Windows: `flock` on the state dir taken by the breaker only** (the
  issue's sketch): an acquirer's `os.link` ignores it, so the race stays.
- **Off-Windows: cooperative `flock` for acquire + release + break:** a
  real equivalent, but a second locking mechanism in ADR-0006's acquire
  path, untestable here and used by no fleet vault (Q2; the future path).
- **Off-Windows: keep move-aside as a named residual:** rejected -- it
  knowingly preserves the exact race class this ADR exists to eliminate (Q2).
