"""R151 cross-tool pins: lint.py and fix_wikilinks.py --alias-only agree on
BOM-prefixed alias holders, and the fixer's own in-vault state never becomes
an input to either tool or to the sync.

P1 PARITY (the pre-merge review ask): one fixture, both tools. Alias
   holders: BOM+LF (wiki/), BOM+CRLF (wiki/), plain control (wiki/), a
   scripts/ page, CLAUDE.md, a BOM holder sharing its alias with a second
   page (ambiguous -> review), an upper-case wiki/Holder.MD (an asset to
   lint's case-sensitive walk, so its alias counts for NEITHER tool), plus a
   BOM-prefixed _meta/ auto_generated page whose alias counts for neither.
   Citers: wiki/index.md, a scripts/ page (out of scope -> review) and a
   wiki/Cite.MD that neither tool scans. Per target, lint's ALIAS-ONLY and
   BROKEN counts must equal the fixer's plan (edits + review items by
   lint_kind, and its broken table), plus named expectations, so a change
   that breaks both tools the same way still fails. Note the ALIAS-ONLY half
   is partly self-referential (the fixer's lint_kind comes from the same
   _wikilib.lint_inventory lint uses): it catches the fixer dropping or
   mis-scoping links; the fixer's own classifier is checked through its
   broken table and the named expectations.
P2 RED controls (automated): the same fixture with a fixer whose _decode
   keeps the BOM, and with a lint whose inventory reads plain utf-8, must
   each FAIL P1. Proves P1 can see the disagreement it pins.
S1 STATE ISOLATION (ADR-0005, regression R153): the dry run and --apply create
   NOTHING inside the vault -- the manifest and the real backups of the
   rewritten page (still holding the old alias links) land in --state-dir,
   outside it. A planted decoy run under LEGACY in-vault state
   (.alias-fix-backup/, the legacy in-vault layout that stays readable until
   --migrate-legacy-state) whose pages are NAMED like link targets (Zed.md,
   Wex.md) and carry competing aliases and links changes neither lint's
   output nor the fixer's plan. After --apply lint's ALIAS-ONLY is exactly
   the review residue, ALL FILES is unchanged, and the fixer's re-run says
   "Nothing to do". S1 RED: with dot-directory pruning removed from both
   vault walkers, S1 must fail.
S2 SYNC: no SYNC_SET entry is, or contains, .alias-fix-backup/ or
   _meta/alias-fix-manifest.json (read from the script's AST, not imported).

Run: python -B scripts/tests/test_alias_bom_parity.py    Exit 0 = pass.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1]
COPY = ("lint.py", "_wikilib.py", "audit_claims.py", "suggest_anchors.py", "fix_wikilinks.py")
AO_HEADER = "ALIAS-ONLY LINKS"
BROKEN_HEADER = "BROKEN LINKS"
MANIFEST = "_meta/alias-fix-manifest.json"  # legacy in-vault manifest (legacy in-vault-state release)
BOM = b"\xef\xbb\xbf"
fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        fails.append(msg)


FILES: dict[str, bytes] = {
    "CLAUDE.md": b"---\naliases: [Uno]\n---\n# Vault\n\n[[index]]\n",
    "wiki/bomholder.md": BOM + b"---\naliases: [Zed]\n---\nbody [[index]]\n",
    "wiki/bomcrlf.md": BOM + b"---\r\naliases:\r\n  - Yon\r\n---\r\nbody [[index]]\r\n",
    "wiki/plainholder.md": b"---\naliases: [Xan]\n---\nbody [[index]]\n",
    "wiki/bomdup.md": BOM + b"---\naliases: [Tam]\n---\nbody [[index]]\n",
    "wiki/tamtoo.md": b"---\naliases: [Tam]\n---\nbody [[index]]\n",
    "wiki/Holder.MD": b"---\naliases: [Qua]\n---\nbody [[index]]\n",
    "wiki/Cite.MD": b"never scanned [[Zed]] [[Xan]]\n",
    "scripts/notes.md": b"---\naliases: [Vim]\n---\nnotes [[index]]\n",
    "scripts/cite.md": b"script doc cites [[Zed]]\n",
    "_meta/generated.md": BOM + b"---\nauto_generated: true\naliases: [Wex]\n---\ngen\n",
    "wiki/index.md": (b"# Index\n\n[[Zed]] [[Zed|z]] [[Yon]] [[Xan]] [[Vim]] [[Uno]] [[Wex]]"
                      b" [[Tam]] [[Qua]]\n"
                      b"[[bomholder]] [[bomcrlf]] [[plainholder]] [[bomdup]] [[tamtoo]]\n"),
}
# The named verdicts both tools must reach (not only agree on).
WANT_AO = {"Zed": 3, "Yon": 1, "Xan": 1, "Vim": 1, "Uno": 1, "Tam": 1}
WANT_BROKEN = {"Wex": 1, "Qua": 1}
# Left after --apply: the ambiguous alias and the out-of-scope scripts/ citer.
WANT_AO_AFTER = {"Zed": 1, "Tam": 1}


def build(base: Path, name: str) -> Path:
    v = base / name
    for rel, data in FILES.items():
        p = v / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    for s in COPY:
        shutil.copy2(SCRIPTS / s, v / "scripts" / s)
    return v


def state_of(v: Path) -> Path:
    """The --state-dir every fixer run here passes: outside the vault."""
    return Path(str(v) + ".state")


def run(v: Path, script: str, *args: str) -> tuple[int, str]:
    extra = ["--state-dir", str(state_of(v))] if "--alias-only" in args else []
    r = subprocess.run([sys.executable, "-B", str(v / "scripts" / script), *args, *extra],
                       cwd=str(v), capture_output=True, text=True, encoding="utf-8")
    return r.returncode, r.stdout + r.stderr


def tree(v: Path) -> set[str]:
    """Every file AND directory path in the vault (scripts/ copies aside)."""
    return {p.relative_to(v).as_posix() + ("/" if p.is_dir() else "") for p in v.rglob("*")
            if not p.relative_to(v).as_posix().startswith("scripts/__pycache__")}


def lint_out(v: Path, bad: list[str]) -> str:
    rc, out = run(v, "lint.py")
    if rc != 0:
        bad.append(f"lint rc {rc} in {v.name}:\n{out}")
    # The LOCATOR root line carries the absolute path; nothing else does.
    return "\n".join(l for l in out.splitlines() if not l.strip().startswith("root:"))


def block_targets(out: str, header: str) -> dict[str, int]:
    """{target: count} from one lint section (lines '  xN    [[Target]] ...')."""
    got: dict[str, int] = {}
    on = False
    for line in out.splitlines():
        if line.startswith("=== "):
            on = line.startswith("=== " + header)
            continue
        m = re.match(r"^\s+x(\d+)\s+\[\[([^\]|#]+)", line) if on else None
        if m:
            got[m.group(2)] = got.get(m.group(2), 0) + int(m.group(1))
    return got


def total(out: str, header: str) -> int:
    m = re.search(r"^=== " + re.escape(header) + r"[^=]*=== (\d+)", out, re.M)
    return int(m.group(1)) if m else -1


def target_of(link: str) -> str:
    return link[2:-2].split("|")[0].split("#")[0]


def fixer_plan(v: Path, bad: list[str]) -> tuple[dict, dict[str, int], dict[str, int]]:
    rc, out = run(v, "fix_wikilinks.py", "--alias-only", "--root", str(v))
    if rc != 0:
        bad.append(f"fixer dry run rc {rc} in {v.name}:\n{out}")
    m = json.loads((state_of(v) / "manifest.json").read_text(encoding="utf-8"))
    ao: dict[str, int] = {}
    items = [e for f in m["files"].values() for e in f["edits"]] + list(m["review"])
    for e in items:
        if e.get("lint_kind") == "alias-only":
            # Edits carry the old link text; review items carry "target".
            t = e.get("target") or target_of(e["old"])
            ao[t] = ao.get(t, 0) + 1
    broken = {k: int(n) for k, n in m["broken"].items()}
    return m, ao, broken


def parity(v: Path) -> list[str]:
    """Disagreements between the two tools on vault v ([] = parity)."""
    bad: list[str] = []
    out = lint_out(v, bad)
    l_ao, l_br = block_targets(out, AO_HEADER), block_targets(out, BROKEN_HEADER)
    _, f_ao, f_br = fixer_plan(v, bad)
    if l_ao != f_ao:
        bad.append(f"ALIAS-ONLY lint {l_ao} != fixer {f_ao}")
    if {k.lower(): n for k, n in l_br.items()} != {k.lower(): n for k, n in f_br.items()}:
        bad.append(f"BROKEN lint {l_br} != fixer {f_br}")
    if l_ao != WANT_AO:
        bad.append(f"lint ALIAS-ONLY {l_ao} != expected {WANT_AO}")
    if l_br != WANT_BROKEN:
        bad.append(f"lint BROKEN {l_br} != expected {WANT_BROKEN}")
    return bad


def isolation(vs: Path) -> list[str]:
    """S1 failures on vault vs ([] = the fixer's state is inert)."""
    bad: list[str] = []
    l0 = lint_out(vs, bad)
    t0 = tree(vs)
    m0, _, _ = fixer_plan(vs, bad)                  # writes the manifest -- outside the vault
    if not (state_of(vs) / "manifest.json").is_file():
        bad.append("dry run wrote no manifest in the state dir")
    if tree(vs) != t0:
        bad.append(f"dry run created inside the vault: {sorted(tree(vs) ^ t0)}")
    dw = vs / ".alias-fix-backup" / "deadbeefdeadbeef" / "wiki"
    dw.mkdir(parents=True)
    # Named like link targets: a walker that reaches them turns [[Zed]]
    # canonical and [[Wex]] resolved.
    (dw / "Zed.md").write_bytes(b"---\naliases: [Xan, Tam]\n---\n[[Wex]] [[Zed]] [[Nope]]\n")
    (dw / "Wex.md").write_bytes(b"---\naliases: [Qua]\n---\n[[Yon]]\n")
    l1 = lint_out(vs, bad)
    if l1 != l0:
        bad.append(f"manifest/backup dir changed lint output:\n--- before\n{l0}\n--- after\n{l1}")
    m1, _, _ = fixer_plan(vs, bad)
    for k in ("files", "review", "broken", "exempt"):
        if m1[k] != m0[k]:
            bad.append(f"fixer plan '{k}' changed with state present: {m0[k]} -> {m1[k]}")
    if m1["totals"]["files_scanned"] != m0["totals"]["files_scanned"]:
        bad.append(f"files_scanned {m0['totals']['files_scanned']} -> {m1['totals']['files_scanned']}")
    t1 = tree(vs)
    rc, out = run(vs, "fix_wikilinks.py", "--alias-only", "--root", str(vs), "--apply")
    if rc != 0:
        bad.append(f"apply rc {rc}:\n{out}")
    if tree(vs) != t1:
        bad.append(f"--apply created or removed inside the vault: {sorted(tree(vs) ^ t1)}")
    backups = [p for p in (state_of(vs) / "backup").rglob("index.md")]
    if not (backups and b"[[Zed]]" in backups[0].read_bytes()):
        bad.append(f"no real backup of wiki/index.md holding the old links in the state dir: {backups}")
    if list((vs / ".alias-fix-backup").rglob("index.md")):
        bad.append("a backup was written into the vault's legacy .alias-fix-backup/")
    l2 = lint_out(vs, bad)
    if block_targets(l2, AO_HEADER) != WANT_AO_AFTER:
        bad.append(f"ALIAS-ONLY after apply {block_targets(l2, AO_HEADER)} != {WANT_AO_AFTER}:\n{l2}")
    if total(l2, "ALL FILES") != total(l0, "ALL FILES"):
        bad.append(f"ALL FILES {total(l0, 'ALL FILES')} -> {total(l2, 'ALL FILES')} (backups scanned?)")
    if block_targets(l2, BROKEN_HEADER) != WANT_BROKEN:
        bad.append(f"BROKEN after apply {block_targets(l2, BROKEN_HEADER)} != {WANT_BROKEN}")
    rc, out = run(vs, "fix_wikilinks.py", "--alias-only", "--root", str(vs))
    if not (rc == 0 and "Nothing to do" in out):
        bad.append(f"re-run after apply not 'Nothing to do' (rc {rc}):\n{out}")
    return bad


def mutate(path: Path, old: str, new: str) -> None:
    src = path.read_text(encoding="utf-8")
    check(src.count(old) == 1, f"mutant anchor not unique in {path.name}: {old!r}")
    path.write_text(src.replace(old, new, 1), encoding="utf-8")


def sync_set() -> list[str]:
    tree = ast.parse((SCRIPTS / "sync_from_template.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SYNC_SET" for t in node.targets):
            return list(ast.literal_eval(node.value))
    return []


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="alias-bom-parity-"))
    import os
    os.environ["LOCALAPPDATA"] = str(tmp / "_localappdata")  # never the real one (ADR-0005)
    try:
        # P1
        bad = parity(build(tmp, "P1"))
        check(not bad, "P1 parity: " + "; ".join(bad))
        if not bad:
            print(f"PASS P1: lint == fixer per target {WANT_AO} (BOM/BOM+CRLF/plain/scripts/CLAUDE.md "
                  f"holders, ambiguous BOM alias, scripts/ citer); BROKEN {WANT_BROKEN} (BOM "
                  f"auto_generated _meta alias and .MD holder ignored by both; .MD citer unscanned)")

        # P2 -- each mutant must break parity.
        vf = build(tmp, "P2-fixer")
        mutate(vf / "scripts" / "fix_wikilinks.py",
               'return True, raw[3:].decode("utf-8")', 'return True, raw.decode("utf-8")')
        vl = build(tmp, "P2-lint")
        mutate(vl / "scripts" / "_wikilib.py",
               'with open(os.path.join(root, p), "r", encoding="utf-8-sig") as fh:',
               'with open(os.path.join(root, p), "r", encoding="utf-8") as fh:')
        vc = build(tmp, "P2-case")
        mutate(vc / "scripts" / "fix_wikilinks.py",
               'files = [f for f in collect_md_files(root) if f.name.endswith(".md")]',
               'files = collect_md_files(root)')
        mutate(vc / "scripts" / "fix_wikilinks.py",
               "# lint's walk is case-sensitive: .MD is an asset\n"
               '                                     if p.is_file() and p.name.endswith(".md"))',
               "if p.is_file())")
        for mv, who in ((vf, "fixer keeps BOM"), (vl, "lint reads plain utf-8"),
                        (vc, "fixer scans .MD pages")):
            got = parity(mv)
            check(bool(got), f"P2 RED: mutant '{who}' still passed P1 -- the test is blind")
            if got:
                print(f"PASS P2 RED: mutant '{who}' fails P1 ({got[0][:160]})")

        # S1 -- state isolation, then its RED control.
        bad = isolation(build(tmp, "S1"))
        check(not bad, "S1: " + "; ".join(bad))
        if not bad:
            print("PASS S1: dry run + apply create nothing in the vault (manifest + backups in --state-dir); "
                  "a target-named decoy under legacy .alias-fix-backup/ changes neither lint output nor "
                  f"the fixer plan; after apply ALIAS-ONLY = review residue {WANT_AO_AFTER}, "
                  "ALL FILES unchanged, re-run Nothing to do")
        vp = build(tmp, "S1-red")
        mutate(vp / "scripts" / "_wikilib.py",
               'dirs[:] = [x for x in dirs if not x.startswith(".")]', 'dirs[:] = list(dirs)')
        mutate(vp / "scripts" / "fix_wikilinks.py",
               'dirs[:] = sorted(x for x in dirs if not x.startswith("."))', 'dirs[:] = sorted(dirs)')
        got = isolation(vp)
        check(bool(got), "S1 RED: walkers without dot-dir pruning still passed S1 -- the decoy is inert")
        if got:
            print(f"PASS S1 RED: no dot-dir pruning fails S1 ({got[0].splitlines()[0]})")

        # S2 -- sync inputs.
        ss = sync_set()
        check(len(ss) > 5, f"S2: SYNC_SET not found / too small: {ss}")
        leak = [e for e in ss if e.rstrip("/").startswith(".alias-fix-backup")
                or e == MANIFEST or MANIFEST.startswith(e.rstrip("/") + "/")]
        check(not leak, f"S2: SYNC_SET reaches fixer state: {leak}")
        if len(ss) > 5 and not leak:
            print(f"PASS S2: none of {len(ss)} SYNC_SET entries is or contains .alias-fix-backup/ or {MANIFEST}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for f in fails:
        print("FAIL", f)
    print("OK" if not fails else f"{len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
