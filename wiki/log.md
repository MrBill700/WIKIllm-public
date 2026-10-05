---
title: Log
description: Append-only chronological record of ingests, queries, and lint passes
---

# Log

Append-only. Each entry: `## [YYYY-MM-DD] <op> | <subject>` where `<op>` is `ingest`, `query`, `lint`, or `meta`.

Quick recent entries: `grep "^## \[" log.md | tail -10`

---

<!-- newest entries on top, directly below this line -->
