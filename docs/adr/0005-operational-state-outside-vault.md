# A mutating tool's operational state lives outside the vault and outside any sync service

## Status

Accepted, 2026-09-30 (regression R153; acceptance is the merge of the change
that carries it). Reverses decision 4 of the legacy in-vault-state release
(pre-ADR-0005), which kept the
`fix_wikilinks.py --alias-only` manifest and backups inside the vault.

## Context

The legacy in-vault-state release shipped `fix_wikilinks.py --alias-only` with its dry-run manifest at
`_meta/alias-fix-manifest.json` and its apply log and page backups under
`<vault>/.alias-fix-backup/<run_id>/`. The design was reviewed, and tests
proved the files could not enter lint, the fixer or the sync.

What failed was the environment. On the first real full apply in a
cloud-synced (Dropbox) vault (several thousand links across ~300 files),
`os.replace` of `apply-log.json` failed with `WinError 5` twice in a row. The
apply log is re-saved three times per batch, and Dropbox briefly holds each new
version open to upload it.

Measurements:
- Rapid replaces 50 ms apart failed 13/40 inside Dropbox and 0/40 on a local
  path. Every lock cleared within 0.26 s.
- The real tool on a disposable Dropbox copy of the vault crashed 3 runs out of 3.
- Every earlier verification had run outside Dropbox, which is why none of
  them found this.

## Decision

The rollback data a mutating tool keeps about its own runs is **operational
state**: the manifest, the apply log, and the original bytes of every page it
rewrote. It is not vault content and not a disaster-recovery backup, and it
lives in a **state directory** outside the vault and outside any sync service.

- **Location.** The default is
  `%LOCALAPPDATA%\WIKIllm\alias-fix\<vault-folder-name>-<hash8>\`. The hash
  comes from the resolved, case- and separator-normalized vault path. Off
  Windows the base falls back to `~/.local/state` when `LOCALAPPDATA` is
  unset; on Windows an unset `LOCALAPPDATA` is refused, since any fallback
  would move the index of record.
  `--state-dir PATH` is the only override, deliberately a flag and not an
  environment variable. A state directory or `--manifest` inside the vault is
  refused.
- **Discovery.** The default directory is the index of record. It holds
  `state-dirs.json`, which lists every other state directory the vault has
  used, as canonical absolute paths with a last-used time.
  - Every run searches the default directory, the registered directories and
    the legacy in-vault location.
  - An unreachable registered directory makes `--apply` and `--restore` fail
    closed.
  - A pointer is removed only by `--forget-state-dir`, and only after the tool
    proves that directory holds no unfinished run.
  - Every run prints `STATE:` and, when it applies, `REGISTERED IN:`.
- **Identity.** State is keyed by path, so a moved vault does not inherit its
  old runs.
  - Same-folder-name state for another path is a warning heuristic, never
    identity. Settled runs there only warn; an unfinished run there blocks
    mutation.
  - Inheriting old state needs an explicit `--rebind-state OLD_PATH`, allowed
    only when the old path no longer exists. If it still exists, the new vault
    may be a copy, and nothing transfers.
  - The normal procedure is to settle or restore runs before moving a vault.
- **Migration.** `--migrate-legacy-state` is a dry run by default. With
  `--apply` it does these steps in order: copy each run, verify hashes and
  metadata, prove restore can discover the run in its new location, register
  it, then delete the in-vault copy.
  - A partial failure never deletes source state.
  - The stale manifest is deleted only after every legacy run has migrated.
  - Afterward the legacy scan returns zero.
  - Retention cleanup of settled runs is a separate, later job.

Moving the state does not by itself make the tool robust. Vault page writes
stay in Dropbox, so every write retries on the three known Windows lock codes
only (`WinError 5/32/33`), with backoff 0.1, 0.2, 0.4, 0.8, 1.6 and 3.2 s. If
the budget runs out, the batch stops cleanly with the restore command, and no
file is skipped.

The journal keeps its write-ahead shape: an intent save, an after-write save
and a committed save per batch, plus a final save. That shape is what makes
restore exact, and once the journal is local its write frequency no longer
matters.

## Considered options

- **Keep in-vault state and pause Dropbox for large applies.** It would finish
  one migration, but it would make correctness depend on an undocumented manual
  ritual, and it would recur on the next vault.
- **Keep in-vault state and add retries only.** This removes today's crash, but
  it still pushes hundreds of backup copies and a high-churn journal through
  Dropbox to every device, and it keeps a dry run mutating the vault.
- **A per-machine default on another drive** (`C:\example\wiki-state`).
  It is equally lock-free: a probe there failed 0/80. But the template would
  break on any machine without that drive. `--state-dir` covers the deliberate
  case instead.
- **An environment variable for the root.** Rejected: a command-rewriting shell hook can drop inline
  `VAR=x cmd` prefixes, and a missing variable falls back silently.

## Consequences

- A restore must run on the machine that did the apply. This is acceptable
  when every vault is maintained from one machine.
- A dry run no longer writes into the vault.
- Existing in-vault runs stay readable until
  `--migrate-legacy-state` moves them.
- Any future tool that keeps rollback state follows this ADR, not the legacy
  in-vault layout.
- Implementation finding, not a change to the decision: only `os.replace`
  carries a Windows lock code. CPython's `open()`, used for every page,
  backup and state READ, reports a real sharing violation (probed with
  `CreateFileW` share mode 0) as `PermissionError` errno 13 with no
  `winerror`. Under the frozen rule a locked read is therefore never
  retried. The tool fails safe instead: apply stops the batch (exit 3),
  restore stops at that page (`restore-read-failed`; every later page
  `restore-not-attempted`) with `RESTORE INCOMPLETE`,
  and the unfinished-run check counts the page live. This is also one
  mechanism that reproduces every observed fact of the 11-missed-files case
  (the first restore's full output was not captured, so the exact lock
  cannot be proven): the legacy in-vault-state tool labelled a locked restore read
  `changed-since-apply`, and a second restore picked the page up. An owner ruling (ruling 1) kept the
  predicate as frozen; retrying "errno 13 with no winerror" on reads is an
  optional follow-up hardening. Ruling 2 made restore stop at the first
  page whose retry is exhausted, matching apply (no skip-and-continue).
  Ruling 3 kept `--verify` out of the same-name blocker rule.
