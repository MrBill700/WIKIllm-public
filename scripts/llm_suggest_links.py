#!/usr/bin/env python3
"""Local-LLM link suggester: find unlinked mentions of existing pages in prose.

Complements suggest_links.py (graph-structure curation): that tool asks "which
pages SHOULD be related?" from link topology; this one asks "which pages are
ALREADY mentioned in this page's prose but not wikilinked?" It uses a local
Ollama model to disambiguate real references from coincidental word matches,
so it costs zero cloud tokens.

The task is deliberately shaped for a small (7-14B) model:
  1. deterministic prefilter -- a candidate is an existing page whose title or
     alias appears as a whole word in the page's prose (wikilink spans and
     code masked out) and which is not already linked (alias-aware)
  2. the LLM answers ONE narrow question: which of these candidate pages
     (each shown with its description) does the text genuinely refer to?
     Forced JSON, temperature 0. It never copies phrases or rates confidence
     -- the mention text is already known from the prefilter.
  3. deterministic validation -- a suggestion survives only if its target was
     in the candidate list. The model structurally cannot invent a page.

The script NEVER edits wiki pages. Default is dry-run (print suggestions);
--write appends new ones to _meta/suggestions/links.md for a human or Claude
session to approve. Every written pair is remembered in
_meta/suggestions/seen.json, so deleting a row (= rejecting it) is permanent:
re-runs won't nag about it again (--include-seen overrides).

Model responses are cached in _meta/llm-link-cache.json keyed on
(model, prose window, candidate set) -- warm re-runs cost seconds, not GPU
minutes. Delete the file to force a full re-judge.

Run: python scripts/llm_suggest_links.py [--page PATH] [--limit N] [--write]
                                         [--include-seen]

Model/endpoint come from optional _meta/local-llm.json:
{"model": "...", "endpoint": "http://..."} -- config beats editing this
script, which syncs verbatim from the template. Default is a local Ollama
model (https://ollama.com); a model whose name starts with "claude-" is
instead routed to the Anthropic Messages API (needs ANTHROPIC_API_KEY,
spends cloud tokens) -- useful mainly for benchmarking cloud vs local.
Exits 0 with a notice if the chosen backend is unavailable (advisory tool,
never a close-out blocker).

Page opt-out: pages with `auto_generated: true` or `no_autolink: true` in
frontmatter are skipped, as is log.md (append-only history by convention).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

# Vaults are cloud-synced folders: the _wikilib import below must not leave
# scripts/__pycache__/_wikilib.*.pyc behind for Dropbox to push to every
# device (regression R83). Set before the import, or it is a no-op.
sys.dont_write_bytecode = True

from _wikilib import (LINK_RE, fm_flag, frontmatter, frontmatter_aliases,  # noqa: E402
                      frontmatter_value, strip_code)

DEFAULT_MODEL = "qwen2.5-coder:14b"
DEFAULT_ENDPOINT = "http://localhost:11434"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MAX_TOKENS = 4096   # headroom for Fable 5's always-on thinking
# Structured-output schema for the cloud path: forces valid {"linked": [ints]}.
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"linked": {"type": "array", "items": {"type": "integer"}}},
    "required": ["linked"],
    "additionalProperties": False,
}
CONFIG_PATH = os.path.join("_meta", "local-llm.json")
SUGGESTIONS_PATH = os.path.join("_meta", "suggestions", "links.md")
SEEN_PATH = os.path.join("_meta", "suggestions", "seen.json")
CACHE_PATH = os.path.join("_meta", "llm-link-cache.json")
BODY_CAP = 8000          # chars of prose judged (prefilter + model see the same window)
CANDIDATE_CAP = 25       # max candidates judged per page
TIMEOUT_S = 120          # per-call ceiling; healthy calls run 5-30s

SYSTEM_PROMPT = (
    "You review a wiki page for missing links. You are given the page text "
    "and a numbered list of CANDIDATE pages, each with a short description "
    "of what that page is about. Decide which candidates the page text "
    "genuinely refers to. Use each description to reject coincidental word "
    "matches: if the text uses a word in a different sense than the "
    "candidate page's subject, it is NOT a reference.\n"
    'Respond with JSON only: {"linked": [<numbers of the genuinely '
    'referenced candidates>]}. If none, return {"linked": []}.'
)


def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


def ollama_chat(endpoint: str, model: str, system: str, user: str,
                stats: dict | None = None) -> dict:
    """One chat call, forced-JSON output. Raises urllib errors upward.
    If given, `stats` accumulates calls/prompt_tokens/output_tokens."""
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": "json",
        "options": {"temperature": 0},
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        outer = json.loads(resp.read().decode("utf-8"))
    if stats is not None:
        stats["calls"] = stats.get("calls", 0) + 1
        stats["prompt_tokens"] = stats.get("prompt_tokens", 0) + outer.get("prompt_eval_count", 0)
        stats["output_tokens"] = stats.get("output_tokens", 0) + outer.get("eval_count", 0)
    try:
        return json.loads(outer["message"]["content"])
    except (KeyError, json.JSONDecodeError, TypeError):
        return {}


def anthropic_chat(model: str, system: str, user: str,
                   stats: dict | None = None) -> dict:
    """One Messages API call, structured-JSON output via output_config.format.
    Zero SDK deps (raw HTTP, matching ollama_chat). Sends no temperature /
    thinking / effort, so the identical request is valid on Fable 5 (thinking
    always on, sampling params rejected), Opus 4.8, Sonnet 5, and Haiku 4.5."""
    payload = json.dumps({
        "model": model,
        "max_tokens": ANTHROPIC_MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
    }).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        outer = json.loads(resp.read().decode("utf-8"))
    if stats is not None:
        usage = outer.get("usage", {})
        stats["calls"] = stats.get("calls", 0) + 1
        stats["prompt_tokens"] = stats.get("prompt_tokens", 0) + usage.get("input_tokens", 0)
        stats["output_tokens"] = stats.get("output_tokens", 0) + usage.get("output_tokens", 0)
    # Skip any leading thinking blocks (Fable 5 / Sonnet 5); read the text block.
    text = next((b.get("text", "") for b in outer.get("content", [])
                 if b.get("type") == "text"), "")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


def ollama_reachable(endpoint: str) -> bool:
    try:
        with urllib.request.urlopen(endpoint.rstrip("/") + "/api/tags", timeout=5):
            return True
    except (urllib.error.URLError, OSError):
        return False


def prose_window(text: str) -> str:
    """What both the prefilter and the model see: body only, code and
    wikilink spans masked (text inside an existing link must not match),
    capped at BODY_CAP so prefilter scope == model scope."""
    body = text[len(frontmatter(text)):]
    body = strip_code(body)
    body = re.sub(r"\[\[[^\]]*\]\]", " ", body)
    return body[:BODY_CAP]


def collect_pages() -> dict[str, str]:
    pages: dict[str, str] = {}
    for d, _, fs in os.walk("wiki"):
        for f in fs:
            if f.endswith(".md"):
                p = os.path.join(d, f).replace("\\", "/")
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        pages[p] = fh.read()
                except OSError:
                    pass
    return pages


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="Local-LLM unlinked-mention finder (advisory; never edits wiki pages).")
    ap.add_argument("--page", help="Only scan this page (path relative to repo root).")
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after N pages with candidates (0 = no limit).")
    ap.add_argument("--write", action="store_true",
                    help=f"Append new suggestions to {SUGGESTIONS_PATH} "
                         "(default: dry-run, print only).")
    ap.add_argument("--include-seen", action="store_true",
                    help="Also report pairs previously written (normally "
                         "suppressed so a deleted row stays rejected).")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, {})
    model = cfg.get("model", DEFAULT_MODEL)
    endpoint = cfg.get("endpoint", DEFAULT_ENDPOINT)

    # Cloud (Anthropic) models are routed by a "claude-" name prefix; anything
    # else is a local Ollama model. Config picks which; default stays local.
    is_cloud = model.startswith("claude-")
    if is_cloud:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(f"Model {model} is a cloud (Anthropic) model but "
                  "ANTHROPIC_API_KEY is not set. Nothing scanned.")
            return 0
    elif not ollama_reachable(endpoint):
        print(f"Ollama not reachable at {endpoint} -- start it (or set "
              f'"endpoint" in {CONFIG_PATH}) and re-run. Nothing scanned.')
        return 0

    pages = collect_pages()
    if not pages:
        print("No pages under wiki/ -- nothing to scan.")
        return 0

    # Vocabulary: mention text -> folder-qualified target ("entities/foo").
    # A mention naming two different pages is ambiguous -- dropped, because
    # no deterministic layer could pick the right one. Same-basename pages
    # in different folders stay distinct targets (path-style links).
    mention_targets: dict[str, set[str]] = {}
    target_of_page: dict[str, str] = {}
    description: dict[str, str] = {}
    for p, text in pages.items():
        target = p[len("wiki/"):-len(".md")]
        target_of_page[p] = target
        description[target] = frontmatter_value(text, "description")
        base = os.path.basename(target)
        for mention in [base.replace("-", " ")] + frontmatter_aliases(text):
            mention_targets.setdefault(mention.lower(), set()).add(target)
    vocab = {m: next(iter(ts)) for m, ts in mention_targets.items()
             if len(ts) == 1}
    # basename -> all targets sharing it, for alias-/basename-aware
    # already-linked checks (Obsidian resolves bare [[foo]] by basename).
    by_basename: dict[str, set[str]] = {}
    for t in target_of_page.values():
        by_basename.setdefault(os.path.basename(t).lower(), set()).add(t)

    def linked_targets(text: str) -> set[str]:
        """Targets already linked from this page: resolve each wikilink's
        bracket text by basename AND by alias/de-hyphenated title.

        KNOWN DEBT (post-regression R151): Obsidian does NOT follow a bare
        [[alias]] (it is grey; lint.py reports it under ALIAS-ONLY), so the
        alias branch below counts a grey link as "already linked" and the
        page never gets a canonical-link suggestion. Kept on purpose for
        now: the bench's CORE trap 'alias-already-linked' (expected-links.json)
        pins this behaviour, and changing it is its own reviewed change."""
        out: set[str] = set()
        for m in LINK_RE.finditer(strip_code(text)):
            raw = m.group(1).strip()
            bn = os.path.basename(raw)
            if bn.endswith(".md"):
                bn = bn[:-3]
            out |= by_basename.get(bn.lower(), set())
            out |= mention_targets.get(raw.lower(), set())
        return out

    scan = sorted(pages)
    if args.page:
        key = args.page.replace("\\", "/")
        if key not in pages:
            print(f"Page not found: {key}")
            return 0
        scan = [key]

    seen: set[str] = set(load_json(SEEN_PATH, []))
    stats: dict = {}
    cache: dict = load_json(CACHE_PATH, {})
    cache_dirty = False
    suggestions: list[dict] = []   # {page, target, mention}
    suppressed = 0
    scanned = 0

    for p in scan:
        text = pages[p]
        if fm_flag(text, "auto_generated") or fm_flag(text, "no_autolink"):
            continue
        if os.path.basename(p) == "log.md":
            continue  # append-only history; retro-linking it is noise
        window = prose_window(text)
        window_l = window.lower()
        already = linked_targets(text) | {target_of_page[p]}

        # Layer 1: deterministic prefilter (whole-word, masked window).
        candidates: list[tuple[str, str]] = []   # (target, mention)
        picked: set[str] = set()
        for mention, target in vocab.items():
            if target in already or target in picked or len(mention) < 4:
                continue
            if re.search(r"\b" + re.escape(mention) + r"\b", window_l):
                candidates.append((target, mention))
                picked.add(target)
        candidates.sort()
        dropped = max(0, len(candidates) - CANDIDATE_CAP)
        candidates = candidates[:CANDIDATE_CAP]
        if not candidates:
            continue

        scanned += 1
        note = f" ({dropped} over cap dropped)" if dropped else ""
        print(f"-- {p} ({len(candidates)} candidates{note}) ...", flush=True)

        # Layer 2: the LLM picks genuine references (cached).
        cand_lines = "\n".join(
            f"{i + 1}. [[{t}]] -- {description.get(t) or '(no description)'} "
            f"(mentioned as: \"{m}\")"
            for i, (t, m) in enumerate(candidates))
        key = hashlib.sha256(
            (model + "\x00" + window + "\x00" + cand_lines).encode("utf-8")
        ).hexdigest()
        if key in cache:
            picks = cache[key]
        else:
            user = f"CANDIDATES:\n{cand_lines}\n\nPAGE TEXT:\n{window}"
            try:
                if is_cloud:
                    result = anthropic_chat(model, SYSTEM_PROMPT, user, stats=stats)
                else:
                    result = ollama_chat(endpoint, model, SYSTEM_PROMPT, user,
                                         stats=stats)
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                print(f"   LLM call failed ({e}); skipping page.")
                continue
            raw = result.get("linked", []) if isinstance(result, dict) else []
            picks = [int(n) for n in raw
                     if isinstance(n, (int, float, str)) and str(n).isdigit()]
            cache[key] = picks
            cache_dirty = True

        # Layer 3: deterministic validation -- picks must index candidates.
        for n in picks:
            if not 1 <= n <= len(candidates):
                continue
            target, mention = candidates[n - 1]
            pair = f"{p}|{target}"
            if pair in seen and not args.include_seen:
                suppressed += 1
                continue
            suggestions.append({"page": p, "target": target,
                                "mention": mention, "pair": pair})
            print(f"   mention \"{mention}\" -> [[{target}]]")

        if args.limit and scanned >= args.limit:
            print(f"(--limit {args.limit} reached)")
            break

    if cache_dirty:
        save_json(CACHE_PATH, cache)

    print()
    print(f"=== LLM === {stats.get('calls', 0)} calls, "
          f"{stats.get('prompt_tokens', 0)} prompt + "
          f"{stats.get('output_tokens', 0)} output tokens "
          "(cached pages cost 0)")
    print(f"=== SUGGESTIONS === {len(suggestions)} "
          f"(from {scanned} pages with candidates; model {model}; "
          f"{suppressed} previously-seen suppressed)")

    if suggestions and args.write:
        new_file = not os.path.exists(SUGGESTIONS_PATH)
        os.makedirs(os.path.dirname(SUGGESTIONS_PATH), exist_ok=True)
        today = datetime.date.today().isoformat()
        with open(SUGGESTIONS_PATH, "a", encoding="utf-8") as fh:
            if new_file:
                fh.write("---\ntitle: link suggestions (local LLM)\n"
                         "description: unlinked-mention suggestions from "
                         "llm_suggest_links.py; approve by making the edit, "
                         "reject by deleting the row (rejection is remembered "
                         "in seen.json).\nauto_generated: true\n---\n\n"
                         "# Link suggestions\n")
            fh.write(f"\n## {today} (model {model})\n\n")
            for s in suggestions:
                fh.write(f"- [ ] `{s['page']}`: mention \"{s['mention']}\" -> "
                         f"`[[{s['target']}]]`\n")
        seen |= {s["pair"] for s in suggestions}
        save_json(SEEN_PATH, sorted(seen))
        print(f"Appended to {SUGGESTIONS_PATH}; pairs recorded in {SEEN_PATH}")
    elif suggestions:
        print(f"(dry-run -- pass --write to record these in {SUGGESTIONS_PATH})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
