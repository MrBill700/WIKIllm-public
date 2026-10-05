#!/usr/bin/env python3
"""One-shot lint pass for the LLM wiki.

Builds an inventory of every .md under wiki/ and _meta/ plus CLAUDE.md, parses
wikilinks ([[...]]), and reports:
  - broken links (target file doesn't exist by basename match; attachment
    embeds like ![[chart.png]] are resolved against the non-.md asset inventory).
    Targets containing "../" that fail basename match are verified against the
    filesystem relative to the linking file; those that exist on disk are
    reported as cross-vault links instead of broken.
  - relative-depth links (regression R76): every target with a "../" segment is
    also checked on disk relative to the linking file, BEFORE and independent
    of the basename match above -- a wrong-depth path whose last segment names
    a local page is otherwise rescued by basename and seen by nothing. Purely
    additive: it changes no other section's count.
  - alias-only links (regression R151): targets that resolve only through a
    page's frontmatter `aliases:`. Obsidian does not follow these (they are
    grey; clicking creates a new empty note), so they are reported in their
    own section right after BROKEN LINKS and earn NO inbound credit -- their
    target can surface as an orphan. When a basename file and alias holders
    share a name, only the basename file is credited; a name that is the
    basename of any .md outside dot-directories (raw/, templates/ too) is
    canonical, never alias-only. Fix with
    scripts/fix_wikilinks.py ([[slug|Display]] rewrites).
  - orphan pages (zero inbound wikilinks)
  - ghost/placeholder links ([[date]], [[published]] from the Obsidian clipper)
  - inbound-count hubs (most-linked pages)
  - locator hygiene: source-citation anchor coverage + broken heading anchors
    (delegated to audit_claims/suggest_anchors -- surfaced here so a coverage
    regression from an ingest is caught at the very next close-out, not at the
    next quarterly audit; added 2026-07-28 from a backfill-session lesson)

Run: python scripts/lint.py [--summary]

--summary prints the section counts only (no per-item listings). Use it at
session close-out instead of truncating the output with `| head` or
`| Select-Object -First N` — early-terminated pipes kill the interpreter and
report a false failure (exit 255 under PowerShell). If a summary count is
nonzero, re-run without the flag for the detail.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
import io

# Wiki instances are cloud-synced folders (Dropbox). The sibling imports in the
# locator-hygiene block below (audit_claims, suggest_anchors) would otherwise
# drop scripts/__pycache__/*.pyc into the vault every time a session runs the
# documented `python scripts/lint.py --summary`, re-syncing derived files to
# every device forever -- and a .pyc restored across machines can shadow a
# newer .py. The flag is process-global and consulted at import-write time, so
# setting it at this entry point covers every sibling reached from it
# (regression R83; maintenance_preflight.py guards its own subprocesses with -B).
sys.dont_write_bytecode = True

# The wikilink grammar and the resolution order live in scripts/_wikilib.py,
# not here: lint is the pass whose link numbers the fleet trusts, and an
# export nobody imports is how a second, drifting copy gets born (regression R80).
# Deliberately NOT guarded with a fallback parser -- a fallback would BE the
# second copy. A partial sync leaving an old _wikilib.py fails loudly instead.
# SyntaxError is caught beside ImportError so a truncated or corrupt copy gets
# the same one-line remedy: maintenance_preflight surfaces _first_line(stderr)
# as its blocker, and a raw traceback would make that blocker read literally
# "Traceback (most recent call last):". NOT caught, and not catchable here: a
# copy that still exports these three names with changed BEHAVIOUR imports
# cleanly -- keeping the library in step is the sync's job, not lint's.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _wikilib import (iter_wikilinks, link_kind, lint_inventory, lint_link_kind,
                          resolve)
except (ImportError, SyntaxError) as e:
    # Remedy BEFORE the exception text, deliberately: maintenance_preflight's
    # _first_line truncates at 160 chars, and a SyntaxError from a truncated
    # copy is long enough to push a trailing remedy off the end of the
    # operator-facing blocker (caught by test_maintenance_preflight case 7b).
    sys.exit(f"lint.py needs an importable scripts/_wikilib.py (regression R80) -- "
             f"run python scripts/sync_from_template.py --apply. Import failed: {e}")


def relative_depth_miss(target: str, from_path: str, root: str = ".") -> bool | None:
    """regression R76 on-disk check for a target that spells out a `../` path.

    Returns None when the target has no `../` segment (not this check's
    business -- measured 2026-09-19, triggering on any `/` flagged thousands of
    ordinary vault-relative links) or when the path exists on disk, as written
    or with `.md` appended, relative to the citing file. Otherwise returns
    whether the missing path escapes the vault root (True) or stays inside it
    (False). `target` is bare: iter_wikilinks has already split off #heading,
    #^block and |display. A directory does not count as existing -- a link names a file.
    """
    if not (target.startswith("../") or "/../" in target):
        return None
    path = os.path.normpath(os.path.join(root, os.path.dirname(from_path), target))
    if os.path.isfile(path) or os.path.isfile(path + ".md"):
        return None
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    except ValueError:  # different drive on Windows: outside the vault
        return True
    return rel == ".." or rel.startswith(".." + os.sep)


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="Wiki link lint (advisory; exits 0 once it has started -- "
                    "the one nonzero exit is a scripts/_wikilib.py that is "
                    "missing, corrupt, or too old to export the link surface).")
    ap.add_argument("--summary", action="store_true",
                    help="Counts only, no per-item listings (close-out friendly).")
    args = ap.parse_args()

    # The inventory walk (files, texts, assets, basename/alias index, vault
    # basenames) and the per-link verdict live in _wikilib (lint_inventory /
    # lint_link_kind), moved there unchanged for regression R151 so fix_wikilinks
    # can predict this pass's verdict on every link it rewrites. Notes that
    # used to sit here, kept with the code in _wikilib:
    # - asset_names: non-.md files (images, CSVs, PDFs) that embeds like
    #   ![[chart.png]] may target, raw/ included -- resolved by basename.
    # - vault_bn: every .md basename outside dot-directories (raw/,
    #   templates/, any top-level folder). Obsidian resolves [[name]] against
    #   all of them, so such a name is not alias-only (regression R151 review).
    # - _meta/ auto_generated pages are dropped via lint_is_auto_generated,
    #   NOT _wikilib.fm_flag: that copy reads text[:text.find("\n---", 3)],
    #   which on an unclosed `---` is the whole body, and moving it would move
    #   ALL FILES / ORPHANS (regression R80 parity). Pages are read utf-8-sig, so
    #   a BOM-prefixed _meta/ auto_generated page IS dropped (regression R151
    #   review 3-R3-L3; pinned by test_lint_alias_only BOM1).
    inv = lint_inventory(".")
    files: list[str] = inv["files"]
    texts: dict[str, str] = inv["texts"]
    asset_names: set[str] = inv["asset_names"]
    basename = inv["index"]
    vault_bn = inv["vault_bn"]

    inbound: collections.Counter = collections.Counter()
    broken: list[tuple[str, str]] = []
    alias_only: list[tuple[str, str]] = []  # (citing file, target) -- regression R151
    crossvault: list[tuple[str, str]] = []
    ghost: list[tuple[str, str]] = []
    # (citing file, target, escapes vault?, basename-rescued?) -- regression R76.
    reldepth: list[tuple[str, str, bool, bool]] = []

    for p in files:
        text = texts[p]
        if not text:
            continue
        # iter_wikilinks strips fenced and inline code spans (single AND
        # double backtick) before parsing: a backticked [[example]] quoted in
        # a log entry is documentation, not an edge.
        for link in iter_wikilinks(text):
            target = link.target
            # regression R76: checked BEFORE, and independent of, the resolution
            # ladder below -- the ladder's basename fallback rescues a
            # wrong-depth path whose last segment names any local page, so a
            # link that spells out a relative path is checked on disk here.
            miss = relative_depth_miss(target, p)
            if miss is not None:
                ext = os.path.splitext(os.path.basename(target))[1]
                if ext and ext.lower() != ".md":
                    rescued = os.path.basename(target) in asset_names
                else:
                    # An alias-only match is not a rescue: Obsidian opens
                    # nothing for it (it is counted in ALIAS-ONLY instead).
                    st, cands = resolve(target, p, basename)
                    rescued = (st == "resolved"
                               and link_kind(target, cands, vault_bn) == "canonical")
                reldepth.append((p, target, miss, rescued))
            # The verdict ladder (ghost -> '#' -> attachment extension ->
            # resolve -> canonical / alias-only -> cross-vault -> broken) is
            # _wikilib.lint_link_kind. regression R151: only a basename hit is an
            # edge Obsidian can follow; a target matched solely via
            # frontmatter aliases is grey (click = new empty note), earns no
            # inbound credit and is reported in ALIAS-ONLY. When a basename
            # file and alias holders share the key, only the basename file(s)
            # are credited (partial fix for R150's double-credit). A canonical
            # off-inventory file (raw/, templates/) credits nothing.
            kind, credit = lint_link_kind(target, p, inv)
            if kind == "ghost":
                ghost.append((p, target))
            elif kind == "canonical":
                for c in credit:
                    inbound[c] += 1
            elif kind == "alias-only":
                alias_only.append((p, target))
            elif kind == "cross-vault":
                # Cross-vault targets do not participate in inbound/orphan math.
                crossvault.append((p, target))
            elif kind == "broken":
                broken.append((p, target))
            # "attachment" / "skip": assets and '#' targets take no part in
            # inbound/orphan math.

    # wiki/data/ notes are Bases datapoints -- table rows surfaced by a .base
    # embed, not graph pages. Zero inbound links is their normal state, so
    # reporting them as orphans is pure noise. Their own outbound links (back
    # to the analysis page, to sources) still count in inbound math above.
    orphans = [p for p in files
               if inbound[p] == 0 and not p.replace("\\", "/").startswith("wiki/data/")]

    print(f"=== ALL FILES === {len(files)}")
    print()
    by_target: dict[str, list[str]] = {}
    for src, tgt in broken:
        by_target.setdefault(tgt, []).append(src)
    print(f"=== BROKEN LINKS === {len(broken)} ({len(by_target)} distinct targets)")
    if not args.summary:
        for tgt, srcs in sorted(by_target.items(), key=lambda kv: -len(kv[1]))[:200]:
            where = srcs[0] if len(srcs) == 1 else f"{srcs[0]} +{len(srcs) - 1} more"
            print(f"  x{len(srcs):<4} [[{tgt}]]  ({where})")
    print()
    # regression R151. Not a subset of BROKEN (resolve() found the intended page)
    # and not a healthy link either (Obsidian cannot follow it). Remedy is
    # scripts/fix_wikilinks.py: rewrite to [[slug|Display]] where unique.
    # wiki/log.md hits are counted here too; the fixer leaves that
    # append-only file alone, so they are a documented residue.
    ao_by_target: dict[str, list[str]] = {}
    for src, tgt in alias_only:
        ao_by_target.setdefault(tgt, []).append(src)
    print(f"=== ALIAS-ONLY LINKS (resolve only via frontmatter aliases -- grey in Obsidian,"
          f" regression R151) === {len(alias_only)} ({len(ao_by_target)} distinct targets)")
    if not args.summary:
        for tgt, srcs in sorted(ao_by_target.items(), key=lambda kv: -len(kv[1]))[:200]:
            where = srcs[0] if len(srcs) == 1 else f"{srcs[0]} +{len(srcs) - 1} more"
            print(f"  x{len(srcs):<4} [[{tgt}]]  ({where})")
        if len(ao_by_target) > 200:
            print(f"  ... {len(ao_by_target) - 200} more distinct targets not shown")
    print()
    print(f"=== CROSS-VAULT LINKS (basename-unresolvable, verified on disk) === {len(crossvault)}")
    if not args.summary:
        for src, tgt in crossvault:
            print(f"  [[{tgt}]]  ({src})")
    print()
    # regression R76. Additive: nothing here feeds BROKEN / CROSS-VAULT / inbound.
    # "rescued" = the basename fallback above resolved it to a page Obsidian
    # opens, so it appears in no other section. An alias-only match is NOT a
    # rescue (regression R151): it is marked "also broken" here and counted in
    # ALIAS-ONLY; the remaining non-rescued ones are ALSO in BROKEN LINKS. Grouped by
    # (citing file, target), not by target alone: [[../index]] is right from
    # wiki/entities/x.md and wrong from wiki/log.md.
    rd_escape = sum(1 for r in reldepth if r[2])
    rd_rescued = sum(1 for r in reldepth if r[3])
    print(f"=== RELATIVE-DEPTH LINKS (../ that resolve to nothing on disk) === {len(reldepth)}"
          f" (escape vault {rd_escape}, inside vault {len(reldepth) - rd_escape};"
          f" basename-rescued {rd_rescued}, also broken {len(reldepth) - rd_rescued})")
    if not args.summary:
        rd_groups = collections.Counter(reldepth)
        for (src, tgt, esc, resc), n in sorted(rd_groups.items(),
                                               key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))[:200]:
            where = "escapes vault" if esc else "inside vault"
            how = "basename-rescued" if resc else "also broken"
            print(f"  x{n:<4} [[{tgt}]]  ({src})  [{where}; {how}]")
        if len(rd_groups) > 200:
            print(f"  ... {len(rd_groups) - 200} more (file, target) groups not shown")
    print()
    print(f"=== ORPHANS (zero inbound wikilinks) === {len(orphans)}")
    if not args.summary:
        for p in orphans:
            print(f"  {p}")
    print()
    print(f"=== GHOST/PLACEHOLDER LINKS === {len(ghost)}")
    if not args.summary:
        g_by_file = collections.Counter([g[0] for g in ghost])
        for src, count in g_by_file.most_common():
            print(f"  {src}  x{count}")
        print()
        print("=== INBOUND-COUNT TOP HUBS ===")
        for p, c in inbound.most_common(20):
            print(f"  {c:>3}  {p}")
    print()
    # Locator hygiene: the anchor stack knows two things this pass can't see --
    # what fraction of source-citing claims carry a #heading locator, and
    # whether existing anchors still name real headings (LINK_RE above strips
    # anchors before resolving, so a broken anchor passes the link check).
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import suggest_anchors as sa
        from audit_claims import iter_claim_lines
        from suggest_anchors import check_anchors_full
        # regression R101: the sibling scripts derive their vault root from
        # __file__, so a template/worktree copy run from a vault's cwd would
        # count THIS repo's claims beside the cwd vault's link inventory, in
        # one report, with nothing saying so. Every other section here walks
        # cwd; make this one agree. suggest_anchors.set_root() (regression R67)
        # cascades to audit_claims.set_root() AND re-derives suggest_anchors'
        # own _meta/ paths, so a later META read in check_anchors_full cannot
        # regress to two trees; it raises ValueError on a cwd with no wiki/
        # (or a pre-R67 audit_claims.py), which the except below turns into
        # the skipped line.
        if not hasattr(sa, "set_root"):
            raise RuntimeError("suggest_anchors.py predates regression R67 (no set_root) -- "
                               "run python scripts/sync_from_template.py --apply")
        root = sa.set_root(os.getcwd())
        claims = list(iter_claim_lines())
        anch = sum(1 for c in claims if c["anchored"])
        pct = 100 * anch / len(claims) if claims else 100.0
        # SUSPECT is reported beside BROKEN, never added to it: it flags an
        # anchor that resolves only under lenient normalization, which is
        # sometimes fine (emphasis) and sometimes a silent top-of-page landing
        # (the em-dash class). Counting them together would make the headline
        # number mean two things at once.
        problems, suspects = check_anchors_full()
        if not claims:
            # 0/0 would print as 100%, which reads as "fully anchored" when it
            # means "no population": this vault cites sources some other way
            # (e.g. `(source: file.md)` prose), so the anchor stack has nothing
            # to measure. Broken-anchor count still prints -- stray
            # [[sources/#...]] anchors can exist outside the claim population.
            print(f"=== LOCATOR HYGIENE === no source-citing claims -- anchor stack"
                  f" not applicable to this vault's citation style"
                  f" | broken heading anchors {len(problems)} | suspect {len(suspects)}")
        else:
            print(f"=== LOCATOR HYGIENE === coverage {anch}/{len(claims)} ({pct:.0f}%, target 60%)"
                  f" | broken heading anchors {len(problems)} | suspect {len(suspects)}")
        # Which tree the numbers above describe -- printed under --summary
        # too, so a close-out paste can never again mix two trees silently.
        print(f"  root: {root}")
        if problems and not args.summary:
            for prob in problems[:50]:
                print(f"  BROKEN: {prob}")
        if suspects and not args.summary:
            for s in suspects[:50]:
                print(f"  SUSPECT: {s}")
        if not args.summary and pct < 60:
            print("  -> anchor new citations at write time; backfill via scripts/suggest_anchors.py")
    except Exception as e:  # advisory -- a missing sibling script must not break lint
        print(f"=== LOCATOR HYGIENE === skipped ({e.__class__.__name__}: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
