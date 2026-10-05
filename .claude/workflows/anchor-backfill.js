export const meta = {
  name: 'anchor-backfill',
  description: 'Backfill #heading anchors on sources-citing claims: apply AUTO, agent-verify HIGH, agent-decide REVIEW per citing file, drift-check NOMATCH, apply via decisions JSON',
  whenToUse: 'When locator coverage is below target and suggest_anchors.py reports a pile. Run from a wiki vault root. Invoke: Workflow({name: "anchor-backfill"}). The calling session fixes reported drift candidates afterward.',
  phases: [
    { title: 'Scan', detail: 'apply AUTO tier, re-scan, partition the rest' },
    { title: 'Decide', detail: 'HIGH verified + REVIEW decided per citing file (sonnet); NOMATCH drift-checked (opus)' },
    { title: 'Apply', detail: 'decisions JSON written and applied via suggest_anchors.py --apply, then --check; applied count parsed from the script output' },
  ],
}

// Model split (do not change casually): sonnet = mechanical read-and-decide; opus = adversarial judgment.
// All paths are RELATIVE to the vault root -- workflows run with cwd = the vault.

const JSONPATH = '_meta/anchor-suggestions.json'
const DECISIONS_PATH = '_meta/anchor-decisions-latest.json'
// Written by suggest_anchors.py --apply-auto itself (regression R94): the pages and
// lines the Scan step's AUTO tier actually anchored. JSONPATH cannot answer
// that -- the fresh re-scan below overwrites it with the post-AUTO list, which
// by construction no longer holds what just landed. Deliberately NOT routed
// through an agent's schema: retyping hundreds of (rel, lineno, slug, anchor)
// tuples is the retranscription risk DECISIONS_SCHEMA's `line` comment guards
// against, and the calling session already reads DECISIONS_PATH off disk, so a
// second _meta file is the same motion.
const AUTO_RECORD_PATH = '_meta/anchor-auto-applied-latest.json'

// Every agent that touches disk gets this. The calling session owns close-out
// (one log.md entry, one ledger pass per run); an agent that writes its own
// leaves a second, contradictory record (regression R86, sample-vault-a 2026-09-18).
const NO_CLOSEOUT = `You create or edit no files yourself except the one(s) this step names; the script you run makes its own edits (wiki pages, _meta/*.json), and that is expected. Never write log.md, _meta/open-loops.md, or any wiki page by hand. Do not run lint.py, check_stale.py, maintenance_preflight.py, a maintenance pass, or any close-out -- the calling session owns close-out. Do not summarize, judge, park, or "fix" anything.`

const APPLY_SCHEMA = {
  type: 'object', required: ['raw_output'],
  properties: { raw_output: { type: 'string', description: 'complete unedited stdout+stderr of the --apply and --check commands, in order' } },
}

// -- parseApply begin
// `applied` is MEASURED from suggest_anchors.py's own "=== APPLIED ===" line,
// never from the number of decisions submitted (that was the mislabeled field
// in regression R86). No line -> null: "unmeasured", not zero.
function parseApply(raw) {
  const text = typeof raw === 'string' ? raw : ''
  const ap = /=== APPLIED === (\d+) anchor\(s\)[^\n]*?(\d+) skipped/.exec(text)
  const ck = /=== ANCHOR CHECK === (\d+) broken, (\d+) suspect/.exec(text)
  const cov = /coverage now: (\d+)\/(\d+) \((\d+)%\)/.exec(text)
  return {
    applied: ap ? Number(ap[1]) : null,
    skipped: ap ? Number(ap[2]) : null,
    broken: ck ? Number(ck[1]) : null,
    suspect: ck ? Number(ck[2]) : null,
    coverage_after: cov ? `${cov[1]}/${cov[2]} (${cov[3]}%)` : null,
    status: ap ? 'measured from the === APPLIED === line' : 'UNMEASURED: no === APPLIED === line in the apply output -- verify on disk (suggest_anchors.py --check + lint LOCATOR line)',
  }
}
// The Scan step's --apply-auto prints a DIFFERENT line ("N AUTO anchor(s); M
// skipped") that parseApply deliberately does not match; parsed separately so
// the AUTO tier is reported, not lost (it is applied before the fresh scan).
function parseAuto(line) {
  const m = /=== APPLIED === (\d+) AUTO anchor\(s\); (\d+) skipped/.exec(typeof line === 'string' ? line : '')
  return m ? { auto_applied: Number(m[1]), auto_skipped: Number(m[2]) } : { auto_applied: null, auto_skipped: null }
}
// -- parseApply end

const SCAN_SCHEMA = {
  type: 'object', required: ['counts', 'review_files', 'high_count', 'nomatch_count', 'auto_applied_line'],
  properties: {
    counts: { type: 'string' },
    auto_applied_line: { type: 'string', description: 'the "=== APPLIED === N AUTO anchor(s); M skipped" line printed by --apply-auto, copied verbatim ("" if it did not print)' },
    review_files: { type: 'array', items: { type: 'object', required: ['rel', 'n'], properties: { rel: { type: 'string' }, n: { type: 'number' } } } },
    high_count: { type: 'number' },
    nomatch_count: { type: 'number' },
  },
}

const DECISIONS_SCHEMA = {
  type: 'object', required: ['decisions'],
  properties: { decisions: { type: 'array', items: {
    type: 'object', required: ['rel', 'lineno', 'slug', 'line', 'heading', 'note'],
    properties: { rel: { type: 'string' }, lineno: { type: 'number' }, slug: { type: 'string' },
      line: { type: 'string', description: "the entry's recorded claim-line text, copied verbatim from the suggestions JSON (suggest_anchors.py --apply skips the entry if the page line no longer starts with it)" },
      heading: { type: ['string', 'null'] }, note: { type: 'string' } } } } },
}

const NOMATCH_SCHEMA = {
  type: 'object', required: ['findings'],
  properties: { findings: { type: 'array', items: {
    type: 'object', required: ['rel', 'lineno', 'slug', 'line', 'verdict', 'heading', 'evidence'],
    properties: { rel: { type: 'string' }, lineno: { type: 'number' }, slug: { type: 'string' },
      line: { type: 'string', description: 'recorded claim-line text, verbatim from the suggestions JSON' },
      verdict: { type: 'string', enum: ['SUPPORTED-UNSECTIONED', 'STRUCTURAL', 'DRIFT-CANDIDATE'] },
      heading: { type: ['string', 'null'] }, evidence: { type: 'string' } } } } },
}

const COMMON = `You are working in a wiki vault (current working directory = vault root). Read ${JSONPATH} (UTF-8 JSON; claim entries under the "entries" key).
An entry = one claim line in a citing wiki page whose [[sources/<slug>]] wikilink lacks a #heading anchor. Decide which section of wiki/sources/<slug>.md actually CONTAINS THE SUPPORT (the quote, figure, date, or statement the claim rests on).
Method per entry: (1) open the citing page and locate the claim by its "line" TEXT (line numbers may have shifted -- trust the text); read enough context to know what it asserts. (2) Read the candidate sections on the sources page (the "candidates" list gives exact heading strings; scores are hints, not verdicts). (3) Choose the ONE heading whose section carries the support; a better non-candidate section is allowed -- copy its heading verbatim from the page. If NO section supports the claim, heading: null and say in the note what is missing (these are drift candidates the calling session must review).
Return heading strings EXACTLY as they appear on the sources page (verbatim copy -- preserve unicode dashes, do not retype). READ-ONLY: edit no files. Keep each entry's rel/lineno/slug/line unchanged in your output -- "line" is the recorded claim text from the JSON, copied byte-for-byte (it is the apply step's stale-line guard: a retyped line makes the entry SKIP, never mis-apply).`

phase('Scan')

const scan = await agent(
  `You are in a wiki vault root. Run: python scripts/suggest_anchors.py --apply-auto  (never pipe it through head). Then run: python scripts/suggest_anchors.py  (fresh scan). Then read ${JSONPATH} and return: auto_applied_line = the "=== APPLIED === ... AUTO anchor(s); ... skipped" line the first command printed, verbatim; counts = the tier-count line as a string; review_files = one {rel, n} per DISTINCT citing file among tier==REVIEW entries (n = its entry count); high_count = number of tier HIGH_REVIEW + AUTO entries; nomatch_count = number of tier NOMATCH entries. Return only what the schema asks. ${NO_CLOSEOUT}`,
  { label: 'scan+apply-auto', phase: 'Scan', schema: SCAN_SCHEMA, model: 'sonnet' }
)
if (!scan) throw new Error('scan agent failed')
const auto = parseAuto(scan.auto_applied_line)
log(`AUTO tier: ${auto.auto_applied === null ? 'unmeasured (no APPLIED line returned)' : `${auto.auto_applied} applied, ${auto.auto_skipped} skipped`}. Post-AUTO scan: ${scan.counts}`)

// Partition REVIEW citing files into batches of roughly <= 8 entries, largest files solo.
const sorted = [...scan.review_files].sort((a, b) => b.n - a.n)
const batches = []
let cur = { files: [], n: 0 }
for (const f of sorted) {
  if (f.n >= 5) { batches.push({ files: [f.rel], n: f.n }); continue }
  if (cur.n + f.n > 8 && cur.files.length) { batches.push(cur); cur = { files: [], n: 0 } }
  cur.files.push(f.rel); cur.n += f.n
}
if (cur.files.length) batches.push(cur)
log(`REVIEW: ${sorted.reduce((s, f) => s + f.n, 0)} entries across ${sorted.length} files -> ${batches.length} agents`)

phase('Decide')

const highTask = scan.high_count === 0 ? Promise.resolve({ decisions: [] }) : agent(
  COMMON + `\n\nYOUR ASSIGNMENT: every entry with tier "HIGH_REVIEW" or "AUTO" (${scan.high_count} entries). These arrive with a pre-filled "anchor". Verify each: does the anchored section really carry the claim's support? Return it unchanged if yes; the correct heading if another section supports it; null if nothing does.`,
  { label: 'verify:HIGH', phase: 'Decide', schema: DECISIONS_SCHEMA, model: 'sonnet' })

const reviewTasks = batches.map((b, i) => agent(
  COMMON + `\n\nYOUR ASSIGNMENT: every entry with tier == "REVIEW" whose "rel" is one of: ${b.files.join(', ')}.`,
  { label: `review:${b.files[0].split('/').pop().slice(0, 24)}${b.files.length > 1 ? '+' + (b.files.length - 1) : ''}`, phase: 'Decide', schema: DECISIONS_SCHEMA, model: 'sonnet' }))

const nomatchTask = scan.nomatch_count === 0 ? Promise.resolve({ findings: [] }) : agent(
  `You are in a wiki vault root. READ-ONLY. Read ${JSONPATH} ("entries") and take every entry with tier == "NOMATCH" (${scan.nomatch_count}) -- claims where no section heading matched; each is a potential drift case. For each: locate the claim by its "line" text, read its context, then read the ENTIRE cited wiki/sources/<slug>.md. Verdicts: SUPPORTED-UNSECTIONED (support exists outside matchable sections -- give a usable heading if any, else null and describe where); STRUCTURAL (navigational link, not a claim citation -- pointer rows, callout headers); DRIFT-CANDIDATE (the sources page does not carry what the line asserts -- quote what IS there vs what is claimed). Default to DRIFT-CANDIDATE only on concrete evidence of a gap, STRUCTURAL only when the line makes no checkable claim. Never force an anchor.`,
  { label: 'nomatch:drift-check', phase: 'Decide', schema: NOMATCH_SCHEMA, model: 'opus' })

const [high, reviews, nomatch] = await Promise.all([highTask, Promise.all(reviewTasks), nomatchTask])

// No silent caps: a dead decide agent must not read as "nothing to decide".
const batchesLost = reviews.filter(r => !r).length + (scan.high_count && !high ? 1 : 0)
const nomatchOk = scan.nomatch_count === 0 || !!nomatch
if (batchesLost || !nomatchOk) log(`WARNING: ${batchesLost} decide batch(es) returned nothing${nomatchOk ? '' : '; NOMATCH drift check returned nothing'} -- their entries are NOT in the decisions below`)
const review = reviews.filter(Boolean).flatMap(r => r.decisions)
const nulls = review.filter(d => !d.heading).concat((high ? high.decisions : []).filter(d => !d.heading))
const supported = (nomatch ? nomatch.findings : []).filter(f => f.verdict === 'SUPPORTED-UNSECTIONED' && f.heading)
const toApply = (high ? high.decisions : []).concat(review).filter(d => d.heading)
  .concat(supported.map(f => ({ rel: f.rel, lineno: f.lineno, slug: f.slug, line: f.line, heading: f.heading, note: 'from NOMATCH drift check' })))
log(`Decisions: ${toApply.length} anchorable, ${nulls.length} null (drift/borderline), NOMATCH: ${(nomatch ? nomatch.findings : []).map(f => f.verdict).join(', ') || 'none'}`)

phase('Apply')

const applyResult = toApply.length === 0 ? null : await agent(
  `You are in a wiki vault root, as the final step of the anchor-backfill workflow.

WHERE THIS JSON COMES FROM: the Decide-phase agents of this same workflow read this vault's pages and chose these anchors; the workflow script (.claude/workflows/anchor-backfill.js, Apply phase) assembled their decisions into the JSON below and handed it to you. Three facts make vetting it NOT your job: (1) suggest_anchors.py --apply refuses any anchor that is not cleanly linkable, any entry whose "line" text no longer matches the start of the page line it names, and any entry whose line carries no unanchored [[sources/<slug>]] link -- a wrong or stale entry is SKIPPED, not written, and printed as a SKIP line; (2) --check then validates every anchor on disk; (3) the calling session reconciles the result against the vault afterwards. So do not re-decide, filter, or reject entries, and do not substitute other work: apply the file and report what the script printed.

YOUR ONLY JOB, three steps, nothing else:
1. Write the following JSON EXACTLY (no edits, no reformatting of heading strings -- they must stay byte-identical) to ${DECISIONS_PATH}:

${JSON.stringify({ entries: toApply.map(d => ({ rel: d.rel, lineno: d.lineno, slug: d.slug, line: d.line, anchor: d.heading })) })}

2. Run: python scripts/suggest_anchors.py --apply ${DECISIONS_PATH}
3. Run: python scripts/suggest_anchors.py --check
(Never pipe either through head.) Return the complete, unedited output of both commands as raw_output -- every SKIP line, the "=== APPLIED ===" line, the "=== ANCHOR CHECK ===" line, the "coverage now:" line. A NON-ZERO EXIT CODE IS EXPECTED whenever the vault has any pre-existing broken anchor (both commands exit 1 on "broken > 0", which counts the whole vault, not this run): that is not a failure -- keep the full output and continue to the next step. Only a traceback or a "file not found" is a failure; then put that text in raw_output and stop.

${NO_CLOSEOUT}`,
  { label: 'apply+check', phase: 'Apply', schema: APPLY_SCHEMA, model: 'sonnet' })

const measured = toApply.length === 0
  ? { applied: 0, skipped: 0, broken: null, suspect: null, coverage_after: null, status: 'nothing submitted; --apply not run' }
  : parseApply(applyResult ? applyResult.raw_output : '')
if (measured.applied !== null && measured.applied < toApply.length) {
  // the R86 signature: fewer landed than were sent. Say so where a session quotes it.
  measured.status = `${measured.applied} of ${toApply.length} submitted landed; ${measured.skipped} skipped -- see the SKIP lines in apply_report`
}
if (toApply.length && measured.applied === null) log(`Apply: ${measured.status}`)
else log(`Apply: submitted ${toApply.length}, applied ${measured.applied}, skipped ${measured.skipped}`)

return {
  scan: scan.counts,
  auto_applied: auto.auto_applied,
  auto_skipped: auto.auto_skipped,
  auto_record: AUTO_RECORD_PATH,
  batches_lost: batchesLost,
  nomatch_ok: nomatchOk,
  submitted: toApply.length,
  applied: measured.applied,
  skipped: measured.skipped,
  apply_status: measured.status,
  check: { broken: measured.broken, suspect: measured.suspect, note: 'vault-wide counts from --check, pre-existing anchors included; not caused by this run' },
  coverage_after: measured.coverage_after,
  apply_report: applyResult ? applyResult.raw_output : (toApply.length ? 'apply agent returned nothing' : 'nothing to apply'),
  drift_candidates: nulls,
  nomatch: nomatch ? nomatch.findings : [],
  followups: 'Calling session: `applied` counts the decisions pass only, `auto_applied` the Scan-phase AUTO tier; both are parsed from suggest_anchors.py output (null = unmeasured); still verify on disk per the wiki-maintenance skill. Read `auto_record` (the path above) for the pages the AUTO tier actually anchored -- the re-scan drops them from the suggestions file, so that file, not this result, names them; entries: [] means the AUTO tier wrote nothing, and an absent file means the vault runs a suggest_anchors.py older than regression R94 (sync it) -- either way do not assume. Review drift_candidates and DRIFT-CANDIDATE nomatch findings (fix or ledger them), bump updated: on touched pages, log the operation, run lint.py --summary and check_stale.py --summary.',
}
