#!/usr/bin/env python3
"""Shared wiki-parsing helpers for the scripts/ scanners.

Single source of truth for the vocabulary every scanner needs: the wikilink
grammar, link resolution against the vault inventory, the frontmatter
boundary, alias lookup, and boolean frontmatter flags. Before this module
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

Aliases are indexed, but they are NOT Obsidian link targets (regression R151,
measured 2026-09-29): clicking a bare [[alias]] in Obsidian creates a new
empty note named after the alias -- it does not open the page whose
frontmatter carries it. build_index() still indexes aliases (callers use them
to find the page a link was MEANT for), and resolve() still returns alias
holders as "resolved"; a caller asking "will Obsidian open this?" must
classify the hit with link_kind() / canonical_paths(). Treating the two as
equivalent was a validator defect: lint called over 90% of one large
vault's resolved links healthy while Obsidian showed them grey.

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

    Obsidian uses this key for search / quick switcher / link suggestions, but
    does NOT resolve a bare [[alias]] link through it -- clicking one creates
    a new empty note (regression R151, measured 2026-09-29). Scanners read aliases
    to find the page a link was MEANT for, not to call the link healthy."""
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

    Obsidian resolves [[foo]] by basename, case-insensitively, vault-wide, so a
    resolver needs the whole inventory, not just the one file it is looking
    at. Frontmatter `aliases:` are indexed too, as extra keys pointing at the
    page that carries them -- but Obsidian does NOT open a page from a bare
    [[alias]] (regression R151): an alias key names the page the link was meant
    for, not one Obsidian will open. Use link_kind() / canonical_paths() to
    tell a canonical basename hit from an alias-only one. `paths` are the vault-relative .md
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


def lookup_key(target: str) -> str:
    """The index key resolve() looks a bare target up by: basename, explicit
    `.md` trimmed (case-sensitive, as resolve() always has), lowercased.
    Shared so canonical_paths() classifies exactly the key resolve() used."""
    bn = os.path.basename(target)
    if bn.endswith(".md"):
        bn = bn[:-3]
    return bn.lower()


def basename_keys(paths: list[str]) -> set[str]:
    """Lowercased page basenames (no `.md`) of `paths` -- the names Obsidian
    will actually open a [[link]] by. Alias keys are NOT in it (regression R151)."""
    return {os.path.basename(p)[:-3].lower() for p in paths}


def vault_md_basenames(root: str = ".") -> set[str]:
    """Lowercased basenames (no `.md`) of EVERY .md file under `root`
    outside dot-directories -- raw/, templates/ and any top-level folder
    included. Obsidian resolves [[name]] against all of them, so a name in
    this set is canonical even when its file is off the scanner's inventory
    (regression R151). Dot-directories are pruned: .obsidian/, .trash/, and
    .alias-fix-backup/, which holds a copy of every page the alias fixer
    edited. Pass the result to link_kind(vault_basenames=...)."""
    names: set[str] = set()
    for _d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in fs:
            if f.lower().endswith(".md"):
                names.add(f[:-3].lower())
    return names


def canonical_paths(target: str, candidates: list[str]) -> list[str]:
    """The subset of resolve()'s `candidates` whose own basename is the
    looked-up name -- the INVENTORY page(s) Obsidian opens on click, i.e. the
    ones to credit inbound. Empty when the target matched only through
    frontmatter aliases (regression R151) -- and also when Obsidian opens a file
    that is not in the index (raw/, templates/): that case has no inventory
    path to return, so "will Obsidian open this?" is link_kind()'s question,
    with vault_basenames. When a basename file AND alias holders share the
    key, only the basename file is returned: Obsidian opens that one, the
    alias holders get nothing."""
    key = lookup_key(target)
    return [c for c in candidates if os.path.basename(c)[:-3].lower() == key]


def link_kind(target: str, candidates: list[str],
              vault_basenames: set[str] | None = None) -> str:
    """'canonical' when Obsidian opens a page for this resolved target, else
    'alias-only' -- resolve() matched it only via frontmatter aliases, and
    Obsidian shows it grey and creates an empty note on click (regression R151).
    Pass the non-empty candidate list from a "resolved" resolve().

    Canonical = the name equals the basename of at least one candidate, OR
    (when `vault_basenames` is given, see vault_md_basenames()) the name is
    the basename of any .md in the vault. Without `vault_basenames` the
    result ignores .md files that are not in the index (raw/, templates/), so
    a name that is both such a file and some page's alias reads 'alias-only'
    although Obsidian opens the file -- lint passes the set."""
    if canonical_paths(target, candidates):
        return "canonical"
    if vault_basenames is not None and lookup_key(target) in vault_basenames:
        return "canonical"
    return "alias-only"


def resolve(target: str, from_path: str, index: dict[str, list[str]],
            root: str = ".") -> tuple[str, list[str]]:
    """Resolve one page wikilink target. Returns (status, paths):

      "resolved"    -- `paths` is the non-empty list of vault pages the target
                       names (more than one = basename collision, regression R76).
                       Includes pages matched ONLY via a frontmatter alias,
                       which Obsidian does not open (regression R151) -- classify
                       with link_kind() / canonical_paths()
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
    candidates = index.get(lookup_key(target), [])
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


LINT_ROOTS = ("wiki", "_meta", "scripts")


def lint_is_auto_generated(text: str) -> bool:
    """lint.py's own auto_generated test, kept byte-for-byte: it reads
    text[:text.find("\n---", 3)], which on an opening `---` with no closing
    fence is the whole body -- unlike fm_flag(). Moving the _meta/ exclusion
    to fm_flag() would move ALL FILES / ORPHANS (regression R80 parity)."""
    if not text.startswith("---"):
        return False
    fm = text[:text.find("\n---", 3)]
    return any(l.lower().replace(" ", "").startswith("auto_generated:true")
               for l in fm.splitlines())


def lint_inventory(root: str = ".") -> dict:
    """The inventory lint.py resolves links against, built exactly as lint
    builds it (regression R151: shared so fix_wikilinks can predict lint's verdict
    on every link it rewrites, instead of a second copy of the rules).

    Returns {"files": lint's file list in lint's walk order (vault-relative,
    '/'-separated; _meta/ auto_generated pages dropped), "texts": path ->
    text (utf-8-sig; "" when unreadable), "asset_names": non-.md basenames
    under wiki/ _meta/ scripts/ raw/ (case-sensitive, as lint compares them),
    "index": build_index(files, texts), "vault_bn": vault_md_basenames(root),
    "root": root}. With root "." the paths are exactly lint's historical ones.
    """
    files: list[str] = []
    asset_names: set[str] = set()
    for r in LINT_ROOTS + ("raw",):
        base = r if root == "." else os.path.join(root, r)
        for d, _, fs in os.walk(base):
            for f in fs:
                if f.endswith(".md"):
                    if r != "raw":
                        p = os.path.join(d, f)
                        if root != ".":
                            p = os.path.relpath(p, root)
                        files.append(p.replace("\\", "/"))
                else:
                    asset_names.add(f)
    if os.path.exists(os.path.join(root, "CLAUDE.md")):
        files.append("CLAUDE.md")
    texts: dict[str, str] = {}
    for p in files:
        try:
            # utf-8-sig: a BOM before `---` would otherwise hide the page's
            # frontmatter (and its aliases).
            with open(os.path.join(root, p), "r", encoding="utf-8-sig") as fh:
                texts[p] = fh.read()
        except Exception:
            texts[p] = ""
    files = [p for p in files
             if not (p.startswith("_meta/") and lint_is_auto_generated(texts[p]))]
    return {"files": files, "texts": texts, "asset_names": asset_names,
            "index": build_index(files, texts), "vault_bn": vault_md_basenames(root),
            "root": root}


def lint_link_kind(target: str, from_path: str, inv: dict) -> tuple[str, list[str]]:
    """lint.py's verdict on ONE link, in lint's branch order. `target` is
    iter_wikilinks' (stripped, anchor/display split off); `inv` is
    lint_inventory(). Returns (kind, credit) where kind is one of

      "ghost"        -- [[date]] / [[published]] clipper placeholder
      "skip"         -- target starts with '#' (vestigial)
      "attachment"   -- non-.md extension whose basename is a known asset
      "broken"       -- anything lint files under BROKEN LINKS, including a
                        dotted note name whose os.path.splitext() reads as an
                        extension (regression R148) and a '/' or ':' name that
                        os.path.basename() splits (regression R149, platform-dependent)
      "canonical"    -- Obsidian opens a page; `credit` = inventory pages
                        credited inbound (empty for an off-inventory file)
      "alias-only"   -- resolved only through frontmatter aliases (regression R151)
      "cross-vault"  -- ../ target that exists outside the vault

    lint.py calls this for every link, so the answer is lint's by definition.
    """
    if target in ("date", "published"):
        return "ghost", []
    if target.startswith("#"):
        return "skip", []
    ext = os.path.splitext(os.path.basename(target))[1]
    if ext and ext.lower() != ".md":
        if os.path.basename(target) not in inv["asset_names"]:
            return "broken", []
        return "attachment", []
    status, candidates = resolve(target, from_path, inv["index"], inv["root"])
    if status == "resolved":
        canon = canonical_paths(target, candidates)
        if canon:
            return "canonical", canon
        if link_kind(target, candidates, inv["vault_bn"]) == "canonical":
            return "canonical", []
        return "alias-only", []
    if status == "cross-vault":
        return "cross-vault", []
    return "broken", []
