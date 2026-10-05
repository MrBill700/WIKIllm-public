---
title: Obsidian syntax reference
description: Distilled Obsidian formatting features available in this vault — what to use when authoring wiki pages
tags: [meta, reference]
---

# Obsidian syntax reference

Distilled from the official Obsidian help pages. This page exists so an LLM session (or human) can answer "what formatting can I use on a wiki page?" without hunting. Apply it to wiki pages here; the high-leverage subset is also baked into the `CLAUDE.md` conventions.

## Linking — the part most relevant to this wiki

Obsidian wikilinks support far more than `[[page]]`. The graph view rewards using the richer forms.

| Form | Meaning |
| --- | --- |
| `[[page]]` | Link to a note. Used everywhere in this wiki. |
| `[[page#Heading]]` | Link to a heading inside another note. |
| `[[#Heading]]` | Link to a heading inside the *current* note. |
| `[[page#Heading#Subheading]]` | Drill multiple heading levels. |
| `[[page#^block-id]]` | Link to a specific block (paragraph, list item, callout). Block id is auto-generated when you type `^`, or you can write a human-readable one like `^bridge-2042`. |
| `[[page\|Display text]]` | Override displayed text for this one link. |
| `[[## term]]` | Search for any heading across the vault matching `term`. |
| `[[^^term]]` | Search for any block across the vault matching `term`. |
| `![[page]]` / `![[image.png]]` | Embed the linked content/image inline. |

Block IDs (the `^id` suffix) only consist of Latin letters, numbers, and dashes. Block references are Obsidian-specific and won't render outside Obsidian. Within a wiki we control end-to-end, that's fine — use them when you want to cite a specific paragraph (e.g. a single quote) rather than the whole page.

**A link must name the page's filename (slug), never an alias.** Obsidian does NOT resolve a bare `[[Other Name]]` against a page's `aliases:` -- the link is grey and clicking it creates a new empty note (regression R151, confirmed by a click test). Frontmatter `aliases: [Other Name]` serve search, the quick switcher and link suggestions only. To show the alternate name, write `[[slug|Other Name]]`. `lint.py` reports bare alias links under ALIAS-ONLY; `python scripts/fix_wikilinks.py --alias-only` rewrites the uniquely resolvable ones.

**Invalid characters in link targets:** `# | ^ : %% [[ ]]`. Avoid these in filenames.

## Callouts — for warnings, decisions, open questions

Syntax: a blockquote whose first line is `[!type] Optional title`. We should standardize a few uses on this wiki:

```md
> [!warning] Source conflict
> Two sources give different dates for this event; verify against the primary before citing.
```

Useful types (case-insensitive; aliases shown):
- `note` (default), `info`, `tip` (`hint`, `important`)
- `abstract` (`summary`, `tldr`) — good for page-top TL;DR
- `warning` (`caution`, `attention`), `danger` (`error`), `failure` (`fail`, `missing`)
- `question` (`help`, `faq`) — good for open questions
- `todo` — pending action items
- `success` (`check`, `done`) — resolved decisions
- `example`, `quote` (`cite`), `bug`

Foldable: append `+` (open) or `-` (collapsed) to the type — `[!faq]-`. Useful for long resolved-question sections that should default-collapse.

Nested callouts work (`> > [!todo]`) but get visually noisy fast.

## Block-level structures

- **Headings:** `#` through `######`. The page's H1 should match the frontmatter `title`.
- **Tables:** standard Markdown pipes; align with `:--`, `:--:`, `--:` in the header separator. **Inside a table cell, escape pipes that appear in wikilink display text or image-resize syntax** — e.g. `[[concepts/foo\|Foo]]`, `![[image.png\|200]]`.
- **Lists:** `-`, `*`, or `+` for unordered; `1.` or `1)` for ordered. Mix freely when nesting. Task lists: `- [ ]` / `- [x]`.
- **Footnotes:** `text[^1]` with `[^1]: definition` later in the doc. Named footnotes (`[^source-a]`) make references easier to track. Inline footnotes `^[like this]` only render in reading view.
- **Code blocks:** triple backticks; specify language for syntax highlighting (`md`, `python`, `js`, etc.). To nest a fenced code block inside another, the outer fence must use **more** backticks than the inner — `` ```` `` outside, ```` ``` ```` inside.
- **Highlights:** `==text==`. Useful for marking unverified claims or fields-needing-update.
- **Strikethrough:** `~~text~~`. Useful for superseded claims that you want to preserve visibly rather than delete.
- **Horizontal rule:** `---` (also valid as YAML frontmatter delimiter — only ambiguous on line 1).

## Diagrams — Mermaid

Fenced ` ```mermaid ` block. Supports flowcharts, sequence diagrams, timelines, gantt, etc. Useful here for:
- Phase or project timelines
- Decision trees (e.g. comparing options against criteria)
- Relationship / flow diagrams between entities

Mermaid nodes can become internal links by attaching the `internal-link` class. Caveat: such links don't show up in the graph view.

## Math — MathJax / LaTeX

Inline: `$e^{2i\pi} = 1$`. Block:

```
$$
\text{Future value} = P (1+r)^n
$$
```

Useful for derivations on `concepts/` pages where the formula matters.

## HTML — sparingly

Obsidian sanitizes HTML and supports a limited subset. Two gotchas:
1. **No Markdown inside HTML.** `<div>**bold**</div>` will render the asterisks literally.
2. **No blank lines inside an HTML block.** A blank line breaks the block.

Prefer pure Markdown wherever possible. Reach for HTML only for things Markdown can't do (e.g. specific column widths, `<details>` if a callout doesn't fit).

## Other niceties

- **Image sizing:** `![[image.png|400]]` (width only) or `![[image.png|400x300]]` (W×H). External: `![alt|400](https://...)`.
- **Line breaks vs paragraphs:** a blank line creates a paragraph; two trailing spaces or `Shift+Enter` create a `<br>` within a paragraph.
- **Escape formatting:** prepend `\` — `\*\*not bold\*\*`.

## See also

- [[../CLAUDE.md]] — wiki conventions distilled here for session bootstrapping
- [[llm-wiki.md]] — the underlying ingest/query/lint pattern
