#!/usr/bin/env python3
"""Sync this wiki's shared tooling from the WIKIllm template.

The template repo is the upstream for the *generic* tooling every wiki
instance shares: the scripts/ scanners and the .claude/skills/ingest skill.
Improvements land in the template first, then flow here. This script makes
that flow a one-command operation instead of hand-copying.

Default is a dry-run drift report (SHA-256 compare, no writes). --apply copies
template -> instance for every drifted or missing file.

  python scripts/sync_from_template.py            # report drift only
  python scripts/sync_from_template.py --apply    # pull template versions in

Template location: %WIKILLM_TEMPLATE% if set, else the default path below.

--apply refuses to copy unless the template source verifies (regression R38):
canonical path (or explicit --allow-noncanonical), checked out on a clean
main (over SYNC_SET paths), no merge/rebase in flight -- and it re-checks
the source hashes after copying so a template that changed mid-sync is
reported instead of silently producing a mixed-version vault.

NOT synced (instance-owned, by design): CLAUDE.md, wiki/, raw/, _meta/ content
(EXCEPT the fleet-shared docs listed in SYNC_SET: _meta/fleet-conventions.md
and _meta/book-scanning.md), and any topic-specific scripts (a macro monitor,
a decrypter, ...). Per-wiki
tracker pages are declared via `tracker_page: true` frontmatter precisely so
the synced scripts never need local edits. If you improve a SYNCED script
locally, back-port it to the template and re-sync — local edits here are
overwritten by the next --apply.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_TEMPLATE = str(Path.home() / ".wikillm" / "template")

# Relative paths (from repo root) of everything the template owns.
# File order here is not a safety guarantee (copies below are non-atomic);
# cross-script imports must be guarded at the importer, not by sequencing.
SYNC_SET = [
    "scripts/lint.py",
    "scripts/check_stale.py",
    "scripts/check_raw.py",
    "scripts/audit_claims.py",
    "scripts/suggest_anchors.py",
    "scripts/help.py",
    "scripts/suggest_links.py",
    "scripts/llm_suggest_links.py",
    "scripts/_wikilib.py",
    "scripts/fix_wikilinks.py",
    "scripts/extract_chapter.py",
    "scripts/graph_export.py",
    # Read-only readiness gate for the /wiki-maintenance skill.
    "scripts/maintenance_preflight.py",
    "scripts/sync_from_template.py",
    # Windows-only PowerShell (ebook page capture); harmless no-op elsewhere.
    "scripts/screen_capture.ps1",
    # Fleet-shared convention docs (regression R27, R28): the ONLY _meta/ files
    # that sync. Everything else under _meta/ stays instance-owned.
    "_meta/fleet-conventions.md",
    "_meta/book-scanning.md",
    ".claude/skills/ingest/SKILL.md",
    # Unattended maintenance pass; gated by scripts/maintenance_preflight.py.
    ".claude/skills/wiki-maintenance/SKILL.md",
    # Named multi-agent workflows (see .claude/workflows/README.md).
    ".claude/workflows/README.md",
    ".claude/workflows/anchor-backfill.js",
    ".claude/workflows/claim-audit.js",
    # Vendored third-party skills (kepano/obsidian-skills, MIT). Mostly not
    # hand-edited -- but deliberate template-side divergences EXIST and are
    # listed in .claude/skills/VENDORED.md "Local divergences". Re-vendoring by
    # folder replacement MUST re-apply that table or it silently drops them.
    ".claude/skills/obsidian-bases/SKILL.md",
    ".claude/skills/obsidian-bases/references/FUNCTIONS_REFERENCE.md",
    ".claude/skills/defuddle/SKILL.md",
]
# scripts/README.md is deliberately NOT synced: instances document their
# topic-specific scripts in it, so it's instance-owned. When a shared script's
# usage changes in the template, update the instance README section by hand.


def git_state(template: Path) -> dict:
    """Read the template repo's git state before trusting its working tree.

    The sync copies from the template's WORKING TREE, not from a git ref, so
    "on main" alone is not enough: main must also be clean over the SYNC_SET
    paths (modified AND untracked -- unrelated untracked junk elsewhere does
    not count) and no merge/rebase may be mid-flight. Degrades to
    {"git": False} on any git failure rather than raising; .git may be a
    worktree pointer FILE, hence rev-parse --absolute-git-dir. (regression R38)
    """
    state = {"git": False, "branch": None, "clean": True, "in_progress": False,
             "commit": None, "status": "", "at_origin": None, "behind": False}

    def run(*args: str) -> str | None:
        try:
            r = subprocess.run(["git", "-C", str(template), *args],
                               capture_output=True, text=True,
                               errors="replace", timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        return state
    status = run("status", "--porcelain", "--untracked-files=all", "--", *SYNC_SET)
    gitdir_out = run("rev-parse", "--absolute-git-dir")
    commit = run("rev-parse", "HEAD")  # full sha: stability compares use it
    if status is None or gitdir_out is None or commit is None:
        return state
    # Reviewed-provenance check (Codex rounds 8+10): clean local main is NOT
    # enough (a direct-to-main commit would sync fleet-wide unreviewed), and
    # an ancestor of origin/main is not enough either (a stale un-pulled
    # template would DOWNGRADE vaults that already synced newer files). HEAD
    # must EQUAL origin/main as of the last fetch/pull. None = unverifiable.
    at_origin = None
    behind = False
    origin_out = run("rev-parse", "refs/remotes/origin/main")
    if origin_out is not None:
        at_origin = (origin_out.strip() == commit.strip())
        if not at_origin:
            try:
                r = subprocess.run(["git", "-C", str(template), "merge-base",
                                    "--is-ancestor", "HEAD", "refs/remotes/origin/main"],
                                   capture_output=True, text=True, errors="replace", timeout=5)
                behind = (r.returncode == 0)
            except (OSError, subprocess.SubprocessError):
                pass
    gitdir = Path(gitdir_out.strip())
    state.update(
        git=True,
        branch=branch.strip(),
        clean=(status.strip() == ""),
        status=status.strip(),
        in_progress=((gitdir / "MERGE_HEAD").exists()
                     or (gitdir / "rebase-merge").is_dir()
                     or (gitdir / "rebase-apply").is_dir()),
        commit=commit.strip(),
        at_origin=at_origin,
        behind=behind,
    )
    return state


def verify_source(template: Path, allow_noncanonical: bool, state: dict) -> list[str]:
    """Refusal reasons for using this template as a sync source (empty = OK).

    regression R38: 'it will be reviewed before merge' protects nothing here --
    the working tree itself must provably be the reviewed thing: the
    canonical path, on main, every SYNC_SET path committed, no merge/rebase
    half-applied. Check order: in_progress before clean, because a mid-merge
    tree is always dirty and the merge message is the more actionable one.
    """
    problems: list[str] = []
    if template.resolve() != Path(DEFAULT_TEMPLATE).resolve() and not allow_noncanonical:
        problems.append(
            f"ERROR: template source {template} is not the canonical template\n"
            f"({DEFAULT_TEMPLATE}). A WIKILLM_TEMPLATE/--template override can point\n"
            "--apply at an unreviewed worktree or stale clone. Pass --allow-noncanonical\n"
            "if this is deliberate.")
    if not state["git"]:
        problems.append(
            f"ERROR: cannot read git state at {template} (git missing, not a repo, or a\n"
            "git call failed). Without it the on-main / clean-tree safety checks cannot\n"
            f"run, so the sync is blocked. Check: git -C \"{template}\" status")
        return problems
    if state["branch"] != "main":
        problems.append(
            f"ERROR: template at {template} is on branch '{state['branch']}', not 'main'.\n"
            "The sync copies from the template's WORKING TREE, so an unmerged review\n"
            "branch would go live to this vault. In the template: git checkout main\n"
            "(branch work belongs in a git worktree) -- then re-run.")
    if state["in_progress"]:
        problems.append(
            f"ERROR: template at {template} has a merge or rebase in progress. Its\n"
            "working tree is mid-operation, not a reviewed state to copy from. Finish\n"
            "it, or git merge --abort / git rebase --abort -- then re-run.")
    elif not state["clean"]:
        problems.append(
            f"ERROR: template at {template} has uncommitted changes in synced files:\n"
            f"{state['status']}\n"
            "These are unreviewed working-tree edits and --apply would copy them into\n"
            "this vault. Commit them via the PR flow, or stash/discard -- then re-run.")
    if state["at_origin"] is None:
        problems.append(
            f"ERROR: cannot verify that the template's HEAD is on origin/main (no such\n"
            "remote ref readable). A clean local main is not reviewed provenance -- a\n"
            "direct-to-main commit would sync fleet-wide unreviewed. Fetch origin (or fix\n"
            "the remote) so HEAD can be verified, then re-run.")
    elif not state["at_origin"]:
        if state["behind"]:
            problems.append(
                f"ERROR: the template's HEAD ({state['commit'][:12]}) is BEHIND origin/main.\n"
                "Syncing from a stale template would DOWNGRADE vaults that already carry\n"
                "newer files. In the template: git pull -- then re-run.")
        else:
            problems.append(
                f"ERROR: the template's HEAD ({state['commit'][:12]}) is NOT origin/main --\n"
                "local-only commits on main are unreviewed content. Push them through the PR\n"
                "flow (or git reset main to origin/main), then re-run.")
    return problems


def sha256(p: Path) -> str:
    """Hash with line endings normalized to LF. Every file in SYNC_SET is
    text, and on Windows a git checkout can flip the template copy to CRLF
    (core.autocrlf) with no semantic change; a raw-byte compare then reports
    DRIFTED for identical content (regression R10), which both trains sessions to
    ignore DRIFTED and makes --apply churn mtimes across the Dropbox vaults."""
    data = p.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync shared wiki tooling from the WIKIllm template.")
    ap.add_argument("--apply", action="store_true", help="Copy template versions over drifted/missing files (default: report only).")
    ap.add_argument("--template", default=os.environ.get("WIKILLM_TEMPLATE", DEFAULT_TEMPLATE),
                    help="Template repo path (env WIKILLM_TEMPLATE overrides; flag overrides both).")
    ap.add_argument("--allow-noncanonical", action="store_true",
                    help="Permit syncing from a template path other than the canonical one (deliberate other-machine or test use; refused by default, regression R38).")
    ap.add_argument("--adopt-meta", action="store_true",
                    help="Overwrite _meta/ fleet docs that differ locally. Refused by default: a differing _meta/ doc is either pre-adoption instance content or a local edit worth back-porting -- diff it first (regression R27).")
    args = ap.parse_args()

    template = Path(args.template)
    instance = Path(__file__).resolve().parents[1]
    if not (template / "scripts" / "check_raw.py").exists():
        print(f"ERROR: template not found at {template} (set WIKILLM_TEMPLATE or pass --template).", file=sys.stderr)
        return 1
    if template.resolve() == instance.resolve():
        print("This IS the template — nothing to sync.")
        return 0

    # The gate (regression R38): a dry-run report is harmless, so problems only
    # WARN there -- but --apply writes into the vault, so problems BLOCK it.
    # state0 + src_hash0 are the gate-time snapshot; apply_changes re-checks
    # both endpoints so a template mutated mid-run is detected, not installed.
    state0 = git_state(template)
    problems = verify_source(template, args.allow_noncanonical, state0)
    src_hash0 = {rel: sha256(template / rel) for rel in SYNC_SET if (template / rel).exists()}
    provenance = (f"{state0.get('branch')}@{(state0.get('commit') or '?')[:12]}"
                  if state0.get("git") else "git state unreadable")
    print(f"template: {template} ({provenance})\ninstance: {instance}\n")
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
            print(file=sys.stderr)
        if args.apply:
            print("REFUSED: --apply blocked by the checks above; nothing was copied.", file=sys.stderr)
            return 1
        print("(dry run: reporting anyway, but --apply would refuse)", file=sys.stderr)
    in_sync, drifted, missing, no_upstream = [], [], [], []
    dst_hash0: dict = {}
    for rel in SYNC_SET:
        dst = instance / rel
        # Classify against the gate-time snapshot, not a fresh read -- a
        # template mutated between gate and scan must not become the baseline.
        # Destination hashes are snapshotted too: a dst edited DURING the sync
        # (editor, cloud client) must be detected, never parked-and-discarded.
        if rel not in src_hash0:
            no_upstream.append(rel)
        elif not dst.exists():
            missing.append(rel)
            dst_hash0[rel] = None
        elif src_hash0[rel] == sha256(dst):
            in_sync.append(rel)
        else:
            drifted.append(rel)
            dst_hash0[rel] = sha256(dst)

    print(f"in sync : {len(in_sync)}")
    for rel in drifted:
        print(f"DRIFTED : {rel}")
    for rel in missing:
        print(f"MISSING : {rel}")
    for rel in no_upstream:
        print(f"(not in template — skipped): {rel}")

    if not (drifted or missing):
        print("\nNothing to do.")
        return 0

    if not args.apply:
        print("\nDry run — re-run with --apply to pull the template versions in.")
        print("(If a DRIFTED file's local changes are improvements, back-port them to the template FIRST.)")
        return 0

    # Adoption guard (regression R27 + Codex): _meta/ was instance-owned before
    # fleet docs existed, so a DIFFERING file at a fleet-doc path is either
    # pre-adoption local content (first sync) or a deliberate local edit --
    # both deserve a diff and a decision, not a silent overwrite. MISSING
    # fleet docs copy normally; only drift needs --adopt-meta.
    meta_conflicts = [rel for rel in drifted if rel.startswith("_meta/")]
    if meta_conflicts and not args.adopt_meta:
        print("\nERROR: fleet doc(s) differ from the template in this instance:", file=sys.stderr)
        for rel in meta_conflicts:
            print(f"  {rel}", file=sys.stderr)
        print("Diff each against the template; back-port anything worth keeping via the PR\n"
              "flow, then re-run with --adopt-meta to accept the template version.\n"
              "Nothing was copied.", file=sys.stderr)
        return 1
    rc = apply_changes(template, instance, drifted + missing, state0, src_hash0, dst_hash0=dst_hash0)
    if rc == 0 and "scripts/sync_from_template.py" in drifted:
        # SYNC_SET is bound at import time: entries the template added since
        # this copy loaded were invisible to this run.
        print("NOTE: the sync script itself was updated — run --apply once "
              "more to pick up any sync-set entries new in the template.")
    return rc


def apply_changes(template: Path, instance: Path, rels: list[str],
                  state0: dict, src_hash0: dict, mutate_hook=None,
                  dst_hash0: dict | None = None) -> int:
    """Stage, re-verify, then install -- never leave a mixed-version vault.

    TOCTOU guard (regression R38, Codex finding): the gate and snapshot ran
    earlier; a concurrent checkout/pull/merge in the template between then
    and now must be DETECTED, not installed. So: copy every source to a
    .sync-tmp beside its destination, then re-read the template's git state
    and re-hash ALL gate-time SYNC_SET sources (not just the copied ones --
    files in-sync at gate time changing is the same race); only if both
    endpoints agree do the staged copies replace the destinations. On any
    mismatch the staged copies are discarded and the vault is untouched.
    mutate_hook exists ONLY as a test seam (not reachable from the CLI): the
    regression harness injects a template mutation right here to prove the
    race is caught.

    Concurrency (Codex round-6 finding): two overlapping --apply runs in one
    vault would fight over the same .sync-tmp/.sync-bak names and could
    destroy each other's rollback copies. An exclusive lock file at the vault
    root is held for the whole transaction; a crashed run leaves it behind,
    and the refusal message says how to clear it.
    """
    lock = instance / ".sync-lock"
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(f"\nERROR: another sync appears to be running in this vault ({lock} exists).",
              file=sys.stderr)
        print("If you are sure none is (a crashed run leaves the lock behind), delete that\n"
              "file and re-run --apply. Nothing was copied.", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"\nERROR: could not take the sync lock at {lock} ({exc}). Nothing was copied.",
              file=sys.stderr)
        return 1
    try:
        os.write(fd, f"pid {os.getpid()}\n".encode("utf-8"))
        os.close(fd)
        if dst_hash0 is None:  # direct callers (tests): snapshot on entry
            dst_hash0 = {rel: (sha256(instance / rel) if (instance / rel).exists() else None)
                         for rel in rels}
        return _apply_locked(template, instance, rels, state0, src_hash0, dst_hash0, mutate_hook)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _apply_locked(template: Path, instance: Path, rels: list[str],
                  state0: dict, src_hash0: dict, dst_hash0: dict,
                  mutate_hook=None) -> int:
    """The transaction body of apply_changes; runs under the vault lock."""
    # Leftover triage (Codex rounds 4+5): a .sync-bak surviving a failed
    # rollback may be the ONLY copy of a pre-sync file -- never overwrite,
    # refuse until a human recovers it. A stale .sync-tmp is a disposable
    # copy from a crashed staging pass -- remove it and continue, so a
    # transient failure does not permanently block syncing.
    precious = []
    for rel in rels:
        dst = instance / rel
        bak = dst.with_name(dst.name + ".sync-bak")
        if bak.exists():
            precious.append(bak)
        tmp = dst.with_name(dst.name + ".sync-tmp")
        if tmp.exists():
            try:
                tmp.unlink()
                print(f"note: removed stale staging file from a crashed run: {tmp}")
            except OSError:
                precious.append(tmp)
    if precious:
        print("\nERROR: leftover artifacts from an earlier failed sync:", file=sys.stderr)
        for p in precious:
            print(f"  {p}", file=sys.stderr)
        print("A .sync-bak may be the only surviving copy of a pre-sync file. Inspect and\n"
              "recover (or deliberately delete) each, then re-run --apply. Nothing was copied.",
              file=sys.stderr)
        return 1
    # Staging is fallible too (disk full, cloud-sync lock): clean up our own
    # partial staging on failure instead of stranding it for the next run.
    staged = []
    tmp = None  # tracked so the except can clean the in-flight copy too
    try:
        for rel in rels:
            src, dst = template / rel, instance / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".sync-tmp")
            shutil.copy2(src, tmp)
            # The STAGED bytes are what installs, so they -- not the live
            # template -- must match the gate-time snapshot (Codex round-7:
            # a source flipped during copy2 and restored before the endpoint
            # check would otherwise stage the transient version undetected).
            if sha256(tmp) != src_hash0.get(rel):
                raise OSError(f"staged copy of {rel} does not match the verified snapshot "
                              "(template changed mid-copy)")
            staged.append((rel, tmp, dst))
    except OSError as exc:
        uncleaned = []
        pending = [t for _, t, _ in staged]
        if tmp is not None and tmp not in pending:
            pending.append(tmp)  # the in-flight copy that raised
        for t in pending:
            try:
                if t.exists():
                    t.unlink()
            except OSError:
                uncleaned.append(t)
        print(f"\nERROR: staging failed ({exc}); destinations were not touched.", file=sys.stderr)
        if uncleaned:
            print("These staging files could not be removed -- delete by hand:", file=sys.stderr)
            for p in uncleaned:
                print(f"  {p}", file=sys.stderr)
        print("Re-run --apply once the underlying problem (disk, lock, permissions) clears.",
              file=sys.stderr)
        return 1
    if mutate_hook is not None:
        mutate_hook()
    state1 = git_state(template)
    # Endpoint re-check mirrors the FULL gate (Codex round-11): a concurrent
    # fetch can advance origin/main mid-run, making this snapshot stale even
    # though HEAD and the tree never moved.
    stable = (state1["git"] and state1["branch"] == state0.get("branch")
              and state1["commit"] == state0.get("commit") and state1["clean"]
              and state1["at_origin"] is True and not state1["in_progress"])
    changed = [rel for rel in src_hash0
               if not (template / rel).exists() or sha256(template / rel) != src_hash0[rel]]
    # Destinations must ALSO be where classification left them (Codex round-12
    # finding): a dst edited during the sync (editor, Obsidian, cloud client)
    # would otherwise be parked to .sync-bak and the backup deleted on
    # success -- silently destroying user work. The vault lock only excludes
    # other syncs, not editors.
    dst_moved = []
    for rel in rels:
        dst = instance / rel
        actual = sha256(dst) if dst.exists() else None
        if actual != dst_hash0.get(rel):
            dst_moved.append(rel)
    if dst_moved:
        for _, tmp, _ in staged:
            try:
                tmp.unlink()
            except OSError:
                pass
        print("\nERROR: destination files changed while this sync was running:", file=sys.stderr)
        for rel in dst_moved:
            print(f"  {rel}", file=sys.stderr)
        print("Something (an editor, Obsidian, a cloud client) is writing these files.\n"
              "Nothing was touched. Let it settle, then re-run --apply.", file=sys.stderr)
        return 1
    if not stable or changed:
        for _, tmp, _ in staged:
            try:
                tmp.unlink()
            except OSError:
                pass
        print("\nERROR: the template changed while this sync was running:", file=sys.stderr)
        if not stable:
            print(f"  git state moved: {state0.get('branch')}@{state0.get('commit')} -> "
                  f"{state1.get('branch')}@{state1.get('commit')} (clean={state1.get('clean')})",
                  file=sys.stderr)
        for rel in changed:
            print(f"  content changed: {rel}", file=sys.stderr)
        print("Nothing was installed (staged copies discarded). Wait for the template\n"
              "operation to finish, then re-run --apply.", file=sys.stderr)
        return 1
    # Install as a transaction (Codex round-2 finding): a failed os.replace
    # midway (file locked, permissions) must not strand a half-updated vault.
    # Every existing destination is parked as .sync-bak first; on ANY failure
    # every already-replaced file is restored before reporting.
    # Bookkeeping rule (Codex round-3 finding): a destination is recorded as
    # disturbed the moment its backup move succeeds -- BEFORE the install --
    # so a failure between the two still rolls it back. Rollback tolerates a
    # destination that was parked but never reinstalled.
    replaced = []  # (rel, dst, backup_or_None) -- appended when dst is disturbed
    try:
        for rel, tmp, dst in staged:
            backup = None
            if dst.exists():
                backup = dst.with_name(dst.name + ".sync-bak")
                os.replace(dst, backup)
                # Park-verify (Codex round-13): an edit landing in the window
                # between the endpoint check and this park would be silently
                # discarded on success. The parked bytes must BE the validated
                # bytes; otherwise give the edit back and abort.
                if sha256(backup) != dst_hash0.get(rel):
                    os.replace(backup, dst)
                    raise OSError(f"{rel} changed between validation and install "
                                  "(concurrent editor or cloud client)")
            replaced.append((rel, dst, backup))
            # A writer recreating dst while it is parked would be overwritten
            # by the install; refuse if the path reappeared. (The check-to-
            # replace instant that remains is consciously accepted: closing
            # it needs OS-level locking a stdlib sync script cannot provide.)
            if backup is not None and dst.exists():
                raise OSError(f"{rel} was recreated by another writer during install")
            os.replace(tmp, dst)
    except OSError as exc:
        restore_failed = []
        for rel, dst, backup in reversed(replaced):
            try:
                # Never clobber a destination that was recreated or edited
                # while we were failing: if its current bytes are neither
                # missing nor the template content we installed, a concurrent
                # writer owns it now -- keep it AND keep the backup.
                cur = sha256(dst) if dst.exists() else None
                if cur is not None and cur != src_hash0.get(rel):
                    restore_failed.append(rel)
                    continue
                if backup is not None:
                    os.replace(backup, dst)
                elif dst.exists():
                    dst.unlink()
            except OSError:
                restore_failed.append(rel)
        for _, tmp, _ in staged:
            try:
                tmp.unlink()
            except OSError:
                pass
        print(f"\nERROR: installing files failed mid-way ({exc}).", file=sys.stderr)
        if restore_failed:
            print("Rollback did NOT touch these files (restore failed, or a concurrent edit\n"
                  "owns them) -- inspect each, recover from its .sync-bak neighbor if present,\n"
                  "then re-run --apply:", file=sys.stderr)
            for rel in restore_failed:
                print(f"  {rel}", file=sys.stderr)
        else:
            print("All destinations were rolled back; the vault is unchanged. Close whatever\n"
                  "holds the file open (editor, Obsidian, Dropbox sync), then re-run --apply.",
                  file=sys.stderr)
        return 1
    # Cleanup failures are NOT silent (Codex round-9 finding): a surviving
    # .sync-bak blocks the next run's leftover check, so reporting success
    # here while it exists turns a transient lock into an unexplained outage.
    cleanup_failed = []
    for rel, dst, backup in replaced:
        print(f"synced  : {rel}")
        if backup is not None:
            try:
                backup.unlink()
            except OSError:
                cleanup_failed.append(backup)
    if cleanup_failed:
        print("\nWARNING: every file INSTALLED correctly, but these rollback backups could\n"
              "not be removed (file lock, antivirus, cloud-sync?). The next --apply will\n"
              "REFUSE to run while they exist -- delete them by hand:", file=sys.stderr)
        for p in cleanup_failed:
            print(f"  {p}", file=sys.stderr)
        return 1
    print(f"\nDone — {len(rels)} file(s) updated from template.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
