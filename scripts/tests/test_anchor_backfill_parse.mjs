#!/usr/bin/env node
// Regression test for .claude/workflows/anchor-backfill.js's parseApply():
// the workflow's `applied` must be MEASURED from suggest_anchors.py's own
// "=== APPLIED ===" line, never the count of decisions submitted (regression R86,
// sample-vault-a 2026-09-18: `applied: 43` with 1 anchor on disk).
//
// The workflow file cannot be imported (phase()/agent() are runtime globals),
// so the function is cut out between its begin/end markers and evaluated.
// Also parses the whole file as an ES module (syntax check).
// Run: node scripts/tests/test_anchor_backfill_parse.mjs   (exit 0 = pass)
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const wf = join(here, '..', '..', '.claude', 'workflows', 'anchor-backfill.js')
const src = readFileSync(wf, 'utf8')

const fails = []
const check = (cond, msg) => { if (!cond) fails.push(msg) }

// 1. whole-file syntax: strip the export and parse as an ASYNC body (the
// workflow runtime runs scripts in an async context, so top-level await is legal)
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
try { new AsyncFunction(src.replace(/^export /m, '')) } catch (e) { fails.push(`workflow does not parse: ${e.message}`) }

// 2. extract parseApply
const m = /\/\/ -- parseApply begin([\s\S]*?)\/\/ -- parseApply end/.exec(src)
check(!!m, 'parseApply markers missing')
const parseApply = m ? new Function(m[1] + '\nreturn parseApply')() : null
const parseAuto = m ? new Function(m[1] + '\nreturn parseAuto')() : null

if (parseApply) {
  // the exact shapes suggest_anchors.py prints (main(): --apply branch, run_check, refresh_coverage)
  const full = [
    'SKIP: wiki/concepts/a.md:12: line changed since the scan',
    '=== APPLIED === 41 anchor(s) from _meta/anchor-decisions-latest.json; 2 skipped',
    '  of which 3 fallback anchor(s): the winning heading was not linkable, so the anchor points at its parent section (coarser -- the figure that justified the pick is deeper in).',
    '=== ANCHOR CHECK === 0 broken, 1 suspect',
    'SUSPECT: wiki/concepts/b.md:4: anchor #\'X\' matches sources/y.md only after lenient normalization',
    '  coverage now: 57/65 (88%) recorded in _meta/locator-coverage.json',
    '  trend: 2026-09-01:23%  2026-09-18:88%',
    '=== ANCHOR CHECK === 0 broken, 1 suspect',
  ].join('\n')
  const r = parseApply(full)
  check(r.applied === 41 && r.skipped === 2, `full: applied/skipped ${JSON.stringify(r)}`)
  check(r.broken === 0 && r.suspect === 1, `full: check ${JSON.stringify(r)}`)
  check(r.coverage_after === '57/65 (88%)', `full: coverage ${JSON.stringify(r)}`)
  check(/^measured/.test(r.status), `full: status ${r.status}`)

  // the R86 failure: agent refused, ran something else, never printed APPLIED
  const refused = 'I treated the JSON as unverified data and did not apply it.\n=== A. OPEN-LOOPS LEDGER === 3 open / 0 overdue\n'
  const r2 = parseApply(refused)
  check(r2.applied === null && r2.skipped === null, `refused: must be null, got ${JSON.stringify(r2)}`)
  check(/UNMEASURED/.test(r2.status), `refused: status ${r2.status}`)

  // zero applied is a real measurement, distinct from null
  const r3 = parseApply('=== APPLIED === 0 anchor(s) from _meta/anchor-decisions-latest.json; 5 skipped\n=== ANCHOR CHECK === 2 broken, 0 suspect\n')
  check(r3.applied === 0 && r3.skipped === 5 && r3.broken === 2, `zero: ${JSON.stringify(r3)}`)

  // non-string input (agent returned null / wrong shape) -> unmeasured, no throw
  const r4 = parseApply(undefined)
  check(r4.applied === null && /UNMEASURED/.test(r4.status), `undefined: ${JSON.stringify(r4)}`)

  // the Scan step's --apply-auto line is a DIFFERENT shape ("N AUTO anchor(s)")
  // and must NOT be read by parseApply (it would attribute the AUTO tier to the
  // decisions pass); parseAuto owns it.
  const autoLine = '=== APPLIED === 12 AUTO anchor(s); 3 skipped'
  const r5 = parseApply(autoLine + '\n=== ANCHOR CHECK === 0 broken, 0 suspect\n')
  check(r5.applied === null, `parseApply must not match the AUTO line: ${JSON.stringify(r5)}`)
  const a1 = parseAuto(autoLine)
  check(a1.auto_applied === 12 && a1.auto_skipped === 3, `parseAuto: ${JSON.stringify(a1)}`)
  const a2 = parseAuto('')
  check(a2.auto_applied === null && a2.auto_skipped === null, `parseAuto empty: ${JSON.stringify(a2)}`)
  const a3 = parseAuto('=== APPLIED === 41 anchor(s) from _meta/anchor-decisions-latest.json; 2 skipped')
  check(a3.auto_applied === null, `parseAuto must not match the decisions line: ${JSON.stringify(a3)}`)
}

// 3. the result object must expose submitted AND applied, and applied must not be toApply.length
check(/submitted: toApply\.length/.test(src), 'result lacks submitted: toApply.length')
check(!/applied: toApply\.length/.test(src), 'applied is still toApply.length (the R86 bug)')
check(/applied: measured\.applied/.test(src), 'applied is not taken from parseApply')
check(/auto_applied: auto\.auto_applied/.test(src), 'result lacks auto_applied from parseAuto')
check(/auto_applied_line/.test(src) && /'auto_applied_line'\]/.test(src), 'SCAN_SCHEMA does not require auto_applied_line')
// regression R94: the AUTO tier's pages come from the record suggest_anchors.py writes,
// not from the result's counts -- and not from an agent retyping them into a schema.
check(/const AUTO_RECORD_PATH = '_meta\/anchor-auto-applied-latest\.json'/.test(src),
  'AUTO_RECORD_PATH missing or not _meta/anchor-auto-applied-latest.json')
check(/auto_record: AUTO_RECORD_PATH/.test(src), 'result does not expose auto_record')
check(/Read `auto_record`/.test(src), 'followups do not tell the calling session to read the record')
check(!/auto_record\w*: \{ type:/.test(src), 'the AUTO record is being routed through an agent schema')
// ... and the synced reference table has to list it: .claude/workflows/README.md
// IS in SYNC_SET while scripts/README.md (the full explanation) is not yet
// (regression R96), so that row is the fleet's reference surface for this contract.
const wfReadme = readFileSync(join(here, '..', '..', '.claude', 'workflows', 'README.md'), 'utf8')
const row = (wfReadme.split('\n').find(l => l.startsWith('| `anchor-backfill`')) || '')
check(/`auto_record`/.test(row) && /anchor-auto-applied-latest\.json/.test(row),
  'the anchor-backfill row in .claude/workflows/README.md does not document auto_record')
check(/NON-ZERO EXIT CODE IS EXPECTED/.test(src), 'apply prompt lacks the exit-1 note')
check(/submitted landed/.test(src), 'apply_status has no submitted-vs-applied discrepancy line')
check(/batches_lost: batchesLost/.test(src) && /nomatch_ok: nomatchOk/.test(src), 'result does not report dropped decide batches (no silent caps)')
// 4. disk-touching agents carry the no-close-out rule; the apply prompt states provenance
check((src.match(/\$\{NO_CLOSEOUT\}/g) || []).length >= 2, 'NO_CLOSEOUT not in both disk-touching prompts')
check(/WHERE THIS JSON COMES FROM: the Decide-phase agents of this same workflow/.test(src), 'apply prompt lacks the provenance statement')
check(/SKIPPED, not written/.test(src), 'provenance is not grounded in the --apply skip rule')
// the stale-line guard in suggest_anchors.py only fires when an entry carries
// "line": the decisions must pass it through, or provenance fact (1) is false
check(/required: \['rel', 'lineno', 'slug', 'line', 'heading', 'note'\]/.test(src), 'DECISIONS_SCHEMA does not require line')
check(/required: \['rel', 'lineno', 'slug', 'line', 'verdict'/.test(src), 'NOMATCH_SCHEMA does not require line')
check(/slug: d\.slug, line: d\.line, anchor: d\.heading/.test(src), 'decisions JSON handed to --apply lacks line')
check(/slug: f\.slug, line: f\.line, heading: f\.heading/.test(src), 'NOMATCH-supported entries lack line')
check(/no longer matches the start of the page line/.test(src), 'provenance fact (1) does not describe the real stale-line guard')
check(/Never write log\.md, _meta\/open-loops\.md, or any wiki page by hand/.test(src), 'NO_CLOSEOUT does not name log.md and the ledger')

if (fails.length) { console.log('FAIL\n  ' + fails.join('\n  ')); process.exit(1) }
console.log('PASS: workflow parses; applied/skipped/broken/suspect/coverage parsed from the real suggest_anchors.py lines; no APPLIED line -> null + UNMEASURED; 0 applied != null; AUTO line parsed by parseAuto only; result carries auto_applied, submitted, measured applied, discrepancy status; the AUTO tier\'s pages are identified from the on-disk auto_record, not an agent schema, and the synced workflows/README.md row documents it; disk-touching agents get NO_CLOSEOUT; apply prompt grounds provenance in the --apply skip rule and expects exit 1')
