# Third-party notices

Original code and documentation in this repository are available under the MIT license in LICENSE. Bundled third-party material retains its own copyright and license.

## Obsidian skills

The following files are derived from [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills):

- .claude/skills/obsidian-bases/SKILL.md
- .claude/skills/obsidian-bases/references/FUNCTIONS_REFERENCE.md
- .claude/skills/defuddle/SKILL.md

Copyright (c) 2026 Steph Ango (@kepano). MIT license; the full copyright and permission notice is reproduced in [licenses/obsidian-skills-MIT.txt](licenses/obsidian-skills-MIT.txt). Local modifications are described in .claude/skills/VENDORED.md. That provenance record does not pin an upstream revision; no exact revision is asserted here.

Obsidian formatting documentation links to the original Obsidian help pages. Optional external tools and services are not bundled and retain their own terms and licenses.

## Pattern inspiration

The repository's wiki-maintenance approach was inspired by [Andrej Karpathy's LLM Wiki idea](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). `_meta/llm-wiki.md` is an independently written description of this implementation; the original essay is not redistributed or relicensed here.

scripts/suggest_links.py reimplements the surprising-connections report idea from [safishamsi/graphify](https://github.com/safishamsi/graphify) (networkx Louvain + Adamic-Adar); no graphify code is copied.
