#!/usr/bin/env node
// Regression test for regression R70 -- the claim-audit escalation telemetry is PERSISTED:
//   T1  the workflow parses; its telemetry helpers are cut out between markers and evaluated
//       (phase()/agent() are runtime globals, so the workflow cannot be imported -- the same
//       pattern as test_claim_audit_queue_cmd.mjs)
//   T2  every escalation reason the pipeline pushes maps to a real "<source>.<kind>" key, and
//       the number of push sites is pinned, so a new reason shape cannot land keyless ("other")
//   T3  escalation_reasons counts escalated CLAIMS per key (a key repeated in one row counts once)
//   T4  log_line renders the canonical parenthesis exactly, including E=0 -> "reasons: none"
//   T5  ONE canonical log shape: every "claim audit (" in the files that state it is the
//       identical string audit_claims.py next_steps() prints, and the old shape is gone
//   T6  audit_claims.py --record-run appends the workflow's run_record (or whole result) to
//       _meta/claim-audit-ledger.json's runs under --root, with run ids YYYY-MM-DD.k
//   T7  bad input exits 2 and leaves the ledger byte-identical; an unreadable ledger is
//       refused, never overwritten (RED control: the corrupt file survives)
// Run: node scripts/tests/test_claim_audit_telemetry.mjs   (exit 0 = pass)
import { readFileSync, writeFileSync, mkdtempSync, mkdirSync, rmSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { tmpdir } from 'node:os'
import { spawnSync } from 'node:child_process'

const here = dirname(fileURLToPath(import.meta.url))
const repo = join(here, '..', '..')
const wfPath = join(repo, '.claude', 'workflows', 'claim-audit.js')
const src = readFileSync(wfPath, 'utf8')
const audit = join(repo, 'scripts', 'audit_claims.py')

const fails = []
const pinned = []
const check = (cond, msg) => { if (!cond) fails.push(msg) }

// T1
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
try { new AsyncFunction(src.replace(/^export /m, '')) } catch (e) { fails.push(`workflow does not parse: ${e.message}`) }
const m = /\/\/ -- telemetry begin([\s\S]*?)\/\/ -- telemetry end/.exec(src)
check(!!m, 'telemetry markers missing')
const T = m ? new Function(m[1] + '\nreturn { escalationKey, escalationReasons, logLine, runRecord }')() : null

if (T) {
  // T2
  const lenses = ['wording', 'scope', 'numbers']
  const want = {}
  for (const l of lenses) {
    want[`${l}: no result`] = `${l}.no_result`
    for (const v of ['DRIFTED', 'UNSUPPORTED', 'STALE']) want[`${l}: ${v}`] = `${l}.verdict`
    want[`${l}: flag (could not open the PDF)`] = `${l}.flag`
    want[`${l}: flag (no reason given)`] = `${l}.flag`
    want[`${l}: primary absent`] = `${l}.absent`
  }
  want['numbers: figure read off a chart'] = 'numbers.chart'
  want['script: no raw primary'] = 'script.no_raw'
  want['script: image primary'] = 'script.image'
  for (const [s, k] of Object.entries(want)) check(T.escalationKey(s) === k, `escalationKey(${JSON.stringify(s)}) = ${T.escalationKey(s)}, want ${k}`)
  check(T.escalationKey('something new') === 'other', 'unknown reason must map to "other", not throw')
  const pushes = (src.match(/escalation\.push\(/g) || []).length
  check(pushes === 7, `the pipeline has ${pushes} escalation.push sites, pinned at 7 -- a new reason shape needs a key in escalationKey() and this count updated`)
  // The strings the pipeline ACTUALLY pushes, cut from the source: each push argument is a
  // quoted or template literal; ${name} expands to every lens, ${l.verdict} to every
  // non-FAITHFUL verdict, ${l.arbiter_reason || ...} to a sample reason. A renamed literal
  // in the pipeline then maps to "other" here and fails -- the hardcoded table above cannot.
  const lits = [...src.matchAll(/escalation\.push\((['`])(.*?)\1\)/g)].map(x => x[2])
  check(lits.length === pushes, `could not cut every push literal from the source (${lits.length} of ${pushes})`)
  let expanded = 0
  for (const lit of lits) {
    const lensVals = /\$\{name\}/.test(lit) ? lenses : ['']
    const verdictVals = /\$\{l\.verdict\}/.test(lit) ? ['DRIFTED', 'UNSUPPORTED', 'STALE'] : ['']
    for (const ln of lensVals) for (const v of verdictVals) {
      const s = lit.replace(/\$\{name\}/g, ln).replace(/\$\{l\.verdict\}/g, v).replace(/\$\{l\.arbiter_reason[^}]*\}/g, 'sample reason')
      check(!/\$\{/.test(s), `push literal has an interpolation the test cannot expand: ${lit}`)
      check(T.escalationKey(s) !== 'other', `the pipeline pushes ${JSON.stringify(s)}, which escalationKey maps to "other"`)
      expanded++
    }
  }
  pinned.push(`T2 ${Object.keys(want).length} table strings + ${expanded} strings expanded from the ${pushes} real push sites -> keys, none "other"`)

  // T3
  const rows = [
    { escalation: ['wording: flag (a)', 'wording: flag (b)', 'script: no raw primary'] },
    { escalation: ['scope: DRIFTED', 'wording: flag (c)'] },
    { escalation: [] },
    { escalation: ['numbers: figure read off a chart'] },
  ]
  const er = T.escalationReasons(rows)
  check(JSON.stringify(er) === JSON.stringify({ 'wording.flag': 2, 'numbers.chart': 1, 'scope.verdict': 1, 'script.no_raw': 1 }),
    `escalationReasons: ${JSON.stringify(er)}`)
  pinned.push(`T3 per-claim dedupe + count-desc/key order: ${JSON.stringify(er)}`)

  // T4
  const tally = { seed: '2026-Q3', n: 10, hits: 1, split: 2, unconfirmed: 0, escalated: 3, escalation_reasons: er }
  const ll = T.logLine(tally)
  check(ll === '(2026-Q3, 10, 1; split 2, unconfirmed 0; escalated 3/10 (reasons: wording.flag=2, numbers.chart=1, scope.verdict=1, script.no_raw=1))', `logLine: ${ll}`)
  const ll0 = T.logLine({ ...tally, escalated: 0, escalation_reasons: {} })
  check(ll0 === '(2026-Q3, 10, 1; split 2, unconfirmed 0; escalated 0/10 (reasons: none))', `logLine E=0: ${ll0}`)
  pinned.push(`T4 ${ll}`)

  // T5 -- one canonical shape
  const py = readFileSync(audit, 'utf8')
  const sm = /lint \| (claim audit \([^"]*\))"\)/.exec(py)
  check(!!sm, 'could not find the canonical shape in audit_claims.py next_steps()')
  const SHAPE = sm ? sm[1] : ''
  check(SHAPE === 'claim audit (seed, n, hits; split S, unconfirmed U; escalated E/N (reasons: k1=n1, k2=n2))', `canonical shape: ${SHAPE}`)
  const files = ['.claude/workflows/claim-audit.js', '.claude/workflows/README.md', '.claude/skills/ingest/SKILL.md', 'scripts/audit_claims.py']
  let occ = 0
  for (const f of files) {
    const t = readFileSync(join(repo, f), 'utf8')
    const re = /claim audit \(/g
    let x
    while ((x = re.exec(t))) {
      occ++
      check(t.startsWith(SHAPE, x.index), `${f}: a "claim audit (" that is not the canonical shape: ${JSON.stringify(t.slice(x.index, x.index + 110))}`)
    }
    check(!/unconfirmed U\)/.test(t), `${f}: still carries the pre-R70 shape ending "unconfirmed U)"`)
  }
  check(occ >= 5, `expected the shape stated at least 5 times across ${files.length} files, found ${occ}`)
  // the rendered line is an instance of the shape: same punctuation skeleton
  check(/^\([^,]+, \d+, \d+; split \d+, unconfirmed \d+; escalated \d+\/\d+ \(reasons: (none|[a-z_.]+=\d+(, [a-z_.]+=\d+)*)\)\)$/.test(ll), 'log_line does not match the canonical skeleton')
  pinned.push(`T5 ${occ} occurrences across ${files.length} files all == canonical shape`)

  // T6 / T7 -- the recorder
  const tmp = mkdtempSync(join(tmpdir(), 'ca-telemetry-'))
  try {
    mkdirSync(join(tmp, 'wiki'), { recursive: true })
    const ledger = join(tmp, '_meta', 'claim-audit-ledger.json')
    const rr = T.runRecord({ ...tally, queued: 10, printed_n: 10, cleared: 3, skipped_faithful: 4, borderline: 1, material: 0, chart_read: 1, lens_disagreement: 2 },
      [{ file: 'wiki/a.md', line: 3, slug: 's', refute_status: 'arbiter', final_verdict: 'DRIFTED', escalation: ['scope: DRIFTED'], lens_evidence: { big: 'x' } }],
      [{ file: 'wiki/b.md', line: 9, slug: 's', agree: true, final_verdict: 'FAITHFUL' }])
    check(!('lens_evidence' in rr.rows[0]), 'run_record rows must not carry lens evidence (ledger bloat)')
    const whole = join(tmp, 'result.json')
    writeFileSync(whole, JSON.stringify({ seed: '2026-Q3', rows: [], run_record: rr }))
    const run = (file) => spawnSync('python', [audit, '--root', tmp, '--record-run', file], { encoding: 'utf8' })
    const r1 = run(whole)
    check(r1.status === 0, `record whole result: exit ${r1.status} ${r1.stderr}`)
    const only = join(tmp, 'rr.json')
    writeFileSync(only, JSON.stringify(rr))
    const r2 = run(only)
    check(r2.status === 0, `record bare run_record: exit ${r2.status} ${r2.stderr}`)
    // r2 is the SAME record: a retry must not double the telemetry
    check(/already recorded as run/.test(r2.stdout), `identical re-record was not a no-op: ${r2.stdout}`)
    const L = JSON.parse(readFileSync(ledger, 'utf8'))
    check(Array.isArray(L.runs) && L.runs.length === 1, `ledger runs after a duplicate: ${JSON.stringify(L.runs && L.runs.length)}`)
    const iso = String(L.runs[0].run).replace(/\.1$/, '')   // Python's date, not JS's (no midnight race)
    check(/^\d{4}-\d\d-\d\d$/.test(iso) && L.runs[0].recorded === iso, `run id / recorded: ${L.runs[0].run} ${L.runs[0].recorded}`)
    check(L.runs[0].rows[0].idx === undefined || Number.isInteger(L.runs[0].rows[0].idx), 'row idx malformed')
    // a record carrying its own run/recorded cannot hijack the ledger-owned id; a deleted
    // earlier run cannot cause a duplicate id (max suffix + 1, not a count)
    const withId = join(tmp, 'withid.json')
    writeFileSync(withId, JSON.stringify({ ...rr, seed: '2026-Q4', run: 'HIJACK', recorded: '1999-01-01' }))
    check(run(withId).status === 0, 'record with its own run id failed')
    const third = join(tmp, 'third.json')
    writeFileSync(third, JSON.stringify({ ...rr, seed: '2027-Q1' }))
    const L2 = JSON.parse(readFileSync(ledger, 'utf8'))
    L2.runs.shift()   // hand-delete run .1
    writeFileSync(ledger, JSON.stringify(L2))
    check(run(third).status === 0, 'third record failed')
    const ids = JSON.parse(readFileSync(ledger, 'utf8')).runs.map(r => r.run)
    check(JSON.stringify(ids) === JSON.stringify([`${iso}.2`, `${iso}.3`]), `run ids after a hand-delete / hijack attempt: ${ids}`)
    check(!ids.includes('HIJACK'), 'input run field overwrote the ledger id')
    // a BOM-prefixed ledger (Windows editor) is still readable
    writeFileSync(ledger, '\ufeff' + readFileSync(ledger, 'utf8'))
    const bomRec = join(tmp, 'bom.json')
    writeFileSync(bomRec, JSON.stringify({ ...rr, seed: '2027-Q2' }))
    const rb = run(bomRec)
    check(rb.status === 0, `BOM ledger refused: ${rb.stderr}`)
    check(JSON.parse(readFileSync(ledger, 'utf8').replace(/^\ufeff/, '')).runs.length === 3, 'BOM ledger lost runs')
    check(L.runs[0].escalated === 3 && L.runs[0].escalation_reasons['wording.flag'] === 2, 'escalation counts not persisted')
    check(JSON.stringify(L.runs[0].rows[0].escalation) === '["scope: DRIFTED"]', 'per-row reasons not persisted verbatim')
    check(/escalated 3\/10; reasons: wording\.flag=2/.test(r1.stdout), `recorder summary line: ${r1.stdout.trim()}`)
    pinned.push(`T6 ${r1.stdout.trim().split(/\r?\n/).pop().replace(/ -> .*?\(/, ' (')}`)

    // T7 bad input: exit 2, ledger untouched
    const before = readFileSync(ledger, 'utf8')
    const bad = join(tmp, 'bad.json')
    writeFileSync(bad, JSON.stringify({ seed: 'x', n: 1 }))
    const r3 = run(bad)
    check(r3.status === 2 && /missing/.test(r3.stderr), `malformed record: exit ${r3.status} ${r3.stderr}`)
    check(readFileSync(ledger, 'utf8') === before, 'malformed record changed the ledger')
    // wrong TYPES are refused before the write (was: saved, then crashed with exit 1)
    for (const [label, patch] of [['reasons as a list', { escalation_reasons: ['a'] }], ['rows as an object', { rows: {} }],
      ['escalated as a string', { escalated: '3' }], ['an older whole result (no kind)', { kind: undefined }]]) {
      const f = join(tmp, 'typed.json')
      writeFileSync(f, JSON.stringify({ ...rr, seed: 'typed', ...patch }))
      const rt = run(f)
      check(rt.status === 2, `${label}: exit ${rt.status} ${rt.stderr}`)
      check(readFileSync(ledger, 'utf8') === before, `${label}: ledger changed`)
    }
    check(!existsSync(ledger + '.tmp') && !existsSync(ledger.replace(/\.json$/, '.json.tmp')), 'a temp file was left in _meta/')
    writeFileSync(ledger, JSON.stringify({ version: 1, runs: [1] }))
    const r5 = run(only)
    check(r5.status === 2 && /refusing to overwrite/.test(r5.stderr), `ledger with a non-object run: exit ${r5.status} ${r5.stderr}`)
    writeFileSync(ledger, '{not json')
    const r4 = run(only)
    check(r4.status === 2 && /refusing to overwrite/.test(r4.stderr), `corrupt ledger: exit ${r4.status} ${r4.stderr}`)
    check(readFileSync(ledger, 'utf8') === '{not json', 'RED control: a corrupt ledger was overwritten')
    check(!existsSync(join(repo, '_meta', 'claim-audit-ledger.json')), 'the recorder wrote into the template repo, not --root')
    pinned.push('T7 malformed input exit 2 + ledger unchanged; corrupt ledger refused and preserved')
  } finally {
    rmSync(tmp, { recursive: true, force: true })
  }
}

if (fails.length) {
  console.log(`FAIL (${fails.length}):`)
  for (const f of fails) console.log(`  - ${f}`)
  process.exit(1)
}
for (const p of pinned) console.log(`  ok ${p}`)
console.log('PASS test_claim_audit_telemetry: escalation telemetry keys, log_line, one canonical shape, --record-run ledger writes')
