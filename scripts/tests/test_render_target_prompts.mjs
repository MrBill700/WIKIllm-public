#!/usr/bin/env node
// Regression test for regression R72 (agent renders must name a write target) and
// regression R71 (two ingest-skill rules that were lost once already).
//
// R72: a claim-audit subagent read "READ-ONLY -- edit nothing" as "do not modify
// existing files", then CREATED raw/karvonen-1957/p314.png beside the PDF it was
// reading (2026-09-16, sample-vault-c). The prompts named the tool (PyMuPDF) and the
// input (a path inside raw/) and nothing about output. So the rule is pinned in
// two places per prompt: the READ-ONLY preamble, and the render instruction
// itself -- the line where the write decision is actually made.
//
// R71: the 2026-09-13 heredoc lesson was drafted into ONE vault's copy of the
// ingest skill and guarded by a note in that vault's ledger; a later fleet
// --apply overwrote it and the note read as current for days. Both the lesson
// and the back-port rule that prevents a repeat are pinned here, because "a
// lesson silently disappearing" is exactly this file's failure class.
//
// Assertions are scoped to the prompt-builder spans / the owning bullet, never
// whole-file substring searches: a whole-file check passes if a later rewrite
// moves the clause into a comment or deletes the builder. Scope also matters in
// the other direction -- writes into raw/ by a HUMAN or a CAPTURE SCRIPT (page
// scans, screen_capture.ps1, banked extractions) are correct and documented, so
// the carve-out wording is asserted too; a blanket "nothing ever goes in raw/"
// must fail this test.
//
// Run: node scripts/tests/test_render_target_prompts.mjs   (exit 0 = pass)
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..', '..')
const read = (...p) => readFileSync(join(root, ...p), 'utf8')

const wf = read('.claude', 'workflows', 'claim-audit.js')
const ingest = read('.claude', 'skills', 'ingest', 'SKILL.md')
const fleet = read('_meta', 'fleet-conventions.md')

const fails = []
const check = (cond, msg) => { if (!cond) fails.push(msg) }

// --- 0. whole-file syntax: the workflow runs as an async body (top-level await).
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
try { new AsyncFunction(wf.replace(/^export /m, '')) } catch (e) { fails.push(`claim-audit.js does not parse: ${e.message}`) }

// --- 1. the two prompt-builder spans, cut out so the assertions cannot be
// satisfied by text living anywhere else in the file.
const lensSpan = /const lensPrompt = \(c, lens\) =>\s*`([\s\S]*?)`\n/.exec(wf)
check(!!lensSpan, 'lensPrompt builder not found (renamed or deleted)')
const arbSpan = /const arbiterPrompt = \(c, lenses, mode\) => \{([\s\S]*?)\n\}\n/.exec(wf)
check(!!arbSpan, 'arbiterPrompt builder not found (renamed or deleted)')

// The preamble clause: every prompt that opens READ-ONLY must also forbid WRITES
// and name where a derived artifact goes instead.
const PREAMBLE = /READ-ONLY -- edit nothing AND write nothing into the vault[\s\S]{0,240}?system temp dir, never inside raw\//
if (lensSpan) check(PREAMBLE.test(lensSpan[1]), 'lensPrompt preamble does not forbid writes / name the temp dir + raw/')
if (arbSpan) check(PREAMBLE.test(arbSpan[1]), 'arbiterPrompt preamble does not forbid writes / name the temp dir + raw/')

// The instruction-site clause: the render order itself carries the target. This
// is the load-bearing one -- the preamble binds weakly, as R72 demonstrated.
if (lensSpan) check(/render with PyMuPDF to a temp dir \(never inside raw\/\)/.test(lensSpan[1]),
  'lensPrompt Method: the "render with PyMuPDF" order names no write target')
if (arbSpan) check(/figures: render to a temp dir \(never inside raw\/\) and view/.test(arbSpan[1]),
  'arbiterPrompt: the "figures: render" order names no write target')

// Both prompts still have to ORDER the render -- a fix that deletes the
// instruction would pass the clauses above vacuously.
if (lensSpan) check(/figures and slides: render/.test(lensSpan[1]), 'lensPrompt no longer orders a figure render')
if (arbSpan) check(/figures: render/.test(arbSpan[1]), 'arbiterPrompt no longer orders a figure render')

// The THIRD render order: LENS_BRIEF.numbers is a separate template literal
// interpolated into lensPrompt, and it is the lens whose job is reading values
// off a rendered page -- i.e. the exact behaviour that produced R72. A
// lens-specific brief outranks generic Method boilerplate in an agent's
// attention, so it names the target too. Cut out as its own span (it is the last
// key, so the close anchors on the object's `}`), with a sanity anchor first.
const numSpan = /\n  numbers: `([\s\S]*?)`,?\s*\n\}/.exec(wf)
check(!!numSpan, 'LENS_BRIEF.numbers not found (renamed or deleted)')
if (numSpan) {
  check(/LENS: NUMBERS/.test(numSpan[1]), 'LENS_BRIEF.numbers span cut wrong -- it does not open "LENS: NUMBERS"')
  check(/render the page image to a temp dir \(never inside raw\/\)/.test(numSpan[1]),
    'LENS_BRIEF.numbers: the "render the page image" order names no write target')
  check(/render the page image/.test(numSpan[1]), 'LENS_BRIEF.numbers no longer orders a page render')
}

// --- 2. the ingest skill's own render lesson (the contact-sheet two-pass).
const bullets = ingest.split(/\n(?=- \*\*)/)
const renderBullet = bullets.find(b => /tile 4-up into contact sheets/.test(b))
check(!!renderBullet, 'ingest SKILL.md: the contact-sheet render lesson is gone')
if (renderBullet) {
  // "an agent makes while reading a source" is load-bearing: human scans and
  // screen_capture.ps1 output land in raw/ by design (see the screen capture lesson below it
  // and _meta/book-scanning.md), so an absolute "never into raw/" would
  // contradict the same file two bullets later.
  check(/Renders an agent makes while reading a source go to a temp dir, never into `raw\/`/.test(renderBullet),
    'ingest contact-sheet lesson: renders still name no write target (or the agent-only scope was dropped)')
  check(/fleet-conventions\.md/.test(renderBullet),
    'ingest contact-sheet lesson: does not point at the fleet-conventions carve-out (the rule is stated once, there)')
}

// --- 3. the fleet-wide carve-out, with its boundary intact in BOTH directions.
// Whitespace is collapsed first so the assertions survive a re-wrap and the
// blockquote markers; the wording itself is asserted strictly.
const fleetFlat = fleet.replace(/^>\s?/gm, '').replace(/\s+/g, ' ')
check(/raw\/ holds only what a human, a capture script, or an ingest banking a source it recovered put there; an agent's derived render or scratch file goes to a temp dir/.test(fleetFlat),
  '_meta/fleet-conventions.md: the raw/ carve-out sentence is missing or reworded')
check(/screen_capture\.ps1/.test(fleetFlat),
  'fleet-conventions raw/ section: the legitimate-writer carve-out (capture scripts) is gone -- a blanket ban contradicts documented practice')
// The banking exception must stay explicit. Without it the carve-out contradicts
// the ingest skill's own three-probe recovery lesson, which orders an agent to
// bank a recovered fulltext IN raw/ -- and an agent resolving that contradiction
// could drop the only surviving copy of a stranded source into a temp dir.
check(/banking clause is the narrow exception and the test is reproducibility/.test(fleetFlat),
  'fleet-conventions raw/ section: the banked-recovery exception is gone -- it contradicts the ingest skill\'s three-probe lesson ("Bank the extraction in raw/")')

// --- 4. regression R71: the heredoc lesson, in the ingest Lessons section.
const heredoc = bullets.find(b => /never a Bash heredoc/.test(b))
check(!!heredoc, 'ingest SKILL.md: the heredoc lesson (regression R71) is missing')
if (heredoc) check(/--body-file/.test(heredoc),
  'heredoc lesson: lacks the --body-file generalization (the workaround that made filing R71 possible)')

// --- 5. regression R71: the back-port rule, at the skill-edit gate that creates the
// ahead copy. Scoped to gate 4, which is the instruction that caused the loss.
const gate = /4\. \*\*Skill-edit gate\.\*\*([\s\S]*?)\n4b\./.exec(ingest)
check(!!gate, 'ingest SKILL.md: the skill-edit gate (step 4) is gone')
if (gate) {
  check(/back-port/i.test(gate[1]), 'skill-edit gate: does not tell the session to back-port the drafted lesson to the template')
  check(/not a safe state/.test(gate[1]), 'skill-edit gate: does not say an ahead copy + a ledger note is not a safe state')
}

if (fails.length) { console.log('FAIL\n  ' + fails.join('\n  ')); process.exit(1) }
console.log('PASS: claim-audit.js parses; lensPrompt, arbiterPrompt and LENS_BRIEF.numbers each name the render target at the instruction site while still ordering the render, and both prompt preambles forbid writes; the ingest contact-sheet lesson names the target and defers to fleet-conventions; the raw/ carve-out keeps both boundaries plus the banked-recovery exception; the heredoc lesson and the back-port rule are present (regression R71, R72)')
