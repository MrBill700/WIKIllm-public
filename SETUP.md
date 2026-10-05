# Setup and supported entry points

## Start a wiki

Use this template or copy its files into a topic folder. Fill the placeholders in CLAUDE.md, add sources under raw/, then run:

```
python scripts/check_raw.py --init
python scripts/lint.py
python scripts/check_stale.py
python scripts/maintenance_preflight.py
```

Expected findings on a fresh copy, before the wiki has content:

- `maintenance_preflight.py` reports `READY : NO` with an "unusable raw/ baseline" blocker while raw/ is empty. A baseline of zero files is treated as no baseline (check_raw.py would rewrite it), so re-running `--init` on an empty raw/ does not clear it. Add at least one source to raw/, run `python scripts/check_raw.py --init` again, and the blocker goes away. The preflight is only needed for unattended maintenance passes; the other scanners work without it. Note that a source added after an `--init` on an empty raw/ is absorbed into the new baseline rather than reported as ADDED, so ingest that first source by hand (or add it before running `--init`).
- `maintenance_preflight.py` warns "template drift unknown" until `WIKILLM_TEMPLATE` names your template checkout (see "Configure a trusted sync source" below). In the template itself this is expected.
- Missing question routing and an uninstalled claim audit are advisory. Configure them when the wiki has content.
- `lint.py` scans every Markdown file under the folder, including the test fixture pages under scripts/tests/ (one ALIAS-ONLY link and a set of fixture orphans). These are test data; delete scripts/tests/ from a topic wiki copy if you do not run the test suite there.
- `check_stale.py` lists wiki/analysis/positions-register.md as a central page with no date until you replace its `updated: <YYYY-MM-DD>` placeholder.

Source documents are ignored by Git by default. Wiki pages themselves are tracked: inspect synthesized content before pushing it to a public remote.

## Requirements

- Python 3.11 or later for the core scanners. Core scans need no pip packages.
- Git for updating a template and using the guarded sync tool.
- Obsidian is optional for browsing the Markdown wiki.
- Claude Code as the agent for the bundled skills (`/ingest`, `/wiki-maintenance`) and the `.claude/` layout. Other agents can follow CLAUDE.md and run the scripts, but the skills are written for Claude Code.
- networkx (`pip install networkx`), needed only for scripts/suggest_links.py.
- The defuddle npm CLI (`npm install -g defuddle`), needed only for the vendored defuddle skill.
- Node.js 20 or later for JavaScript regression tests. The optional workflow files require a separate compatible Workflow host with agent, tool, step and parallel execution bindings; installing Node.js does not supply that host. Without it, run the Python scanners and review their proposed changes manually.
- Model-assisted links require a locally configured Ollama endpoint or an ANTHROPIC_API_KEY environment variable for the Anthropic backend. No credentials are bundled. This feature sends selected wiki text to the configured model provider.
- Capture/OCR are optional. screen_capture.ps1 is Windows-specific. verify_capture.py and ocr_sidecars.py document their Pillow, pytesseract and external Tesseract requirements in their help and scripts/README.md.
- The link fixer's automatic stale-lock breaking is Windows-only. Off Windows it refuses; follow its manual recovery guidance. Cross-platform claims require tests on that platform.

## Configure a trusted sync source

Keep a separate clone of this repository as the upstream template. Your topic wiki is a separate copy; only this clone is the sync source. Create it once (a fork's URL works too):

POSIX shell:

```sh
git clone https://github.com/MrBill700/WIKIllm-public.git ~/.wikillm/template
```

PowerShell:

```powershell
git clone https://github.com/MrBill700/WIKIllm-public.git "$HOME\.wikillm\template"
```

A wiki created with GitHub's "Use this template" has no origin/main relationship to the template, so do not use the wiki itself as the sync source. Update the clone with git fetch and git pull --ff-only before syncing, and keep main clean and equal to origin/main.

The default trusted checkout is ~/.wikillm/template. To use another location, set WIKILLM_TEMPLATE to its absolute path in your shell:

PowerShell:

```powershell
$env:WIKILLM_TEMPLATE = 'C:\path\to\template'
```

POSIX shell:

```sh
export WIKILLM_TEMPLATE="$HOME/path/to/template"
```

From the wiki instance, inspect the dry run:

```
python scripts/sync_from_template.py
```

Only after reviewing the report, run --apply. It overwrites shared tooling, so back-port local improvements first. A differing shared _meta document requires review before --adopt-meta. A --template path different from the trusted path requires --allow-noncanonical; that flag does not bypass clean-main, origin/main or in-progress-operation checks.

The sync tool consults the last fetched origin/main; it does not fetch for you. Selecting the template itself reports that there is nothing to sync. Do not point a topic wiki's public remote at private content without reviewing it.

## Tests

Run the tests from the repository root. POSIX shell:

```sh
for f in scripts/tests/test_*.py; do python -B "$f" || echo "FAIL $f"; done
for f in scripts/tests/test_*.mjs; do node "$f" || echo "FAIL $f"; done
```

PowerShell:

```powershell
Get-ChildItem scripts/tests/test_*.py | % { python -B $_.FullName; if ($LASTEXITCODE) { "FAIL $_" } }
Get-ChildItem scripts/tests/test_*.mjs | % { node $_.FullName; if ($LASTEXITCODE) { "FAIL $_" } }
```

The PowerShell geometry test (scripts/tests/test_screen_geometry.ps1) runs separately; see scripts/README.md. Tests create temporary fixture vaults. GUI geometry uses PowerShell; OCR tests require the optional dependencies. bench_links.py is an optional model benchmark and can consume paid API tokens.

Historical compatibility and migration tests load sanitized, test-only script fixtures from scripts/tests/legacy-fixtures/. No old Git history is required. Missing fixtures fail the tests. Historical revision and R labels identify regression cases, not issues or commits available in this public repository. Optional OCR, real cloud-sync and privileged filesystem cases may report explicit skips when their environment is unavailable.

## Contributing and updates

Use a branch or worktree, review the diff, and run the relevant tests. Publish only reviewed files; never copy an entire private working directory into this repository. Future imports must pass the same personal-data and secret review as the initial snapshot. Personal operations and release-preparation logs belong outside the published tree.
