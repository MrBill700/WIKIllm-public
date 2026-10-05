#!/usr/bin/env node
// Regression test for .claude/workflows/claim-audit.js's queueCommand() (regression R52):
// args.seed / args.include must reach audit_claims.py's command line, and with
// NEITHER given the command must stay byte-identical to the pre-ledger (pre-R52) form -- that
// byte-identity is the zero-regression guarantee for every existing caller.
//
// The workflow file cannot be imported (phase()/agent() are runtime globals), so the
// function is cut out between its begin/end markers and evaluated -- the same pattern
// as test_anchor_backfill_parse.mjs.
// Run: node scripts/tests/test_claim_audit_queue_cmd.mjs   (exit 0 = pass)
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const wf = join(here, '..', '..', '.claude', 'workflows', 'claim-audit.js')
const src = readFileSync(wf, 'utf8')

const fails = []
const check = (cond, msg) => { if (!cond) fails.push(msg) }
const throws = (fn, msg) => {
  try { const r = fn(); fails.push(`${msg} -- expected a throw, got ${JSON.stringify(r)}`) }
  catch (e) { if (!(e instanceof Error)) fails.push(`${msg} -- threw a non-Error: ${String(e)}`) }
}

// 1. whole-file syntax: strip the export and parse as an ASYNC body (the workflow
// runtime runs scripts in an async context, so top-level await is legal)
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
try { new AsyncFunction(src.replace(/^export /m, '')) } catch (e) { fails.push(`workflow does not parse: ${e.message}`) }

// 2. extract queueCommand
const m = /\/\/ -- queueCommand begin([\s\S]*?)\/\/ -- queueCommand end/.exec(src)
check(!!m, 'queueCommand markers missing')
const queueCommand = m ? new Function(m[1] + '\nreturn queueCommand')() : null

const BASE = 'python scripts/audit_claims.py --n 10'

if (queueCommand) {
  // 3. THE INVARIANT: no seed, no includes == today's command, byte for byte.
  check(queueCommand(10, '', []) === BASE, `no-args: ${JSON.stringify(queueCommand(10, '', []))}`)
  check(queueCommand(3, '', []) === 'python scripts/audit_claims.py --n 3', `no-args n=3: ${JSON.stringify(queueCommand(3, '', []))}`)
  // callers that pass nothing at all still get the bare command (INCLUDE defaults to [])
  check(queueCommand(10, '', undefined) === BASE, 'undefined include list must behave as empty')

  // 4. seed only
  check(queueCommand(10, '2026-Q4', []) === `${BASE} --seed 2026-Q4`, `seed: ${JSON.stringify(queueCommand(10, '2026-Q4', []))}`)

  // 5. seed + two includes -- one --include per spec (audit_claims.py uses action="append"),
  // each double-quoted because vault-relative paths can contain spaces.
  const both = queueCommand(10, '2026-Q4', ['wiki/analysis/positions-register.md:42', 'wiki/concepts/base building.md:7'])
  check(both === `${BASE} --seed 2026-Q4 --include "wiki/analysis/positions-register.md:42" --include "wiki/concepts/base building.md:7"`, `seed+include: ${JSON.stringify(both)}`)

  // 6. includes without a seed
  check(queueCommand(5, '', ['wiki/entities/a.md:3']) === 'python scripts/audit_claims.py --n 5 --include "wiki/entities/a.md:3"',
    `include only: ${JSON.stringify(queueCommand(5, '', ['wiki/entities/a.md:3']))}`)

  // 7. backslashes are normalized workflow-side, so none ever reaches a double-quoted
  // Bash argument (audit_claims.py normalizes them again on its side)
  const winPath = queueCommand(10, '', ['wiki\\analysis\\positions-register.md:42'])
  check(winPath === `${BASE} --include "wiki/analysis/positions-register.md:42"`, `windows path: ${JSON.stringify(winPath)}`)
  check(!/\\/.test(winPath), 'a backslash survived into the composed command')

  // 8. injection / malformed input THROWS -- a silently dropped force-include would make
  // the decision-calendar's "Q4: --include the anchor-resistant claims" a quiet no-op,
  // and a silently dropped seed would re-serve the already-judged same-quarter queue.
  throws(() => queueCommand(10, '2026-Q4; rm -rf wiki', []), 'seed with a command separator')
  throws(() => queueCommand(10, '$(whoami)', []), 'seed with a command substitution')
  throws(() => queueCommand(10, '2026 Q4', []), 'seed with a space')
  throws(() => queueCommand(10, '--n 999', []), 'seed that is really another flag')
  throws(() => queueCommand(10, '', ['wiki/a.md:1" ; rm -rf wiki ; echo "']), 'include breaking out of the quotes')
  throws(() => queueCommand(10, '', ['wiki/a.md:1;id']), 'include with a command separator')
  throws(() => queueCommand(10, '', ['wiki/`id`.md:1']), 'include with a backtick')
  throws(() => queueCommand(10, '', ['wiki/$HOME.md:1']), 'include with a shell variable')
  // malformed specs: audit_claims.py rpartition(":") turns a colon-less spec into an
  // empty path, and endswith("") matches the FIRST population entry -- i.e. a bare
  // filename force-includes an arbitrary claim. Reject before it gets there.
  throws(() => queueCommand(10, '', ['positions-register.md']), 'include with no :LINE')
  throws(() => queueCommand(10, '', ['positions-register.md:']), 'include with an empty line number')
  throws(() => queueCommand(10, '', ['positions-register.md:abc']), 'include with a non-numeric line')
  throws(() => queueCommand(10, '', [':42']), 'include with an empty path')
}

// 8b. the args -> INCLUDE derivation, which queueCommand's validation cannot cover:
// a malformed args.include that is normalized away to [] never reaches queueCommand, and
// the run then serves the DEFAULT queue with no error and no log line -- the quiet no-op
// the throw exists to prevent. One accepted shape: an array of non-empty strings.
const mi = /\/\/ -- includeList begin([\s\S]*?)\/\/ -- includeList end/.exec(src)
check(!!mi, 'includeList markers missing')
const includeList = mi ? new Function(mi[1] + '\nreturn includeList')() : null
if (includeList) {
  check(JSON.stringify(includeList(undefined)) === '[]', 'no args at all must derive []')
  check(JSON.stringify(includeList({ n: 10 })) === '[]', 'absent args.include must derive []')
  check(JSON.stringify(includeList({ include: null })) === '[]', 'args.include: null must derive []')
  check(JSON.stringify(includeList({ include: [] })) === '[]', 'an empty array must derive []')
  check(JSON.stringify(includeList({ include: ['wiki/a.md:1', '  wiki/b.md:2  '] })) === '["wiki/a.md:1","wiki/b.md:2"]',
    `array entries must be kept and trimmed: ${JSON.stringify(includeList({ include: ['wiki/a.md:1', '  wiki/b.md:2  '] }))}`)
  throws(() => includeList({ include: 'wiki/a.md:1' }), 'a bare-string args.include (the likeliest hand-written slip)')
  throws(() => includeList({ include: { file: 'wiki/a.md', line: 1 } }), 'an object args.include')
  throws(() => includeList({ include: 42 }), 'a numeric args.include')
  throws(() => includeList({ include: ['   '] }), 'a whitespace-only entry')
  throws(() => includeList({ include: ['wiki/a.md:1', null] }), 'a null entry beside a good one')
  throws(() => includeList({ include: [{ file: 'wiki/a.md:1' }] }), 'an object entry')
}
check(/const INCLUDE = includeList\(args\)/.test(src), 'INCLUDE is not derived through includeList')
// the exact old expression: a ternary that turned any non-array args.include into []
check(!/Array\.isArray\(args\.include\)\s*\)\s*\?/.test(src), 'the silent Array.isArray-else-[] ternary is still in place')

// 9. the composed command is actually what the Queue agent is told to run, and the
// surrounding prompt is unchanged where it must be
check(/Run: \$\{QUEUE_CMD\}  \(never pipe it through head\)\./.test(src), 'Queue prompt does not interpolate QUEUE_CMD before the two-space "(never pipe it through head)." literal')
check(!/python scripts\/audit_claims\.py --n \$\{N\}/.test(src), 'the hardcoded "--n ${N}" command is still in the prompt')
check(/const QUEUE_CMD = queueCommand\(N, SEED, INCLUDE, INCLUDE_JUDGED\)/.test(src), 'QUEUE_CMD is not built from N/SEED/INCLUDE/INCLUDE_JUDGED')
// the extra guidance is CONDITIONAL, so a no-seed/no-include run sends the identical prompt
check(/const FLAG_NOTE = \(SEED \|\| INCLUDE\.length \|\| INCLUDE_JUDGED\)/.test(src), 'FLAG_NOTE is not conditional on SEED/INCLUDE/INCLUDE_JUDGED')
check(/const INCLUDE_NOTE = INCLUDE\.length/.test(src), 'INCLUDE_NOTE is not conditional on INCLUDE')
check(/never truncate the array to \$\{N\}/.test(src), 'the include prompt does not stop the agent truncating its array to N')
// the label records which seed was served, so the run log says what was audited
check(/label: `queue:n=\$\{N\}\$\{SEED \? ` seed=\$\{SEED\}` : ''\}/.test(src), 'queue label does not report the seed')
// REPLAY PATH UNTOUCHED: a benchmark label like "2026-Q3 rerun" must keep working, so
// queueCommand's strict validation must not run on the args.claims branch.
check(/queue = \{ seed: String\(args\.seed \|\| 'replay'\)/.test(src), 'replay branch no longer takes its seed straight from args.seed')
check(/^\s*const QUEUE_CMD/m.test(src) && src.indexOf('const QUEUE_CMD') > src.indexOf("String(args.seed || 'replay')"), 'QUEUE_CMD is built outside the else branch -- a bad seed would throw on replay too')
// documented for callers
check(/args\.seed/.test(src.slice(0, src.indexOf('phases:'))), 'meta.whenToUse does not document args.seed')
check(/args\.include/.test(src.slice(0, src.indexOf('phases:'))), 'meta.whenToUse does not document args.include')
// 10. the docs must not resurrect the false mechanism: audit_claims.py shuffles the
// population UNDER THE SEED before a stable sort, so the risk ranking is seed-dependent
// wherever claims are tied (measured: a 10-claim all-tied fixture returned three distinct
// claims across five seeds at --n 1). "seed-independent risk ranking" is a wrong claim
// that syncs to every vault.
const readme = readFileSync(join(here, '..', '..', '.claude', 'workflows', 'README.md'), 'utf8')
for (const [name, text] of [['claim-audit.js', src], ['workflows/README.md', readme]]) {
  check(!/seed-independent/i.test(text), `${name} still calls the risk ranking seed-independent`)
  check(!/top-risk entries do NOT move/i.test(text), `${name} still claims the top-risk entries cannot move under a new seed`)
}

// 11. regression R52 judged-claims ledger: the workflow is the quarterly deep audit, so it passes
// --include-judged BY DEFAULT; args.include_judged: false opts into the skip; a non-boolean throws.
if (queueCommand) {
  check(queueCommand(10, '', [], true) === `${BASE} --include-judged`, `include-judged: ${JSON.stringify(queueCommand(10, '', [], true))}`)
  check(queueCommand(10, '', [], false) === BASE, 'includeJudged=false must leave the command byte-identical')
  check(queueCommand(10, '2026-Q4', ['wiki/a.md:1'], true) === `${BASE} --seed 2026-Q4 --include "wiki/a.md:1" --include-judged`, 'include-judged is not appended after seed/includes')
}
const mj = /function includeJudged\(a\) \{[\s\S]*?\n\}/.exec(src)
check(!!mj, 'includeJudged() missing')
const includeJudged = mj ? new Function(mj[0] + '\nreturn includeJudged')() : null
if (includeJudged) {
  check(includeJudged(undefined) === true && includeJudged({ n: 10 }) === true, 'the default must be include-judged (the quarterly deep audit)')
  check(includeJudged({ include_judged: false }) === false, 'include_judged: false must opt into the skip')
  throws(() => includeJudged({ include_judged: 'false' }), 'a string include_judged')
}
check(/const INCLUDE_JUDGED = includeJudged\(args\)/.test(src), 'INCLUDE_JUDGED is not derived through includeJudged')
check(/key \(the 16-hex id= token/.test(src), 'the Queue prompt does not ask for the id= key')

if (fails.length) { console.log('FAIL\n  ' + fails.join('\n  ')); process.exit(1) }
console.log('PASS: workflow parses; queueCommand(n, "", []) is byte-identical to the pre-ledger (pre-R52) command; seed and each include reach the command line quoted and backslash-normalized; unsafe seeds/specs and malformed FILE:LINE throw; a present-but-not-an-array args.include throws instead of being dropped; prompt interpolates QUEUE_CMD with conditional flag/include notes; label reports the seed; replay branch untouched; --include-judged is the default and include_judged:false opts into the ledger skip (regression R52); neither doc calls the risk ranking seed-independent')
