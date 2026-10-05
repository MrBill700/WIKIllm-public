# Maintaining a wiki with an assistant

This implementation is inspired by [Andrej Karpathy's LLM Wiki idea](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). The instructions below describe this repository; the original essay is not bundled.

Keep source documents in `raw/`. Treat them as evidence and leave their bytes unchanged. Put explanations, source notes, entities and comparisons in `wiki/`. Configure the topic and writing rules in `CLAUDE.md`.

For each new source, inspect its contents, discuss the useful findings, and update the relevant pages with citations. Maintain `wiki/index.md` so later sessions can find those pages. Record completed work in `wiki/log.md`.

When answering a question, read the relevant wiki pages and check important claims against their sources. Save an answer as a page when it will help future work. Mark uncertainty instead of turning an inference into a fact.

Run the supplied scanners to find structural problems and deferred work. Review proposed repairs before applying them. A successful scan cannot establish that a claim is true; substantive changes still need source verification and human judgment.

The owner chooses sources and approves consequential changes. The assistant maintains the files within that authority.
