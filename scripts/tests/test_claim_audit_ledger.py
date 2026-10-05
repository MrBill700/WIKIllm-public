#!/usr/bin/env python3
"""Regression test for regression R52 -- the judged-claims ledger.

Before: audit_claims.py's quarterly seed re-served the IDENTICAL queue at every
same-quarter close-out, so after run 1 the audit re-judged the same claims and
stopped sampling new ones. Ruled fix (the owner 2026-09-19): _meta/claim-audit-ledger.json
keyed by sha256(relpath + whitespace-normalized line); rows store facts only;
skip is DERIVED in one function.

  L1  is_skipped() truth table: FAITHFUL + this period -> skip; DRIFTED /
      UNSUPPORTED / STALE never skip (re-armed); another period never skips
      (period roll); --include-judged never skips; no row never skips
  L2  claim_key: backslash vs slash paths and whitespace runs are the same
      identity; any other text change (an anchor insertion) is a new identity
  L3  --n means NEW claims: a claim recorded FAITHFUL leaves the pool, the
      queue serves the others, NEXT STEPS prints the skipped count
  L4  non-FAITHFUL re-arms: a claim recorded DRIFTED is served again
  L5  --include-judged lifts the skip
  L6  period roll lifts the skip (ledger row from an earlier quarter)
  L7  identity change = re-judge: editing the judged line serves it again, and
      the next record PRUNES the dead key and counts it
  L8  --record-run writes verdicts from workflow rows (by key, and by
      file:line when a replay row has no key); per-slug UNCONFIRMED is stored;
      one slug judged twice keeps the worse verdict
  L9  bad input: a malformed --record-verdict exits 2 and writes nothing; an
      unknown id is rejected before write
  L11 review fixes: UNCONFIRMED vetoes FAITHFUL on one key; a key beside the
      wrong file is UNRESOLVED; --record-verdict never appends to runs (the
      regression R70 telemetry); re-recording a run on file applies its verdicts
  L12 re-recording a run on file only back-fills (own quarter), never replaces a row,
      same day included (L12b); a new run never overwrites a strictly newer row
  L10 no stored boolean: ledger rows carry exactly relpath / verdict /
      last_judged_run / last_judged_period

Run: python scripts/tests/test_claim_audit_ledger.py    Exit 0 = pass.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True  # imports audit_claims below; leave no __pycache__

import datetime
import copy
import json
import re
import subprocess
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
AUDIT = SCRIPTS / "audit_claims.py"
sys.path.insert(0, str(SCRIPTS))
import audit_claims as ac  # noqa: E402

fails: list[str] = []
pinned: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        fails.append(msg)


CLAIMS = [
    "- LEDGERFIX alpha ceiling is 42 kg [[sources/handbook]].",
    "- LEDGERFIX beta storage is dry-only [[sources/handbook]].",
    "- LEDGERFIX gamma crates stack five high [[sources/handbook]].",
]


def build(root: Path) -> Path:
    (root / "_meta").mkdir(parents=True)
    src = root / "wiki" / "sources"
    src.mkdir(parents=True)
    (src / "handbook.md").write_text("---\ntitle: Handbook\n---\n\n# Handbook\n\n## Storage\n\nDry.\n", encoding="utf-8")
    con = root / "wiki" / "concepts"
    con.mkdir()
    page = con / "crates.md"
    page.write_text("---\ntitle: Crates\n---\n\n# Crates\n\n" + "\n".join(CLAIMS) + "\n", encoding="utf-8")
    return page


def run(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(AUDIT), "--root", str(root), "--no-history", *extra],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def queue(root: Path, *extra: str):
    """(ids served in order, skipped count, full stdout)"""
    r = run(root, "--n", "3", *extra)
    check(r.returncode == 0, f"sample {extra}: exit {r.returncode} {r.stderr}")
    ids = re.findall(r"id=([0-9a-f]{16})", r.stdout)
    m = re.search(r"skipped (\d+) claim-source judgment", r.stdout)
    return ids, (int(m.group(1)) if m else None), r.stdout


def ledger(root: Path) -> dict:
    return json.loads((root / "_meta" / "claim-audit-ledger.json").read_text(encoding="utf-8"))


def review_regressions(period: str) -> None:
    """Review regression: legacy chronology and malformed judgments must not false-clear."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "vault"
        page = build(root)
        line = "- REVIEW claim [[sources/a]] and [[sources/b]]."
        page.write_text("---\ntitle: Crates\n---\n\n" + line + "\n", encoding="utf-8")
        for slug in ("a", "b"):
            (root / "wiki" / "sources" / f"{slug}.md").write_text(f"# {slug}\n", encoding="utf-8")
        key = ac.claim_key("wiki/concepts/crates.md", line)
        path = root / "_meta" / "claim-audit-ledger.json"
        record = Path(td) / "review-run.json"
        day = datetime.date.today().isoformat()

        def judgment(slug, **fields):
            return {"key": key, "file": "wiki/concepts/crates.md", "line": 5, "slug": slug, **fields}

        def workflow(seed, rows):
            return {"kind": "workflow", "seed": seed, "n": len(rows), "hits": 0, "split": 0,
                    "unconfirmed": 0, "escalated": 0, "escalation_reasons": {}, "rows": rows}

        def stored(rec, suffix):
            return {**rec, "run": f"{day}.{suffix}", "recorded": day}

        def replay(rec):
            record.write_text(json.dumps(rec), encoding="utf-8")
            result = run(root, "--record-run", str(record))
            check(result.returncode == 0, f"review record-run must exit 0: {result.stderr}")
            return result

        faithful = [judgment(s, final_verdict="FAITHFUL") for s in ("a", "b")]
        older = workflow("review-older", faithful)
        newer = workflow("review-newer", faithful)
        flat_row = {"relpath": "wiki/concepts/crates.md", "verdict": "DRIFTED",
                    "last_judged_run": f"{day}.2", "last_judged_period": period}
        initial = {"version": 1, "runs": [stored(older, 1), stored(newer, 3)], "claims": {key: flat_row}}
        path.write_text(json.dumps(initial), encoding="utf-8")
        replay(older)
        check(ledger(root) == initial, "P1a older replay must leave the newer legacy DRIFTED semantically unchanged")
        ids, skipped, _ = queue(root)
        check(ids == [key, key] and skipped == 0, "P1a neither legacy multi-source slug may be suppressed")
        pinned.append("P1a newer legacy DRIFTED survives older FAITHFUL replay; both slugs stay eligible")

        path.write_text(json.dumps(initial), encoding="utf-8")
        replay(newer)
        row = ledger(root)["claims"][key]
        check(all(row.get("sources", {}).get(s, {}).get("last_judged_run") == f"{day}.3" for s in ("a", "b")),
              "P1b genuinely newer replay must migrate into per-slug facts")
        check(queue(root)[0] == [], "P1b both newer FAITHFUL judgments should suppress the line")
        pinned.append("P1b genuinely newer replay can migrate forward into per-slug coverage")

        # A partial migration must retain the watermark for the still-unjudged sibling.
        partial = workflow("review-partial", faithful[:1])
        partial_initial = copy.deepcopy(initial)
        partial_initial["runs"] = [stored(older, 1), stored(partial, 3)]
        path.write_text(json.dumps(partial_initial), encoding="utf-8")
        replay(partial)
        migrated = ledger(root)
        replay(older)
        check(ledger(root) == migrated, "P1c stale replay must not back-fill b after a newer partial migration")
        ids, skipped, output = queue(root)
        check(ids == [key] and skipped == 1 and re.findall(r"^  \[\[sources/([^]]+)\]\]", output, re.M) == ["b"],
              "P1c watermark gives no coverage: missing b stays eligible after partial migration")
        pinned.append("P1c ordering watermark survives partial migration and still grants no sibling coverage")

        # Fresh manual judgments share a date-only run token; they are later writes,
        # unlike replay of the old workflow telemetry.
        manual_initial = copy.deepcopy(initial)
        manual_initial["claims"][key]["last_judged_run"] = f"in-session:{day}"
        path.write_text(json.dumps(manual_initial), encoding="utf-8")
        result = run(root, "--record-verdict", f"{key}@a=FAITHFUL", "--record-verdict", f"{key}@b=FAITHFUL")
        check(result.returncode == 0 and queue(root)[0] == [],
              "P1d fresh manual judgments must still migrate a same-day in-session watermark")
        pinned.append("P1d fresh same-day manual judgments retain last-write-wins migration")

        # Missing/unknown verdicts veto only their pair, including malformed JSON types.
        for label, bad_fields in (("missing", {}), ("unknown", {"final_verdict": "MAYBE"}),
                                  ("null", {"final_verdict": None}), ("list", {"final_verdict": []}),
                                  ("object", {"final_verdict": {}})):
            rows = [faithful[0], judgment("a", **bad_fields)]
            malformed = workflow("review-" + label, rows)
            path.write_text(json.dumps({"version": 1, "runs": [stored(malformed, 4)], "claims": {}}), encoding="utf-8")
            result = replay(malformed)
            check("1 judged row(s) UNRESOLVED" in result.stdout, f"P2 {label}: malformed verdict must report UNRESOLVED")
            check(not ledger(root)["claims"], f"P2 {label}: malformed verdict must veto FAITHFUL for the same pair")
            check(queue(root)[1] == 0, f"P2 {label}: malformed pair must stay eligible")
        pinned.append("P2a/b missing, unknown, null, list, and object verdicts report UNRESOLVED and veto FAITHFUL")

        malformed_a = workflow("review-pair-scope", [faithful[0], judgment("a"), faithful[1]])
        path.write_text(json.dumps({"version": 1, "runs": [], "claims": {}}), encoding="utf-8")
        result = replay(malformed_a)
        row = ledger(root)["claims"].get(key, {})
        check("UNRESOLVED" in result.stdout and set(row.get("sources", {})) == {"b"}
              and row["sources"]["b"]["verdict"] == "FAITHFUL", "P2c malformed a must not veto valid b")
        ids, skipped, output = queue(root)
        check(ids == [key] and skipped == 1 and re.findall(r"^  \[\[sources/([^]]+)\]\]", output, re.M) == ["a"],
              "P2c only malformed a should remain eligible")
        pinned.append("P2c veto is pair-scoped: malformed a stays eligible while valid b is recorded")


def main() -> int:
    period = ac.period_of(datetime.date.today())

    # L1
    F = {"verdict": "FAITHFUL", "last_judged_period": period}
    F = {"sources": {"handbook": F}}
    check(ac.is_skipped(F, "handbook", period, False) is True, "FAITHFUL this period must skip")
    for v in ("DRIFTED", "UNSUPPORTED", "STALE"):
        row = {"sources": {"handbook": {"verdict": v, "last_judged_period": period}}}
        check(ac.is_skipped(row, "handbook", period, False) is False, f"{v} must re-arm")
    old = {"sources": {"handbook": {"verdict": "FAITHFUL", "last_judged_period": "2000-Q1"}}}
    check(ac.is_skipped(old, "handbook", period, False) is False, "period roll must lift")
    check(ac.is_skipped(F, "handbook", period, True) is False, "--include-judged must lift")
    check(ac.is_skipped(None, "handbook", period, False) is False, "no ledger row must not skip")
    pinned.append("L1 is_skipped truth table (7 cases)")

    # L2
    k = ac.claim_key("wiki/concepts/crates.md", CLAIMS[0])
    check(ac.claim_key("wiki\\concepts\\crates.md", "  " + CLAIMS[0].replace(" ", "   ") + "  ") == k,
          "slash direction / whitespace runs changed the identity")
    check(ac.claim_key("wiki/concepts/crates.md", CLAIMS[0].replace("[[sources/handbook]]", "[[sources/handbook#Storage]]")) != k,
          "an anchor insertion must be a NEW identity")
    check(re.fullmatch(r"[0-9a-f]{16}", k) is not None, f"key shape: {k}")
    pinned.append(f"L2 key {k}: whitespace/slash-stable, anchor insertion = new identity")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "vault"
        page = build(root)
        ids0, sk0, _ = queue(root)
        check(len(ids0) == 3 and sk0 == 0, f"baseline: {ids0} skipped={sk0}")
        judged = ids0[0]

        # L3
        r = run(root, "--record-verdict", f"{judged}=FAITHFUL")
        check(r.returncode == 0 and "1 claim verdict(s) written" in r.stdout, f"record FAITHFUL: {r.returncode} {r.stdout} {r.stderr}")
        ids1, sk1, out1 = queue(root)
        check(judged not in ids1 and len(ids1) == 2 and sk1 == 1, f"after FAITHFUL: {ids1} skipped={sk1}")
        check(re.search(r"n=2\b", out1) is not None, "printed n= must count only the new claims")
        pinned.append(f"L3 FAITHFUL {judged} skipped; queue {len(ids1)} new, skipped count {sk1} printed")

        # L5
        ids5, sk5, _ = queue(root, "--include-judged")
        check(judged in ids5 and len(ids5) == 3, f"--include-judged: {ids5}")
        pinned.append("L5 --include-judged serves the judged claim again")

        # L6
        L = ledger(root)
        L["claims"][judged]["last_judged_period"] = "2000-Q1"
        L["claims"][judged]["sources"]["handbook"]["last_judged_period"] = "2000-Q1"
        (root / "_meta" / "claim-audit-ledger.json").write_text(json.dumps(L), encoding="utf-8")
        ids6, sk6, _ = queue(root)
        check(judged in ids6 and sk6 == 0, f"period roll: {ids6} skipped={sk6}")
        pinned.append("L6 a row from an earlier quarter does not skip")

        # L4
        r = run(root, "--record-verdict", f"{judged}=DRIFTED")
        check(r.returncode == 0, f"record DRIFTED: {r.stderr}")
        ids4, sk4, _ = queue(root)
        check(judged in ids4 and sk4 == 0 and ledger(root)["claims"][judged]["verdict"] == "DRIFTED",
              f"DRIFTED re-arm: {ids4} skipped={sk4}")
        pinned.append("L4 DRIFTED stays in the queue (previous verdict overwritten, stored)")

        # L7
        run(root, "--record-verdict", f"{judged}=FAITHFUL")
        check(queue(root)[1] == 1, "setup: judged claim should be skipped before the edit")
        text = page.read_text(encoding="utf-8").replace(CLAIMS[0], CLAIMS[0].replace("[[sources/handbook]]", "[[sources/handbook#Storage]]"))
        page.write_text(text, encoding="utf-8")
        ids7, sk7, _ = queue(root)
        check(judged not in ids7 and len(ids7) == 3 and sk7 == 0, f"edited line must be a new, unjudged identity: {ids7} skipped={sk7}")
        other = [i for i in ids7 if i not in ids0][0]
        r = run(root, "--record-verdict", f"{other}=FAITHFUL")
        check("1 key(s) pruned" in r.stdout and judged not in ledger(root)["claims"], f"prune: {r.stdout}")
        pinned.append("L7 anchor edit -> new id served; old key pruned on the next write ('1 key(s) pruned')")

        # L8 --record-run rows: by key, by file:line (replay row with no key), UNCONFIRMED skipped,
        # duplicate key keeps the worse verdict
        ids8 = queue(root, "--include-judged")[0]
        lines = page.read_text(encoding="utf-8").splitlines()
        c_line = next(i + 1 for i, l in enumerate(lines) if "gamma" in l)
        c_key = ac.claim_key("wiki/concepts/crates.md", lines[c_line - 1])
        a, b = [i for i in ids8 if i != c_key][:2]   # gamma is recorded by file:line only
        rr = {"kind": "workflow", "seed": period, "n": 5, "hits": 1, "split": 0, "unconfirmed": 1, "escalated": 2,
              "escalation_reasons": {"scope.verdict": 1}, "rows": [
                  {"key": a, "file": "wiki/concepts/crates.md", "line": 99, "final_verdict": "FAITHFUL"},
                  {"key": a, "file": "wiki/concepts/crates.md", "line": 99, "final_verdict": "UNSUPPORTED"},
                  {"key": b, "file": "wiki/concepts/crates.md", "line": 99, "final_verdict": "UNCONFIRMED"},
                  {"file": "wiki\\concepts\\crates.md", "line": c_line, "final_verdict": "FAITHFUL"}]}
        f = Path(td) / "rr.json"
        f.write_text(json.dumps({"run_record": rr}), encoding="utf-8")
        before_b = ledger(root)["claims"].get(b)
        r = run(root, "--record-run", str(f))
        check(r.returncode == 0, f"--record-run: {r.returncode} {r.stderr}")
        C = ledger(root)["claims"]
        check(C.get(a, {}).get("verdict") == "UNSUPPORTED", f"duplicate key must keep the worse verdict: {C.get(a)}")
        check(C.get(b, {}).get("sources", {}).get("handbook", {}).get("verdict") == "UNCONFIRMED",
              "an UNCONFIRMED source judgment must be stored and remain eligible")
        check(C.get(c_key, {}).get("verdict") == "FAITHFUL", f"a keyless replay row must resolve by file:line: {c_key} {c_line} {sorted(C)} {r.stdout}")
        run_id = ledger(root)["runs"][-1]["run"]
        check(C.get(a, {}).get("last_judged_run") == run_id, "last_judged_run must name the recording run")
        runs_before = len(ledger(root)["runs"])
        pinned.append(f"L8 --record-run: per-slug worst-verdict-wins, UNCONFIRMED stored, file:line fallback; run {run_id}")

        # L11 (review B1) an UNCONFIRMED row VETOES a FAITHFUL on the same key: a two-link
        # line with one link cleared and one unadjudicated was not judged FAITHFUL
        run(root, "--record-verdict", f"{b}=DRIFTED")          # arm b (not FAITHFUL)
        rr2 = dict(rr, seed="veto", rows=[{"key": b, "file": "wiki/concepts/crates.md", "line": 1, "final_verdict": "FAITHFUL"},
                                          {"key": b, "file": "wiki/concepts/crates.md", "line": 1, "final_verdict": "UNCONFIRMED"}])
        f.write_text(json.dumps(rr2), encoding="utf-8")
        run(root, "--record-run", str(f))
        check(ledger(root)["claims"][b]["verdict"] == "UNCONFIRMED", f"UNCONFIRMED did not veto FAITHFUL: {ledger(root)['claims'][b]}")
        # (review P1) a key carried beside a DIFFERENT file is not trusted
        rr3 = dict(rr, seed="swap", rows=[{"key": b, "file": "wiki/concepts/other.md", "line": 1, "final_verdict": "FAITHFUL"}])
        f.write_text(json.dumps(rr3), encoding="utf-8")
        r = run(root, "--record-run", str(f))
        check("UNRESOLVED" in r.stdout and ledger(root)["claims"][b]["verdict"] == "UNCONFIRMED", f"key/file mismatch accepted: {r.stdout}")
        # (review B2) in-session records never touch the workflow telemetry in runs
        runs_now = len(ledger(root)["runs"])
        for _ in range(3):
            run(root, "--record-verdict", f"{b}=DRIFTED")
        check(len(ledger(root)["runs"]) == runs_now, "--record-verdict appended to runs")
        check(ledger(root)["claims"][b]["last_judged_run"].startswith("in-session:"), "in-session last_judged_run token")
        check(all(r_.get("kind") == "workflow" for r_ in ledger(root)["runs"]), "a non-workflow entry is in runs")
        # (review C1) re-recording a run already on file still applies its verdicts (idempotent)
        L = ledger(root)
        L["claims"].pop(c_key, None)
        (root / "_meta" / "claim-audit-ledger.json").write_text(json.dumps(L), encoding="utf-8")
        f.write_text(json.dumps({"run_record": rr}), encoding="utf-8")
        r = run(root, "--record-run", str(f))
        check("telemetry not duplicated" in r.stdout and ledger(root)["claims"].get(c_key, {}).get("verdict") == "FAITHFUL"
              and len(ledger(root)["runs"]) == runs_now, f"duplicate run did not apply verdicts: {r.stdout}")
        # (re-review C1-regress) replaying an OLD run: its FAITHFUL must not bury a newer
        # DRIFTED, and a claim it newly writes is stamped with the run's own quarter, not today's
        old_day = datetime.date.today() - datetime.timedelta(days=200)
        old_id = f"{old_day.isoformat()}.1"
        rr_old = dict(rr, seed="old-run", rows=[{"key": a, "file": "wiki/concepts/crates.md", "line": 1, "final_verdict": "FAITHFUL"},
                                               {"key": b, "file": "wiki/concepts/crates.md", "line": 1, "final_verdict": "FAITHFUL"}])
        L = ledger(root)
        L["runs"].append({**rr_old, "run": old_id, "recorded": old_day.isoformat()})
        L["claims"][a] = {"relpath": "wiki/concepts/crates.md", "verdict": "DRIFTED",
                          "last_judged_run": f"in-session:{datetime.date.today().isoformat()}", "last_judged_period": period}
        L["claims"].pop(b, None)
        (root / "_meta" / "claim-audit-ledger.json").write_text(json.dumps(L), encoding="utf-8")
        f.write_text(json.dumps(rr_old), encoding="utf-8")
        r = run(root, "--record-run", str(f))
        C = ledger(root)["claims"]
        check(C[a]["verdict"] == "DRIFTED", f"an old replayed FAITHFUL overwrote a newer DRIFTED: {C[a]}")
        check(C.get(b, {}).get("last_judged_period") == ac.period_of(old_day) and not ac.is_skipped(C.get(b), "handbook", period, False),
              f"replayed run stamped with today's quarter: {C.get(b)}")
        # L12b same-day: a retry of today's run must not undo an in-session verdict recorded after it
        run(root, "--record-verdict", f"{c_key}=DRIFTED")
        f.write_text(json.dumps({"run_record": rr}), encoding="utf-8")
        r = run(root, "--record-run", str(f))
        check("telemetry not duplicated" in r.stdout and ledger(root)["claims"][c_key]["verdict"] == "DRIFTED",
              f"same-day replay overwrote a later in-session DRIFTED: {ledger(root)['claims'][c_key]} {r.stdout}")
        pinned.append("L12b same-day replay leaves a later in-session DRIFTED in place (replay back-fills only)")
        pinned.append("L12 replayed old run: newer DRIFTED kept; new row stamped with the run's own quarter (not skipped now)")
        pinned.append("L11 UNCONFIRMED vetoes FAITHFUL; key/file mismatch UNRESOLVED; --record-verdict leaves runs alone; "
                      "a re-recorded run applies verdicts without duplicating telemetry")

        # L9
        snap = (root / "_meta" / "claim-audit-ledger.json").read_text(encoding="utf-8")
        r = run(root, "--record-verdict", f"{a}=MAYBE")
        check(r.returncode == 2, f"bad verdict exit {r.returncode}")
        r = run(root, "--record-verdict", "nothex=FAITHFUL")
        check(r.returncode == 2, f"bad id exit {r.returncode}")
        check((root / "_meta" / "claim-audit-ledger.json").read_text(encoding="utf-8") == snap, "bad input changed the ledger")
        r = run(root, "--record-verdict", "0123456789abcdef=FAITHFUL")
        check(r.returncode == 2 and "unknown or stale claim id" in r.stderr and "0123456789abcdef" not in ledger(root)["claims"],
              f"unknown id: {r.stdout}")
        pinned.append("L9 malformed pair and unknown/stale id exit 2, ledger unchanged")

        # L10
        for key, row in ledger(root)["claims"].items():
            check(sorted(row) == ["last_judged_period", "last_judged_run", "relpath", "sources", "verdict"], f"row {key} fields: {sorted(row)}")
            check(row["relpath"] == "wiki/concepts/crates.md", f"relpath not slash-normalized: {row['relpath']}")
        pinned.append(f"L10 {len(ledger(root)['claims'])} ledger rows carry per-slug facts only, no stored skip flag")

    # regression R132 acceptance: line identity stays shared while eligibility is per slug.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "vault"
        (root / "_meta").mkdir(parents=True)
        sources = root / "wiki" / "sources"
        sources.mkdir(parents=True)
        for slug in ("a", "b"):
            (sources / f"{slug}.md").write_text(f"---\ntitle: {slug}\n---\n# {slug}\n", encoding="utf-8")
        concepts = root / "wiki" / "concepts"
        concepts.mkdir()
        line = "- MULTISOURCE claim [[sources/a]] and [[sources/b]]."
        (concepts / "multi.md").write_text("---\ntitle: Multi\n---\n# Multi\n\n" + line + "\n", encoding="utf-8")
        key = ac.claim_key("wiki/concepts/multi.md", line)

        rr = {"kind": "workflow", "seed": period, "n": 1, "hits": 0, "split": 0,
              "unconfirmed": 0, "escalated": 0, "escalation_reasons": {}, "rows": []}
        record = Path(td) / "run.json"
        rr["rows"] = [{"key": key, "file": "wiki/concepts/multi.md", "line": 5,
                       "slug": "a", "final_verdict": "FAITHFUL"}]
        record.write_text(json.dumps(rr), encoding="utf-8")
        check(run(root, "--record-run", str(record)).returncode == 0, "R52 A1 record a")
        _, _, out = queue(root)
        headers = re.findall(r"^  \[\[sources/([^]]+)\]\]", out, re.M)
        check(headers == ["b"],
              "R52 A1 only a FAITHFUL: next queue must offer b, not a")

        rr["seed"] = period + "-b-unconfirmed"
        rr["rows"] = [{"key": key, "file": "wiki/concepts/multi.md", "line": 5,
                       "slug": "b", "final_verdict": "UNCONFIRMED"}]
        record.write_text(json.dumps(rr), encoding="utf-8")
        run(root, "--record-run", str(record))
        _, _, out = queue(root)
        headers = re.findall(r"^  \[\[sources/([^]]+)\]\]", out, re.M)
        row = ledger(root)["claims"][key]
        check(headers == ["b"]
              and row["sources"]["a"]["verdict"] == "FAITHFUL"
              and row["sources"]["b"]["verdict"] == "UNCONFIRMED",
              "R52 A3 b UNCONFIRMED must stay eligible without re-arming a")

        rr["seed"] = period + "-b-faithful"
        rr["rows"][0]["final_verdict"] = "FAITHFUL"
        record.write_text(json.dumps(rr), encoding="utf-8")
        run(root, "--record-run", str(record))
        ids, skipped, out = queue(root)
        check(not ids and skipped == 2, "R52 A2 a+b FAITHFUL must fully skip both source entries")
        ids, _, out = queue(root, "--include-judged")
        check(len(ids) == 2 and out.count(f"id={key}") == 2,
              "R52 A4 --include-judged must lift both per-slug skips")
        pinned.append("R52 A1-A4: line key retained; a/b eligibility independent; all-faithful skips; include-judged lifts")

        # Compatibility edges requested in Lane H review.
        flat = {"version": 1, "runs": [], "claims": {key: {
            "relpath": "wiki/concepts/multi.md", "verdict": "FAITHFUL",
            "last_judged_run": "in-session:2026-09-26", "last_judged_period": period}}}
        (root / "_meta" / "claim-audit-ledger.json").write_text(json.dumps(flat), encoding="utf-8")
        ids, skipped, _ = queue(root)
        check(len(ids) == 2 and skipped == 0,
              "compat: a legacy flat row on a multi-source line must suppress nothing")
        ids, skipped, _ = queue(root, "--source", "a")
        check(ids == [key] and skipped == 0,
              "compat: --source must not turn a legacy multi-source flat row into a one-source skip")

        snap = ledger(root)
        bad = run(root, "--record-verdict", f"{key}@=FAITHFUL")
        wrong = run(root, "--record-verdict", f"{key}@not-cited=FAITHFUL")
        unknown = run(root, "--record-verdict", "0123456789abcdef@a=FAITHFUL")
        ambiguous = run(root, "--record-verdict", f"{key}=FAITHFUL")
        check(bad.returncode == 2 and "expected ID[@SLUG]=VERDICT" in bad.stderr,
              "compat: malformed ID@SLUG must exit 2 with the accepted shape")
        check(wrong.returncode == 2 and "not currently cited" in wrong.stderr
              and unknown.returncode == 2 and "unknown or stale claim id" in unknown.stderr
              and ambiguous.returncode == 2 and "slug omitted" in ambiguous.stderr
              and ledger(root) == snap,
              "compat: unknown/wrong/ambiguous targets must hard-fail and leave ledger data unchanged")
        pinned.append("compat: legacy multi-source flat row skips none; malformed, unknown, wrong, and ambiguous ID@SLUG hard-fail")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "vault"
        build(root)
        key = ac.claim_key("wiki/concepts/crates.md", CLAIMS[0])
        flat = {"version": 1, "runs": [], "claims": {key: {
            "relpath": "wiki/concepts/crates.md", "verdict": "FAITHFUL",
            "last_judged_run": "in-session:2026-09-26", "last_judged_period": period}}}
        (root / "_meta" / "claim-audit-ledger.json").write_text(json.dumps(flat), encoding="utf-8")
        ids, skipped, _ = queue(root)
        check(key not in ids and skipped == 1,
              "compat: existing single-source flat FAITHFUL row must still skip exactly as before")
        r = run(root, "--record-verdict", f"{key}=DRIFTED")
        row = ledger(root)["claims"][key]
        check(r.returncode == 0 and row["sources"]["handbook"]["verdict"] == "DRIFTED"
              and key in queue(root)[0],
              "compat: plain ID=VERDICT must remain valid and lazily migrate a one-source flat row")
        r = run(root, "--record-verdict", f"{key}@handbook=FAITHFUL")
        check(r.returncode == 0 and ledger(root)["claims"][key]["sources"]["handbook"]["verdict"] == "FAITHFUL",
              "compat: ID@correct-slug=VERDICT must record normally")
        before = ledger(root)
        r = run(root, "--record-verdict", f"{key}@stale-source=DRIFTED")
        check(r.returncode == 2 and "not currently cited" in r.stderr and ledger(root) == before,
              "compat: explicit wrong/stale slug on a single-source line must hard-fail without fallback")
        before_bytes = (root / "_meta" / "claim-audit-ledger.json").read_bytes()
        r = run(root, "--record-verdict", f"{key}@handbook=DRIFTED",
                "--record-verdict", f"{key}@stale-source=FAITHFUL")
        check(r.returncode == 2
              and (root / "_meta" / "claim-audit-ledger.json").read_bytes() == before_bytes,
              "compat: valid then invalid repeated --record-verdict must be byte-transactional")
        pinned.append("compat: single-source flat row preserved; explicit slug checked; mixed-validity batch changes zero bytes")

    review_regressions(period)

    if fails:
        print(f"FAIL ({len(fails)}):")
        for f in fails:
            print(f"  - {f}")
        return 1
    for p in pinned:
        print(f"  ok {p}")
    print("PASS test_claim_audit_ledger: skip derived in one function, FAITHFUL skipped this quarter, "
          "non-FAITHFUL re-armed, --include-judged and period roll lift, identity change re-judges, prune counted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
