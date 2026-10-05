# data/ — structured records + Bases

One small note per datapoint (typed frontmatter, `tags: [<type>, data]`) plus Obsidian `.base` files that render them as live sortable/filterable tables, embedded into `analysis/` pages with `![[data/<type>.base]]`.

Use this for anything you'd otherwise hand-maintain as a growing markdown table — races, experiments, releases, readings, transactions. **Add a note → the table updates itself.**

- **Syntax reference:** the `obsidian-bases` skill (`.claude/skills/obsidian-bases/`).
- **Pattern + copy-paste `.base` skeleton:** the "Structured data & Bases" section of the WIKIllm template `CLAUDE.md` (and this wiki's `CLAUDE.md` if it has been filled in).
- Renders in **Obsidian 1.9+** only; a plain-markdown viewer shows the embed line, not a grid.

Empty by default — delete this README once you have real records if you prefer.
