#!/usr/bin/env python3
"""Benchmark llm_suggest_links.py against the frozen fixture vault.

The fixture (scripts/tests/fixture-vault/) is NEVER edited after creation --
it is the fixed baseline that makes script/model changes quantifiable.
expected-links.json holds the ground truth: pairs that must be suggested
(recall), trap pairs that must not be (precision -- each trap encodes a bug
class fixed in the 2026-07 code review), and "stretch" pairs beyond the
current design (reported, not scored).

Each run copies the fixture to a temp dir (so the response cache and
seen-ledger never leak between runs or into the repo), runs the suggester,
parses its output, and scores it.

  python scripts/tests/bench_links.py                       # score default model
  python scripts/tests/bench_links.py --model qwen35b-iq3   # bake-off another model
  python scripts/tests/bench_links.py --record              # also append a row to results.md

Template-only development tooling: not in SYNC_SET, instances never see it.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TESTS))
SUGGESTER = os.path.join(REPO, "scripts", "llm_suggest_links.py")
FIXTURE = os.path.join(TESTS, "fixture-vault")
TRUTH = os.path.join(TESTS, "expected-links.json")
RESULTS = os.path.join(TESTS, "results.md")


def model_digest(endpoint: str, model: str) -> str:
    """First 12 chars of the Ollama model digest ('' if unavailable) --
    recorded because a model re-pull can shift results with no code change."""
    try:
        with urllib.request.urlopen(endpoint.rstrip("/") + "/api/tags", timeout=5) as r:
            for m in json.loads(r.read()).get("models", []):
                if m.get("name", "").split(":latest")[0] in (model, model.split(":latest")[0]):
                    return m.get("digest", "")[:12]
    except Exception:
        pass
    return ""


def git_sha() -> str:
    try:
        return subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return "?"


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Score llm_suggest_links.py on the fixture vault.")
    ap.add_argument("--model", default="", help="Ollama model to bench (default: script default).")
    ap.add_argument("--endpoint", default="http://localhost:11434")
    ap.add_argument("--record", action="store_true", help="Append the scores to results.md.")
    args = ap.parse_args()

    with open(TRUTH, "r", encoding="utf-8") as fh:
        truth = json.load(fh)
    expected = {tuple(p) for p in truth["expected"]}
    forbidden = {name: tuple(p) for name, p in truth["forbidden"].items()}
    hard_expected = {name: tuple(p) for name, p in truth.get("hard_expected", {}).items()}
    hard_forbidden = {name: tuple(p) for name, p in truth.get("hard_forbidden", {}).items()}
    stretch = {tuple(p) for p in truth["stretch"]}
    # Hard-tier pages score only in the hard tier; keep them out of core FP.
    hard_pages = {p for p, _ in list(hard_expected.values()) + list(hard_forbidden.values())}

    tmp = tempfile.mkdtemp(prefix="wikillm-bench-")
    try:
        work = os.path.join(tmp, "vault")
        shutil.copytree(FIXTURE, work)
        if args.model:
            os.makedirs(os.path.join(work, "_meta"), exist_ok=True)
            with open(os.path.join(work, "_meta", "local-llm.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"model": args.model, "endpoint": args.endpoint}, fh)

        t0 = time.monotonic()
        proc = subprocess.run([sys.executable, SUGGESTER], cwd=work,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=1800)
        seconds = time.monotonic() - t0
        if "Ollama not reachable" in proc.stdout:
            print(proc.stdout.strip())
            return 0

        got: set[tuple[str, str]] = set()
        page = None
        calls = prompt_tok = out_tok = 0
        for line in proc.stdout.splitlines():
            m = re.match(r"-- (\S+) \(", line)
            if m:
                page = m.group(1)
            m = re.match(r'\s+mention ".*" -> \[\[(.+)\]\]', line)
            if m and page:
                got.add((page, m.group(1)))
            m = re.match(r"=== LLM === (\d+) calls, (\d+) prompt \+ (\d+) output", line)
            if m:
                calls, prompt_tok, out_tok = map(int, m.groups())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    model = args.model or "(script default)"
    core_got = {g for g in got if g[0] not in hard_pages}
    tp = core_got & expected
    fn = expected - core_got
    fp = core_got - expected - stretch
    tripped = {name: pair for name, pair in forbidden.items() if pair in core_got}
    stretch_hits = got & stretch
    precision = len(tp) / (len(tp) + len(fp)) if (tp or fp) else 1.0
    recall = len(tp) / len(expected) if expected else 1.0

    hard_hits = {name: pair in got for name, pair in hard_expected.items()}
    hard_avoids = {name: pair not in got for name, pair in hard_forbidden.items()}
    hard_ok = sum(hard_hits.values()) + sum(hard_avoids.values())
    hard_n = len(hard_expected) + len(hard_forbidden)

    print(f"model    : {model}")
    print(f"seconds  : {seconds:.0f}  ({calls} llm calls, "
          f"{prompt_tok} prompt + {out_tok} output tokens)")
    print(f"CORE (acceptance -- should stay near-perfect)")
    print(f"precision: {precision:.2f}  ({len(tp)} true / {len(fp)} false positives)")
    print(f"recall   : {recall:.2f}  ({len(tp)}/{len(expected)} expected found)")
    print(f"traps    : {len(forbidden) - len(tripped)}/{len(forbidden)} passed")
    for name, pair in tripped.items():
        print(f"  TRIPPED: {name}: {pair[0]} -> [[{pair[1]}]]")
    for pair in sorted(fn):
        print(f"  MISSED : {pair[0]} -> [[{pair[1]}]]")
    for pair in sorted(fp):
        print(f"  EXTRA  : {pair[0]} -> [[{pair[1]}]]")
    print(f"HARD (model ranking -- headroom by design)")
    print(f"hard     : {hard_ok}/{hard_n}")
    for name, ok in list(hard_hits.items()) + list(hard_avoids.items()):
        print(f"  {'pass' if ok else 'FAIL'} : {name}")
    print(f"stretch  : {len(stretch_hits)}/{len(stretch)} (unscored)")

    if args.record:
        digest = model_digest(args.endpoint, args.model) if args.model else ""
        new = not os.path.exists(RESULTS)
        with open(RESULTS, "a", encoding="utf-8") as fh:
            if new:
                fh.write("# wiki-llm scoreboard (bench_links.py)\n\n"
                         "Frozen-fixture scoreboard. CORE = acceptance (a usable model "
                         "stays near-perfect); HARD = model-ranking headroom (built so "
                         "the 2026 best fails some -- this column is where a better "
                         "future model shows up); sec/tokens break ties once CORE "
                         "saturates. Same model + new sha = did the code change help; "
                         "same sha + new model = bake-off. Digest recorded because a "
                         "model re-pull can shift results.\n\n"
                         "| date | sha | model | digest | precision | recall | traps "
                         "| hard | sec | tokens (p+o) | stretch |\n"
                         "|---|---|---|---|---|---|---|---|---|---|---|\n")
            fh.write(f"| {datetime.date.today().isoformat()} | {git_sha()} | {model} "
                     f"| {digest} | {precision:.2f} | {recall:.2f} "
                     f"| {len(forbidden) - len(tripped)}/{len(forbidden)} "
                     f"| {hard_ok}/{hard_n} | {seconds:.0f} "
                     f"| {prompt_tok}+{out_tok} "
                     f"| {len(stretch_hits)}/{len(stretch)} |\n")
        print(f"recorded -> {RESULTS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
