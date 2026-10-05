#!/usr/bin/env python3
"""Shared wiki-parsing helpers for the scripts/ scanners.

Single source of truth for the vocabulary every scanner needs: the wikilink
grammar, link resolution against the vault inventory, the frontmatter
boundary, alias resolution, and boolean frontmatter flags. Before this module
existed the wikilink regex had five diverged copies across scripts/ and the
auto_generated check had three -- fixes to one copy silently missed the
others. New scripts import from here; existing scanners migrate
opportunistically when next touched.

`lint.py` is the canonical consumer of iter_wikilinks/build_index/resolve: it
is the pass whose link numbers the fleet trusts, so the library must produce
lint's answers exactly. Two ad-hoc reimplementations written in one afternoon
(regression R80) produced two DIFFERENT wrong answers -- one read backticked
examples in an append-only log as live links, the other appended ".md" to a
target that already had one. The failure mode is not forgetting one rule; it
is that the rule set is open-ended and every reimplementation rediscovers a
different subset. So: a link-finding pass written for one session imports
from here, or it is not evidence.

Stdlib only. Synced from the WIKIllm template via sync_from_template.py --
improve it there first.
"""

from __future__ import annotations

import json
import os
import re
from typing import Iterator, NamedTuple

# Wikilink target extraction. Target excludes backslash so the table-cell
# escaped pipe ([[foo\|Foo]], the CLAUDE.md convention) isn't absorbed into
# the target; the display alternative allows the optional backslash before |.
# Group 1 = target (may include a path and/or omit .md), like Obsidian.
# Group 2 = the anchor (#heading or #^block-id), group 3 = display text --
# both optional. Group 1's subpattern is unchanged from before regression R80, so
# existing finditer/group(1) callers see exactly what they saw before.
LINK_RE = re.compile(r"\[\[([^\]|#\\]+)(?:#([^\]|]*))?(?:\\?\|([^\]]*))?\]\]")


def frontmatter(text: str) -> str:
    """The YAML frontmatter block (including fences), or '' if none."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[:end] if end != -1 else ""


def frontmatter_value(text: str, key: str) -> str:
    """Single-line frontmatter value for `key` ('' if absent). No YAML lists."""
    for line in frontmatter(text).splitlines():
        if line.lower().startswith(key.lower() + ":"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def fm_flag(text: str, key: str) -> bool:
    """True when frontmatter has `key: true` (case/space-insensitive)."""
    for line in frontmatter(text).splitlines():
        if line.lower().replace(" ", "").startswith(key.lower() + ":true"):
            return True
    return False


def frontmatter_aliases(text: str) -> list[str]:
    """Values of the frontmatter `aliases:` key (inline [a, b] or block list).
    Obsidian resolves [[alias]] vault-wide via this key, so scanners must too."""
    fm = frontmatter(text)
    out: list[str] = []
    lines = fm.splitlines()
    for i, line in enumerate(lines):
        if not line.lower().startswith("aliases:"):
            continue
        val = line.split(":", 1)[1].strip()
        if val.startswith("["):
            try:  # JSON-style list survives commas inside quoted aliases
                out += [str(v) for v in json.loads(val)]
            except (json.JSONDecodeError, TypeError):
                out += [v.strip().strip("'\"") for v in val.strip("[]").split(",")]
        elif val:
            out.append(val.strip("'\""))
        else:
            for nxt in lines[i + 1:]:
                m = re.match(r"\s+-\s+(.+)", nxt)
                if not m:
                    break
                out.append(m.group(1).strip().strip("'\""))
    return [a for a in out if a]


def strip_code(text: str) -> str:
    """Remove fenced and inline code spans (links there aren't real edges)."""
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"``[^\n]+?``", "", text)
    return re.sub(r"`[^`\n]+`", "", text)


class Wikilink(NamedTuple):
    """One [[link]] occurrence. `anchor` and `display` are '' when absent."""
    target: str
    anchor: str
    display: str
    span: tuple[int, int]


def iter_wikilinks(text: str, strip: bool = True) -> Iterator[Wikilink]:
    """Yield every [[wikilink]] in `text` as (target, anchor, display, span).

    Handles, in one place, the rules each ad-hoc reimplementation rediscovered
    a different subset of (regression R80): fenced and inline code spans (single
    AND double backtick) are not edges, `#heading` / `#^block-id` suffixes and
    `\\|` display-text escapes are split off the target, and the target is
    returned verbatim -- an explicit `.md` is the caller's (or resolve()'s)
    business, not stripped here.

    Known gap, stated so nobody rediscovers it: a SAME-NOTE link `[[#Heading]]`
    has no target and is not yielded at all (LINK_RE requires one non-`#`
    character). Widening the pattern would make it a target-less link that
    every consumer would have to special-case, so a caller that needs same-note
    links finds them itself.

    `strip=True` (the default) scans strip_code(text), so **spans index the
    STRIPPED text, not the original**. A caller that rewrites the file in place
    must pass strip=False and deal with code spans itself.
    """
    scanned = strip_code(text) if strip else text
    for m in LINK_RE.finditer(scanned):
        yield Wikilink(m.group(1).strip(), (m.group(2) or "").strip(),
                       (m.group(3) or "").strip(), m.span())


def build_index(paths: list[str], texts: dict[str, str]) -> dict[str, list[str]]:
    """Vault inventory for resolve(): lowercased page name -> paths naming it.

    Obsidian resolves [[foo]] by basename, case-insensitively, vault-wide, and
    honours frontmatter `aliases:` -- so a resolver needs the whole inventory,
    not just the one file it is looking at. `paths` are the vault-relative .md
    paths to index (already filtered by the caller); `texts` maps path -> text.
    A name with more than one path is a basename collision (regression R76): the
    list is returned whole rather than picking a winner.
    """
    index: dict[str, list[str]] = {}
    for p in paths:
        name = os.path.basename(p)[:-3]
        index.setdefault(name.lower(), []).append(p)
        for alias in frontmatter_aliases(texts.get(p, "")):
            if alias.lower() != name.lower():
                index.setdefault(alias.lower(), []).append(p)
    return index


def resolve(target: str, from_path: str, index: dict[str, list[str]],
            root: str = ".") -> tuple[str, list[str]]:
    """Resolve one page wikilink target. Returns (status, paths):

      "resolved"    -- `paths` is the non-empty list of vault pages the target
                       names (more than one = basename collision, regression R76)
      "cross-vault" -- the target leaves the vault via `../` and the file
                       exists on disk; `paths` is []
      "broken"      -- nothing matched; `paths` is []

    The order is the load-bearing part, and matches Obsidian: an explicit
    ".md" is trimmed before the lookup (so `[[../CLAUDE.md]]` resolves -- a
    scanner that appended ".md" unconditionally filed two false hits on it),
    and the basename lookup wins over the path, so a wrong-depth `../` link
    naming a real page still resolves (that matches Obsidian, so do not
    "fix" it here -- lint.py's RELATIVE-DEPTH section checks every `../`
    target on disk separately, regression R76). Only a target the index cannot place is checked on disk,
    relative to `from_path` inside `root`.

    Pass a bare target -- iter_wikilinks has already split off anchor and
    display. Non-.md embeds (![[chart.png]]) are an asset-inventory question
    and belong to the caller; filter them out before calling.
    """
    bn = os.path.basename(target)
    if bn.endswith(".md"):
        bn = bn[:-3]
    candidates = index.get(bn.lower(), [])
    if candidates:
        return "resolved", candidates
    if "../" in target:
        if not bn.strip("."):          # [[../..]] and friends name no page
            return "broken", []
        candidate = os.path.normpath(
            os.path.join(root, os.path.dirname(from_path), target))
        candidate_md = (candidate if candidate.lower().endswith(".md")
                        else candidate + ".md")
        if os.path.isfile(candidate_md):
            return "cross-vault", []
    return "broken", []
