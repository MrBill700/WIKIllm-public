# Vendored third-party skills

These skill folders are **not authored here** — they are copied ("vendored") from an upstream project and distributed to wiki instances via `scripts/sync_from_template.py`. Do **not** hand-edit them; local edits are overwritten on the next sync. To update, re-pull from upstream, replace the folder here, and re-sync.

| Skill | Upstream | License | Purpose |
|---|---|---|---|
| `obsidian-bases/` | [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) | MIT | Create/edit Obsidian `.base` files (database views, filters, formulas). |
| `defuddle/` | [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) | MIT | Extract clean markdown from web pages. **Requires the `defuddle` npm CLI** (`npm install -g defuddle`) — the skill is docs only. |

Vendored 2026-07-09 (obsidian-bases, defuddle). The wiki's own `ingest/` skill is authored here and is not third-party.

## Local divergences (re-apply after any re-vendor)

Deliberate template-side edits to vendored folders. **The re-vendor procedure above
(replace the folder) silently drops these** -- whoever re-pulls upstream must re-apply
each row (or confirm upstream absorbed it) before committing the refresh:

| Skill | Divergence | Why / where |
|---|---|---|
| `obsidian-bases/SKILL.md` | View schema documents the `sort:` key (list of `{property, direction}`, composes with `groupBy`) + a verification note after the schema fence | Upstream omits it; verified working 2026-08-12 (regression R44; not yet upstreamed). Check whether upstream has since added `sort:` -- if so, drop this row. |
