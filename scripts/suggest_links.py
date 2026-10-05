#!/usr/bin/env python3
"""Suggest missing wikilinks + surface weak spots in the wiki graph.

lint.py answers "is the graph broken?" (broken links, orphans, ghosts).
This script answers "is the graph under-connected?" — a curation aid, not a
close-out gate. Adapted from graphify's algorithmic "surprising connections"
report (github.com/safishamsi/graphify): build the wikilink graph, cluster it
with Louvain community detection, then report:

  1. WEAKLY CONNECTED  — pages with exactly one inbound link (one deletion
     away from orphanhood; lint.py only catches zero-inbound orphans).
  2. CANDIDATE MISSING LINKS — same-community page pairs with no direct link,
     ranked by Adamic-Adar (many shared neighbors => probably should link;
     hub neighbors are discounted, so index-style pages don't drown the signal).
  3. MISSING BACK-REFERENCES — one-way pairs between wiki content pages where
     A links B two or more times but B never links back. Added after
     a real miss: a plan page linked a strategy page heavily, the
     strategy page never pointed back at the plan, and report
     #2 couldn't see it (any edge in either direction makes a pair "connected").
     Scoped to wiki/ minus sources/ — source pages and _meta trackers cite
     content pages one-way by design.
  4. SINGLE-EDGE BRIDGES — topic-cluster pairs connected by exactly one link:
     either a fragile seam worth reinforcing or a genuinely interesting
     connection worth strengthening.

Catalog pages (wiki/index.md, wiki/log.md, CLAUDE.md) are excluded from the
graph — they link to nearly everything and would flatten the community
structure.

Advisory only — never edits anything, always exits 0. Every line is a
*candidate* for a human/session to judge; "no link" is sometimes correct.

Run: python scripts/suggest_links.py
Requires: networkx >= 3.0 (pip install networkx)
"""

from __future__ import annotations

import collections
import io
import os
import re
import sys

try:
    import networkx as nx
except ImportError:
    sys.exit("networkx not installed — run: pip install networkx")

# Same scope as lint.py, minus the catalogs (they link everything).
ROOTS = ["wiki", "_meta", "scripts"]
EXCLUDE = {"wiki/index.md", "wiki/log.md", "CLAUDE.md"}
GHOSTS = {"date", "published"}

LINK_RE = re.compile(r"\[\[([^\]|#\\]+)(?:#[^\]|]*)?(?:\\?\|[^\]]*)?\]\]")  # backslash-aware: [[foo\|Foo]] targets foo

TOP_MISSING = 20          # candidate missing links to report
MIN_SHARED = 2            # min shared neighbors for a candidate pair
MIN_COMMUNITY = 4         # communities smaller than this skip pair analysis


def short(p: str) -> str:
    return p[:-3] if p.endswith(".md") else p


def collect_files() -> list[str]:
    files = []
    for root in ROOTS:
        for d, _, fs in os.walk(root):
            for f in fs:
                if f.endswith(".md"):
                    p = os.path.join(d, f).replace("\\", "/")
                    if p not in EXCLUDE:
                        files.append(p)
    return files


def build_graph(files: list[str]) -> nx.DiGraph:
    basename: dict[str, list[str]] = {}
    for p in files:
        basename.setdefault(os.path.basename(p)[:-3], []).append(p)

    D = nx.DiGraph()
    D.add_nodes_from(files)
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            continue
        # Strip code fences + inline code so quoted [[links]] don't count.
        cleaned = re.sub(r"```[\s\S]*?```", "", text)
        cleaned = re.sub(r"``[^\n]+?``", "", cleaned)
        cleaned = re.sub(r"`[^`\n]+`", "", cleaned)
        for m in LINK_RE.finditer(cleaned):
            target = m.group(1).strip()
            if target in GHOSTS or target.startswith("#"):
                continue
            bn = os.path.basename(target)
            if bn.endswith(".md"):
                bn = bn[:-3]
            for c in basename.get(bn, []):
                if c != p:
                    if D.has_edge(p, c):
                        D[p][c]["weight"] += 1
                    else:
                        D.add_edge(p, c, weight=1)
    return D


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    files = collect_files()
    D = build_graph(files)
    G = D.to_undirected()

    # --- Communities (deterministic: fixed seed + size/lexical ordering) ---
    raw = nx.community.louvain_communities(G, seed=42)
    communities = sorted((sorted(c) for c in raw), key=lambda c: (-len(c), c[0]))
    node_cid = {n: i for i, c in enumerate(communities) for n in c}

    def label(cid: int) -> str:
        members = communities[cid]
        top = max(members, key=lambda n: (G.degree(n), n))
        return f"C{cid} [{short(top)}] ({len(members)} pages)"

    print(f"=== GRAPH === {G.number_of_nodes()} pages, {G.number_of_edges()} link pairs, "
          f"{len(communities)} communities (catalogs excluded: {', '.join(sorted(EXCLUDE))})")
    print()
    print("=== COMMUNITIES (labeled by highest-degree member) ===")
    for i, c in enumerate(communities):
        if len(c) >= MIN_COMMUNITY:
            print(f"  {label(i)}")
    small = sum(1 for c in communities if len(c) < MIN_COMMUNITY)
    if small:
        print(f"  (+ {small} fragments of <{MIN_COMMUNITY} pages)")
    print()

    # --- 1. Weakly connected: exactly one inbound link ---
    weak = sorted(n for n in D.nodes() if D.in_degree(n) == 1)
    print(f"=== WEAKLY CONNECTED (exactly 1 inbound link) === {len(weak)}")
    for n in weak:
        linker = next(iter(D.predecessors(n)))
        print(f"  {short(n)}  <-  only {short(linker)}")
    print()

    # --- 2. Candidate missing links: same community, no edge, high Adamic-Adar ---
    candidates = []
    for c in communities:
        if len(c) < MIN_COMMUNITY:
            continue
        pairs = [(u, v) for i, u in enumerate(c) for v in c[i + 1:] if not G.has_edge(u, v)]
        for u, v, score in nx.adamic_adar_index(G, pairs):
            shared = sorted(nx.common_neighbors(G, u, v))
            if len(shared) >= MIN_SHARED:
                candidates.append((score, u, v, shared))
    candidates.sort(key=lambda t: (-t[0], t[1], t[2]))
    print(f"=== CANDIDATE MISSING LINKS (same community, ≥{MIN_SHARED} shared neighbors, "
          f"top {TOP_MISSING}) === {min(len(candidates), TOP_MISSING)} of {len(candidates)}")
    for score, u, v, shared in candidates[:TOP_MISSING]:
        ex = ", ".join(short(s) for s in shared[:3])
        more = f" +{len(shared) - 3}" if len(shared) > 3 else ""
        print(f"  {score:5.2f}  {short(u)}  <->  {short(v)}")
        print(f"         shared: {ex}{more}")
    print()

    # --- 3. Missing back-references: A links B repeatedly, B never links A ---
    def content_page(p: str) -> bool:
        return p.startswith("wiki/") and not p.startswith("wiki/sources/")

    oneway = sorted(
        ((d["weight"], u, v) for u, v, d in D.edges(data=True)
         if d["weight"] >= 2 and not D.has_edge(v, u)
         and content_page(u) and content_page(v)),
        key=lambda t: (-t[0], t[1], t[2]),
    )
    # Diversity cap: max 2 rows per linker page, else aggregator-style pages
    # (tactical-levers, blind-spots, ...) drown the list — they cite half the
    # wiki by design and usually don't need reciprocal links.
    shown, per_linker = [], collections.Counter()
    for w, u, v in oneway:
        if per_linker[u] < 2:
            shown.append((w, u, v))
            per_linker[u] += 1
        if len(shown) >= TOP_MISSING:
            break
    print(f"=== MISSING BACK-REFERENCES (A links B ≥2×, B never links back; "
          f"wiki content pages only; ≤2 rows per linker) === showing "
          f"{len(shown)} of {len(oneway)}")
    for w, u, v in shown:
        print(f"  {short(v)}  never links back to  {short(u)}  (which links it ×{w})")
    print()

    # --- 4. Single-edge bridges between communities ---
    between: dict[tuple[int, int], list[tuple[str, str]]] = collections.defaultdict(list)
    for u, v in G.edges():
        cu, cv = node_cid[u], node_cid[v]
        if cu != cv:
            between[tuple(sorted((cu, cv)))].append((u, v))
    bridges = sorted(
        (k, es[0]) for k, es in between.items()
        if len(es) == 1
        and len(communities[k[0]]) >= MIN_COMMUNITY
        and len(communities[k[1]]) >= MIN_COMMUNITY
    )
    print(f"=== SINGLE-EDGE BRIDGES (two clusters joined by exactly one link) === {len(bridges)}")
    for (ca, cb), (u, v) in bridges:
        print(f"  {label(ca)}  <->  {label(cb)}")
        print(f"         via: {short(u)} <-> {short(v)}")
    print()
    print("Advisory only — every line is a candidate, not a defect. 'No link' is sometimes right.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
