export const meta = {
  name: 'claim-audit',
  description: 'Run audit_claims.py, read every sampled claim with a three-lens Sonnet panel (wording / scope / numbers), escalate any dissent to an Opus arbiter, spot-check one unanimous clear',
  whenToUse: 'The quarterly claim audit, or after big backfills/migrations. Run from a wiki vault root. Invoke: Workflow({name: "claim-audit", args: {n: 10}}). args.seed (a bare token, e.g. "2026-Q4") overrides audit_claims.py\'s default quarterly seed; in the default risk mode it does two things -- it draws the seeded-random remainder (args.n minus ceil(args.n * 0.7)) AND it breaks ties inside the risk ranking, because the sampler shuffles the population under the seed before a stable sort by risk. So whether a queue moves depends on whether the claims at the rank cut are TIED; observed on sample-vault-a (65 claims, 2026-09-18), --n 10 changed 5 of 10 entries while --n 3 returned an identical queue. A seed override is a lever, not on its own a cure for a re-served same-quarter queue; the cure is the judged-claims ledger (regression R52): this workflow is the quarterly deep audit and passes --include-judged by default (re-reads claims judged FAITHFUL this quarter); args.include_judged: false skips them instead, so an ad-hoc run samples only claims not yet judged FAITHFUL this quarter. args.include is an ARRAY of "FILE:LINE" specs force-included in the sample -- any other shape, or an empty entry, THROWS rather than being dropped; forced entries are prepended, so printed n= exceeds args.n. Replay/benchmark: args.claims (a fixed queue) plus args.root / args.raw_root (a snapshot tree). The calling session applies fixes ONLY where fix_ok is true, logs `lint | claim audit (seed, n, hits; split S, unconfirmed U; escalated E/N (reasons: k1=n1, k2=n2))` (the returned log_line is that shape filled in), persists the returned run_record with `python scripts/audit_claims.py --record-run <file>`, and re-stamps Last run / Next due.',
  phases: [
    { title: 'Queue', detail: 'audit_claims.py draws the risk-weighted sample, optionally under args.seed / args.include (skipped when args.claims is given)' },
    { title: 'Panel', detail: 'three independent Sonnet readers per claim, one lens each: wording, scope, numbers' },
    { title: 'Arbiter', detail: 'Opus at high effort on every claim the panel did not clear unanimously, plus any claim whose raw pointer is missing or an image, or whose numbers were read off a chart' },
    { title: 'Spot-check', detail: 'Opus re-reads one unanimously cleared claim looking for what all three missed' },
  ],
}

// Design: docs/adr/0002-sonnet-lens-panel-opus-arbiter.md (supersedes 0001, the Codex witness).
// Lessons baked in:
// - Judge against the RAW PRIMARY, never only the wiki's own restatement of it (2026-08-08).
// - The single judge was the noise: same seed, same 10 claims, two opus runs disagreed on 3 of 10
//   (2026-09-14 vs 2026-09-15), and a one-judge FAITHFUL was never challenged. So every claim is
//   read three times, by DIFFERENT lenses, and dissent escalates -- it is never outvoted.
// - Readers and the arbiter are READ-ONLY; the calling session applies fixes.
// - audit_claims.py emits ONE queue entry PER WIKILINK MATCH: the same file:line can appear more
//   than once, even under the same slug. Keep every printed entry; a count mismatch is a WARNING,
//   never a throw (a throw there bricked the audit for a quarter -- deterministic seed).
// - Buckets must partition the run: hits + cleared + split + unconfirmed + skipped_faithful == n.

const N = (args && Number.isInteger(args.n) && args.n > 0) ? args.n : 10
const ROOT = (args && args.root) ? String(args.root).replace(/[\\/]+$/, '') : ''
const RAW_ROOT = (args && args.raw_root) ? String(args.raw_root).replace(/[\\/]+$/, '') : (ROOT ? `${ROOT}/raw` : 'raw')
const SPOT_CHECKS = (args && Number.isInteger(args.spot_check) && args.spot_check >= 0) ? args.spot_check : 1
// Flags audit_claims.py already has, exposed so a caller can rotate the queue off the
// deterministic quarterly seed and force-include named claims (regression R52). Both empty by
// default: with neither, the Queue command is byte-identical to the pre-ledger (pre-R52) form.
const SEED = (args && args.seed != null && String(args.seed).trim()) ? String(args.seed).trim() : ''
// -- includeList begin
// args.include shape check. queueCommand validates each SPEC, but validation cannot fire
// on a value that never reaches it: an args.include that is present and malformed used to
// be normalized away to [], and the run then served the DEFAULT queue with no error and no
// log line -- exactly the quiet no-op ("Q4: --include the anchor-resistant claims" audits
// the usual suspects instead) the throw exists to prevent. One accepted shape: an array of
// non-empty strings. Everything else throws and names the required form.
function includeList(a) {
  if (!a || a.include == null) return []
  if (!Array.isArray(a.include)) {
    throw new Error(`args.include must be an ARRAY of "FILE:LINE" strings (e.g. ["wiki/analysis/positions-register.md:42"]), not ${typeof a.include === 'string' ? JSON.stringify(a.include) : typeof a.include} -- refusing to run an audit that silently drops the force-includes`)
  }
  return a.include.map((s, i) => {
    const spec = (typeof s === 'string' || typeof s === 'number') ? String(s).trim() : ''
    if (!spec) throw new Error(`args.include[${i}] is empty or not a string (${JSON.stringify(s)}) -- every entry must be a "FILE:LINE" spec`)
    return spec
  })
}
// -- includeList end
const INCLUDE = includeList(args)
// Judged-claims ledger (regression R52, ruled 2026-09-19): this workflow IS the quarterly
// stamp-driven deep audit, so by default it passes --include-judged and re-reads claims the
// ledger shows FAITHFUL this quarter. args.include_judged: false opts an ad-hoc run (e.g. after
// a backfill) into the skip, so it samples only claims not yet judged FAITHFUL this quarter.
// Anything but a boolean throws: a silently misread flag would change which queue is served.
function includeJudged(a) {
  if (!a || a.include_judged == null) return true
  if (typeof a.include_judged !== 'boolean') throw new Error(`args.include_judged must be true or false, not ${JSON.stringify(a.include_judged)}`)
  return a.include_judged
}
const INCLUDE_JUDGED = includeJudged(args)
const LABEL_LEN = 24
const EVIDENCE_CAP = 6000
const ROW_EVIDENCE_CAP = 4000

const VERDICTS = ['FAITHFUL', 'DRIFTED', 'UNSUPPORTED', 'STALE']
const LENS_NAMES = ['wording', 'scope', 'numbers']

const QUEUE_SCHEMA = {
  type: 'object', required: ['seed', 'printed_n', 'coverage', 'claims'],
  properties: {
    seed: { type: 'string' }, printed_n: { type: 'number' }, coverage: { type: 'string' },
    claims: { type: 'array', items: {
      type: 'object', required: ['file', 'line', 'slug', 'raw', 'excerpt', 'key'],
      properties: { file: { type: 'string' }, line: { type: 'number' }, slug: { type: 'string' }, raw: { type: 'string' }, excerpt: { type: 'string' }, key: { type: 'string' } } } },
  },
}

const LENS_SCHEMA = {
  type: 'object', required: ['verdict', 'evidence', 'proposed_fix', 'needs_arbiter', 'arbiter_reason', 'primary_kind'],
  properties: {
    verdict: { type: 'string', enum: VERDICTS },
    evidence: { type: 'string' },
    proposed_fix: { type: ['string', 'null'] },
    needs_arbiter: { type: 'boolean' },
    arbiter_reason: { type: ['string', 'null'] },
    primary_kind: { type: 'string', enum: ['text', 'image-or-chart', 'absent'] },
  },
}

const ARBITER_SCHEMA = {
  type: 'object', required: ['final_verdict', 'certainty', 'severity', 'reasoning', 'proposed_fix', 'fix_ok', 'lens_findings_upheld'],
  properties: {
    final_verdict: { type: 'string', enum: VERDICTS },
    certainty: { type: 'string', enum: ['clear', 'borderline'] },
    severity: { type: 'string', enum: ['none', 'minor', 'material'] },
    reasoning: { type: 'string' },
    proposed_fix: { type: ['string', 'null'] },
    fix_ok: { type: 'boolean' },
    lens_findings_upheld: { type: 'array', items: { type: 'string', enum: LENS_NAMES } },
  },
}

const errMsg = e => (e && e.message ? String(e.message) : String(e)).slice(0, 300)
const at = p => (ROOT ? `${ROOT}/${p}` : p)
// audit_claims.py prints the raw pointer as "(none named on page)", "(sources page NOT FOUND ...)",
// or up to three pointers joined with "; ". A replay queue may also carry a "raw/" prefix already.
// Parenthesized text means no pointer; everything else is split and de-prefixed.
const rawList = r => (!r || /^\(/.test(String(r).trim()))
  ? []
  : String(r).split(/;\s*/).map(s => s.trim()).filter(Boolean)
    .map(s => (/^([a-zA-Z]:|[\\/])/.test(s) ? s : s.replace(/^raw[\\/]/i, '')))
const rawPath = r => { const l = rawList(r); return l.length ? l.map(x => (/^([a-zA-Z]:|[\\/])/.test(x) ? x : `${RAW_ROOT}/${x}`)).join('; ') : '(none)' }
const IMAGE_RAW = /\.(jpe?g|png|gif|webp|heic|bmp|tiff?)$/i

// -- queueCommand begin
// Composes the Queue agent's audit_claims.py command line. Self-contained and
// marker-delimited so scripts/tests/test_claim_audit_queue_cmd.mjs can cut it out and
// eval it (phase()/agent() are runtime globals, so the workflow cannot be imported).
// INVARIANT: queueCommand(n, '', []) is byte-identical to the pre-regression R52 command; the
// workflow itself passes includeJudged = true by default, which appends ' --include-judged'.
// The agent runs this string as a Bash command, so values reaching the shell are
// VALIDATED, not escaped-and-hoped: a bad value throws rather than silently serving a
// different queue than the caller asked for (a dropped force-include would make the
// decision-calendar's "Q4: --include the anchor-resistant claims" quietly a no-op).
const SHELL_UNSAFE = /["'`$;&|<>\\\r\n]/
const SEED_TOKEN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/
const INCLUDE_SPEC = /^[^:]+:\d+$/
function queueCommand(n, seed, include, includeJudged) {
  let cmd = `python scripts/audit_claims.py --n ${n}`
  if (seed) {
    if (!SEED_TOKEN.test(seed)) throw new Error(`args.seed ${JSON.stringify(seed)} is not a bare token (letters/digits then . _ -) -- refusing to build a shell command from it`)
    cmd += ` --seed ${seed}`
  }
  for (const entry of (include || [])) {
    // audit_claims.py normalizes backslashes itself; doing it here too means no
    // backslash ever reaches a double-quoted Bash argument.
    const spec = String(entry).trim().replace(/\\/g, '/')
    if (!INCLUDE_SPEC.test(spec) || SHELL_UNSAFE.test(spec)) {
      throw new Error(`args.include entry ${JSON.stringify(entry)} is not a safe FILE:LINE spec (repo-relative path, then ":", then a line number; no quotes or shell metacharacters) -- refusing to build a shell command from it`)
    }
    cmd += ` --include "${spec}"`
  }
  if (includeJudged) cmd += ' --include-judged'
  return cmd
}
// -- queueCommand end

phase('Queue')

let queue
if (args && Array.isArray(args.claims) && args.claims.length) {
  queue = { seed: String(args.seed || 'replay'), printed_n: args.claims.length, coverage: '(replay: queue supplied via args.claims)', claims: args.claims }
} else {
  const QUEUE_CMD = queueCommand(N, SEED, INCLUDE, INCLUDE_JUDGED)
  // Both notes are conditional on the flags actually passed. Since the judged-claims ledger
  // (regression R52) the default run passes --include-judged, so FLAG_NOTE fires by default too;
  // only an include_judged:false run with no seed and no includes sends the pre-ledger (pre-R52) prompt.
  const FLAG_NOTE = (SEED || INCLUDE.length || INCLUDE_JUDGED)
    ? ' Run it EXACTLY as written -- do not add, drop or reorder flags, and if it errors do not re-run it without them; report the error instead.'
    : ''
  const INCLUDE_NOTE = INCLUDE.length
    ? ` --include force-includes named claims and they are PREPENDED to the sample, so the printed n= may EXCEED --n ${N}: report printed_n exactly as printed and return every printed entry -- never truncate the array to ${N}.`
    : ''
  queue = await agent(
    `You are in a wiki vault root. Run: ${QUEUE_CMD}  (never pipe it through head).${FLAG_NOTE}${INCLUDE_NOTE} Parse its output and return: seed (e.g. "2026-Q3"), printed_n (the n= value on the "=== CLAIM-AUDIT SAMPLE ===" line, as a number), coverage (the locator-coverage line as a string), and claims -- one object per verification-queue item: file (repo-relative citing page), line (number), slug (the cited sources page slug, no "sources/" prefix), raw (the raw-file pointer string, or "(none)"), excerpt (the claim text shown, trimmed), key (the 16-hex id= token at the end of the entry's file:line line, or "" if the line has none). EMIT ONE CLAIM OBJECT PER PRINTED QUEUE ENTRY and never dedupe: the script prints one entry per wikilink match, so the same file:line legitimately appears more than once -- under two different slugs, and also TWICE UNDER THE SAME SLUG with byte-identical text. Keep the identical repeats too; your claims array length must equal printed_n.`,
    { label: `queue:n=${N}${SEED ? ` seed=${SEED}` : ''}${INCLUDE.length ? ` +${INCLUDE.length}inc` : ''}${INCLUDE_JUDGED ? '' : ' skip-judged'}`, phase: 'Queue', schema: QUEUE_SCHEMA, model: 'sonnet' }
  )
  if (!queue || !queue.claims.length) throw new Error('queue agent returned no claims')
  if (queue.claims.length !== queue.printed_n) log(`WARNING: queue agent returned ${queue.claims.length} claims but audit_claims.py printed n=${queue.printed_n} -- entries were collapsed or invented; n below reports what was actually judged`)
}
log(`Seed ${queue.seed}, ${queue.claims.length} claims, panel=3 sonnet lenses + opus arbiter, spot_check=${SPOT_CHECKS}. ${queue.coverage}`)

const treeNote = ROOT
  ? `\nTREE: every wiki read (citing page, sources pages, vault-wide searches for support) uses ONLY the tree under ${ROOT}/wiki -- never another copy of the vault. Raw primaries live under ${RAW_ROOT}.`
  : ''

const claimBlock = c =>
  `CLAIM: ${at(c.file)} line ~${c.line} (locate by text if lines shifted): "${c.excerpt}"
CITED SOURCE PAGE: ${at(`wiki/sources/${c.slug}.md`)}   RAW PRIMARY: ${rawPath(c.raw)}${treeNote}`

const LENS_BRIEF = {
  wording: `LENS: WORDING AND QUOTATION FIDELITY. Your one question: does the claim's WORDING say what the primary says? Check every quoted span verbatim against the primary (splices, dropped words inside quotes, ellipses that change meaning), and every close paraphrase for a changed degree word, modal, quantifier or hedge (e.g. "rarely" vs "sometimes", "can" vs "will", "linked to" vs "leads to", "most" vs "all", "similar" vs "the same"). A single narrowed or strengthened word that the argument then leans on is DRIFTED.`,
  scope: `LENS: SCOPE, ATTRIBUTION AND CURRENCY. Your one question: does the cited source cover THIS claim, at the breadth claimed, and is it still the vault's current reading? Split the claim into clauses and map each clause to its citation; judge only the clauses carried by the cited slug. Flag: a citation attached to a clause the source does not cover; a characterization of the source -- or a comparison between the source and the vault's own position -- that goes beyond what the source page and primary support; a clause contradicted by the source page's own characterization; attribution to the wrong study arm, author, speaker or document; a population, setting or subgroup stretched beyond what was studied. Currency: check whether a later sources page on the same topic, or the vault's own log/positions entries, supersede what the claim asserts -- if so the verdict is STALE.`,
  numbers: `LENS: NUMBERS, DATES, UNITS, PERIODS AND DEFINITIONS. Your one question: is every figure, range, CI, date, unit, sample size, time window, rank or order word and operational definition exactly supported by the primary? Recompute any arithmetic. When the claim states ONE definition, instrument, threshold or cutoff, check it holds for EVERY group, arm, wave or condition it is applied to. For values in figures, charts or slides, render the page image to a temp dir (never inside raw/) and read the actual values -- say in evidence that you did.`,
}

const lensPrompt = (c, lens) =>
  `Claim audit in a wiki vault (cwd = vault root). READ-ONLY -- edit nothing AND write nothing into the vault: every page render, extracted text file and scratch file you make goes in a system temp dir, never inside raw/ (it is the provenance store -- an agent-written file there becomes a pending ADDED file forever) and never under wiki/. You are ONE of three independent readers; each reader checks a different lens, and nobody sees the others' work.
${claimBlock(c)}
${LENS_BRIEF[lens]}
Method: read the claim in full context on the citing page (if the queue line is a callout header or lead-in, the real claims are the lines under it -- judge those); read the cited sources page, following the #anchor if the link carries one; then verify against the RAW primary where one exists (PDFs: pdftotext or python pypdf; figures and slides: render with PyMuPDF to a temp dir (never inside raw/) and view the image; image files: view directly). Judge against the PRIMARY, never merely the wiki's restatement; never conclude "absent from the literature" from absence on the sources page alone; never assert from model memory.
OUT OF SCOPE for every lens: whether the claim fits, supports or contradicts the vault's own positions, plans or arguments. The audit judges ONE thing -- does the cited source support what is asserted about it. A claim that characterizes the relationship between a source and the vault's own position is judged only on whether the SOURCE (and its sources page) supports that characterization, never on whether the vault's position is coherent.
Still IN SCOPE: every clause the citation is attached to, including clauses inside a sentence that argues for a vault position -- an unsupported clause riding on a cited one is exactly what this audit catches. Also IN SCOPE: the currency check -- if a later source, or the vault's own log or positions entries, supersede what the claim asserts, that is STALE.
Stay inside your lens: give FAITHFUL when your lens passes. But set needs_arbiter=true (with arbiter_reason) when you noticed a possible problem OUTSIDE your lens, when you could not verify what your lens requires, or when your own call is borderline. needs_arbiter=false requires arbiter_reason=null.
Verdicts: FAITHFUL / DRIFTED (the source says something subtly different -- quote both sides) / UNSUPPORTED (no support found; say where you looked) / STALE (was true, superseded). If not FAITHFUL, proposed_fix = the minimal wording fix (use the vault's ==unverified== marker rather than inventing support), else null.
primary_kind: "text" if everything your lens relied on was checked against extractable text; "image-or-chart" if any value you relied on was read off an image, scan, slide or chart; "absent" if no raw primary exists or you could not open it.
Evidence: verbatim quotes with file/line or page references.`

const arbiterPrompt = (c, lenses, mode) => {
  const panel = LENS_NAMES.map((name, i) => {
    const l = lenses[i]
    if (!l) return `--- ${name.toUpperCase()} READER: returned nothing ---`
    return `--- ${name.toUpperCase()} READER ---
verdict: ${l.verdict}; needs_arbiter: ${l.needs_arbiter}${l.arbiter_reason ? ` (${l.arbiter_reason})` : ''}; primary_kind: ${l.primary_kind}
evidence: ${String(l.evidence).slice(0, EVIDENCE_CAP)}
proposed_fix: ${l.proposed_fix === null ? '(none)' : l.proposed_fix}`
  }).join('\n')
  const brief = mode === 'spot'
    ? `SPOT-CHECK: all three readers below independently found this claim FAITHFUL on their lenses and nobody flagged it. You are auditing that unanimous clear. Look specifically for what all three could have missed together: a degree word or hedge changed in paraphrase, a characterization or comparison broader than the source, a definition applied to a period or arm it does not cover, a rank word a figure does not support, a contradiction with the cited source page's own characterization. Default to FAITHFUL only if you verified it yourself.`
    : `ESCALATION: the three-reader panel did not clear this claim unanimously, or its raw pointer is missing or an image, or a reader read a figure off a chart while the claim states numbers. Their analyses are EVIDENCE, NOT VOTES: one well-evidenced dissent beats two readers who did not look at that aspect. Decide, finding by finding, whether each reader's non-FAITHFUL verdict or flag is real.`
  return `Claim-audit ARBITER in a wiki vault (cwd = vault root). READ-ONLY -- edit nothing AND write nothing into the vault: every page render, extracted text file and scratch file you make goes in a system temp dir, never inside raw/ (it is the provenance store -- an agent-written file there becomes a pending ADDED file forever) and never under wiki/. Yours is the final verdict.
${claimBlock(c)}
${brief}
Re-read everything yourself: the citing page in full context, the cited sources page (follow its #anchor), and the raw primary (PDFs: pdftotext or python; figures: render to a temp dir (never inside raw/) and view). Judge ONLY whether the cited source supports what is asserted about it; whether the claim fits the vault's own positions or arguments is out of scope. Search the rest of the vault's wiki/sources and raw for support the readers missed before calling anything UNSUPPORTED.
To overturn a reader's non-FAITHFUL finding you must quote the primary passage that refutes it; if you cannot, the finding stands.
Return: final_verdict; certainty = "clear" when any careful reader of the primary would reach your verdict, "borderline" when careful readers could reasonably split (a defensible compression, a nuance the surrounding text already hedges, a word choice the source itself also uses loosely) -- borderline calls go to a human, so do not force them; severity = "material" if a reader acting on the claim would believe something the source does not support, "minor" if it is imprecise but not misleading, "none" when FAITHFUL; reasoning (address each reader by lens name, and say who was right where they differ); proposed_fix = ONE minimal wording fix resolving every finding you uphold (null when FAITHFUL); fix_ok = true only if that fix is safe to apply exactly as written -- no spliced quote, no wrong negative, no over-attribution, no status error, and it does not break the surrounding sentence; lens_findings_upheld = the lens names whose non-FAITHFUL verdict or flag you upheld (empty when none).
${panel}`
}

let arbiterFailures = 0
let arbiterSuccesses = 0
let abortReason = null

const arbiterOpts = (c, tag) => ({ label: `${tag}:${c.slug.slice(0, LABEL_LEN)}`, schema: ARBITER_SCHEMA, model: 'opus', effort: 'high' })

const results = await pipeline(
  queue.claims,
  async (c, _item, idx) => {
    // .catch is explicit: a rejecting lens must become a null (which escalates), never drop the claim.
    const lenses = await parallel(LENS_NAMES.map(lens => () =>
      agent(lensPrompt(c, lens), { label: `${lens}:${c.slug.slice(0, LABEL_LEN)}`, phase: 'Panel', schema: LENS_SCHEMA, model: 'sonnet' })
        .catch(() => null)
    ))
    return { c, idx, lenses }
  },
  async ({ c, idx, lenses }) => {
    const base = { claim: c, idx, lenses, arbiter: null, arbiter_error: null, escalation: [] }
    if (lenses.every(l => !l)) return { ...base, status: 'panel-failed' }
    const escalation = []
    LENS_NAMES.forEach((name, i) => {
      const l = lenses[i]
      if (!l) escalation.push(`${name}: no result`)
      else {
        if (l.verdict !== 'FAITHFUL') escalation.push(`${name}: ${l.verdict}`)
        if (l.needs_arbiter) escalation.push(`${name}: flag (${l.arbiter_reason || 'no reason given'})`)
        // Only an unreadable primary escalates by itself; "image-or-chart" is informational (a reader
        // reading a table off a rendered page escalated 8 of 10 claims in validation). Image primaries
        // are caught deterministically from the raw pointer below.
        if (l.primary_kind === 'absent') escalation.push(`${name}: primary absent`)
        // A figure read off a chart is exactly what the numbers lens exists for, so that one
        // combination still escalates when the claim states numbers. A chart read by the other
        // lenses is recorded (lens_primary_kind) but does not escalate: in validation the
        // any-lens-any-chart trigger sent 8 of 10 claims to Opus.
        else if (l.primary_kind === 'image-or-chart' && name === 'numbers' && /\d/.test(String(c.excerpt))) escalation.push('numbers: figure read off a chart')
      }
    })
    const raws = rawList(c.raw)
    if (!raws.length) escalation.push('script: no raw primary')
    else if (raws.some(x => IMAGE_RAW.test(x))) escalation.push('script: image primary')
    if (!escalation.length) return { ...base, status: 'panel-cleared' }

    // Once the arbiter stage is judged dead, in-flight items stop paying for more Opus reads.
    if (abortReason) return { ...base, escalation, status: 'arbiter-failed', arbiter_error: 'not attempted: arbiter stage judged dead' }
    let arbiterError = null
    const arbiter = await agent(arbiterPrompt(c, lenses, 'escalate'), { ...arbiterOpts(c, 'arbiter'), phase: 'Arbiter' })
      .catch(e => { arbiterError = errMsg(e); return null })
    if (!arbiter) {
      arbiterFailures += 1
      if (!arbiterError) arbiterError = 'agent returned null (skipped or budget)'
      if (arbiterFailures >= 2 && arbiterSuccesses === 0 && !abortReason) {
        abortReason = `${arbiterFailures} opus arbiters returned nothing and not one had succeeded (last error: ${arbiterError}) -- the arbiter stage looks dead.`
      }
      return { ...base, escalation, status: 'arbiter-failed', arbiter_error: arbiterError }
    }
    arbiterSuccesses += 1
    // A hit needs two readers on the SAME finding: the arbiter must uphold a lens that itself ruled
    // non-FAITHFUL or raised a flag. Drift the arbiter found that no upheld lens raised is a split.
    const upheld = new Set(arbiter.lens_findings_upheld || [])
    const corroborated = lenses.some((l, i) => l && upheld.has(LENS_NAMES[i]) && (l.verdict !== 'FAITHFUL' || l.needs_arbiter))
    // Borderline drift goes to a human: validation showed careful readers (two Fable passes included)
    // split on these, so neither auto-applying nor silently clearing them is honest.
    const status = arbiter.final_verdict === 'FAITHFUL' ? 'arbiter'
      : arbiter.certainty === 'borderline' ? 'borderline'
      : corroborated ? 'arbiter' : 'arbiter-only'
    return { ...base, escalation, arbiter, status }
  }
)
// Abort only when NOTHING came back from the arbiter stage; a late recovery keeps the run's rows.
if (abortReason && arbiterSuccesses === 0) throw new Error(`${abortReason} Aborting; re-run the workflow.`)
if (abortReason) log(`WARNING: ${abortReason} Later arbiters did succeed, so the run continues; escalated claims skipped after the trip are arbiter-failed (unconfirmed).`)

const done = results.filter(Boolean)
if (done.length !== queue.claims.length) log(`WARNING: ${queue.claims.length - done.length} claim(s) dropped by a stage error -- n under-reports`)

// Spot-check: a barrier is needed -- the pick is over ALL unanimous clears. Deterministic from the
// seed (no Math.random in workflows), so a replay spot-checks the same claim.
phase('Spot-check')
const clears = done.filter(r => r.status === 'panel-cleared').sort((a, b) => a.idx - b.idx)
const spot = []
if (SPOT_CHECKS > 0 && clears.length) {
  const h = [...String(queue.seed)].reduce((s, ch) => (s * 31 + ch.charCodeAt(0)) % 100003, 7)
  const picks = []
  for (let k = 0; k < Math.min(SPOT_CHECKS, clears.length); k++) picks.push(clears[(h + k) % clears.length])
  const uniq = [...new Set(picks)]
  const checked = await parallel(uniq.map(r => () =>
    agent(arbiterPrompt(r.claim, r.lenses, 'spot'), { ...arbiterOpts(r.claim, 'spot'), phase: 'Spot-check' })
      .then(a => ({ r, a }), e => ({ r, a: null, err: errMsg(e) }))
  ))
  for (const x of checked.filter(Boolean)) {
    const agree = x.a ? x.a.final_verdict === 'FAITHFUL' : null
    if (x.a) { x.r.arbiter = x.a; x.r.status = agree ? 'spot-check-agree' : 'spot-check-miss' }
    else log(`spot-check arbiter failed on ${x.r.claim.file}:${x.r.claim.line} -- the clear stands unaudited`)
    spot.push({ file: x.r.claim.file, line: x.r.claim.line, slug: x.r.claim.slug, agree, final_verdict: x.a ? x.a.final_verdict : null })
  }
} else if (SPOT_CHECKS > 0) log('spot-check skipped: no unanimous clears this run')

// -- telemetry begin
// Escalation telemetry (regression R70, observe-only: this records WHY claims escalated so several
// real quarterly audits can be compared before anyone tunes the gate -- it changes no gate).
// escalationKey maps each reason string pushed in the pipeline above onto a stable
// "<source>.<kind>" key; the free-text arbiter_reason inside a flag is deliberately dropped from
// the key (it stays verbatim in the row's escalation array). An unknown shape maps to "other"
// rather than throwing, and the test pins that every reason this file pushes has a real key.
// Self-contained and marker-delimited so scripts/tests/test_claim_audit_telemetry.mjs can eval it.
function escalationKey(reason) {
  const s = String(reason)
  let m
  if ((m = /^(wording|scope|numbers): no result$/.exec(s))) return `${m[1]}.no_result`
  if ((m = /^(wording|scope|numbers): flag \(/.exec(s))) return `${m[1]}.flag`
  if ((m = /^(wording|scope|numbers): primary absent$/.exec(s))) return `${m[1]}.absent`
  if (s === 'numbers: figure read off a chart') return 'numbers.chart'
  if ((m = /^(wording|scope|numbers): (DRIFTED|UNSUPPORTED|STALE)$/.exec(s))) return `${m[1]}.verdict`
  if (s === 'script: no raw primary') return 'script.no_raw'
  if (s === 'script: image primary') return 'script.image'
  return 'other'
}
// Counts ESCALATED CLAIMS per key (a key repeated inside one row counts once), so a count
// reads "k of the E escalated claims carried this reason". Ordered by count desc, then key.
function escalationReasons(rows) {
  const counts = {}
  for (const r of rows) {
    for (const k of new Set((r.escalation || []).map(escalationKey))) counts[k] = (counts[k] || 0) + 1
  }
  return Object.fromEntries(Object.entries(counts).sort((a, b) => (b[1] - a[1]) || (a[0] < b[0] ? -1 : 1)))
}
// The parenthesized part of the canonical log heading -- audit_claims.py next_steps() is the
// single source for the shape; this renders it so nobody hand-types the counts:
//   (seed, n, hits; split S, unconfirmed U; escalated E/N (reasons: k1=n1, k2=n2))
function logLine(t) {
  const reasons = Object.entries(t.escalation_reasons)
  const rs = reasons.length ? reasons.map(([k, v]) => `${k}=${v}`).join(', ') : 'none'
  return `(${t.seed}, ${t.n}, ${t.hits}; split ${t.split}, unconfirmed ${t.unconfirmed}; escalated ${t.escalated}/${t.n} (reasons: ${rs}))`
}
// The machine-readable per-run record the calling session persists with
// `python scripts/audit_claims.py --record-run <file>` into _meta/claim-audit-ledger.json's
// `runs` list. Facts only; per-row reasons kept verbatim so flag-vs-reflex can be judged later.
function runRecord(t, rows, spot) {
  return {
    kind: 'workflow', seed: t.seed, n: t.n, queued: t.queued, printed_n: t.printed_n,
    hits: t.hits, cleared: t.cleared, split: t.split, unconfirmed: t.unconfirmed, skipped_faithful: t.skipped_faithful,
    escalated: t.escalated, borderline: t.borderline, material: t.material, chart_read: t.chart_read,
    lens_disagreement: t.lens_disagreement, escalation_reasons: t.escalation_reasons,
    spot_check: (spot || []).map(s => ({ file: s.file, line: s.line, slug: s.slug, agree: s.agree })),
    rows: rows.map(r => ({ idx: r.idx, key: r.key, file: r.file, line: r.line, slug: r.slug, refute_status: r.refute_status, final_verdict: r.final_verdict, escalation: r.escalation })),
  }
}
// -- telemetry end

const HIT_OR_CLEAR = ['arbiter']
const SPLIT = ['arbiter-only', 'spot-check-miss', 'borderline']
const UNCONFIRMED = ['arbiter-failed', 'panel-failed']
const SKIPPED = ['panel-cleared', 'spot-check-agree']

const rows = done.sort((a, b) => a.idx - b.idx).map(r => {
  const a = r.arbiter
  const settledHit = HIT_OR_CLEAR.includes(r.status) && a && a.final_verdict !== 'FAITHFUL'
  return {
    idx: r.idx, key: r.claim.key || null, file: r.claim.file, line: r.claim.line, slug: r.claim.slug,
    refute_status: r.status,
    lens_verdicts: Object.fromEntries(LENS_NAMES.map((n, i) => [n, r.lenses[i] ? r.lenses[i].verdict : null])),
    lens_flags: Object.fromEntries(LENS_NAMES.map((n, i) => [n, r.lenses[i] && r.lenses[i].needs_arbiter ? (r.lenses[i].arbiter_reason || true) : null])),
    lens_primary_kind: Object.fromEntries(LENS_NAMES.map((n, i) => [n, r.lenses[i] ? r.lenses[i].primary_kind : null])),
    escalation: r.escalation,
    final_verdict: a ? a.final_verdict : (SKIPPED.includes(r.status) ? 'FAITHFUL' : 'UNCONFIRMED'),
    certainty: a ? a.certainty : null,
    severity: a ? a.severity : null,
    proposed_fix: settledHit ? a.proposed_fix : null,
    // Splits carry the arbiter's text for the human resolving them -- never fix-safe.
    arbiter_proposed_fix: SPLIT.includes(r.status) && a ? a.proposed_fix : null,
    // Fail closed: only an adjudicated two-reader hit can be fix-safe.
    fix_ok: settledHit ? a.fix_ok : false,
    lens_findings_upheld: a ? a.lens_findings_upheld : [],
    arbiter_reasoning: a ? a.reasoning : null,
    lens_evidence: Object.fromEntries(LENS_NAMES.map((n, i) => [n, r.lenses[i] ? String(r.lenses[i].evidence).slice(0, ROW_EVIDENCE_CAP) : null])),
    arbiter_error: r.arbiter_error || null,
  }
})

const hits = rows.filter(r => HIT_OR_CLEAR.includes(r.refute_status) && r.final_verdict !== 'FAITHFUL').length
const cleared = rows.filter(r => HIT_OR_CLEAR.includes(r.refute_status) && r.final_verdict === 'FAITHFUL').length
const split = rows.filter(r => SPLIT.includes(r.refute_status)).length
const unconfirmed = rows.filter(r => UNCONFIRMED.includes(r.refute_status)).length
const skipped_faithful = rows.filter(r => SKIPPED.includes(r.refute_status)).length
const escalated = rows.filter(r => r.escalation.length).length
const borderline = rows.filter(r => r.certainty === 'borderline').length
const chart_read = rows.filter(r => Object.values(r.lens_primary_kind).includes('image-or-chart')).length
const material = rows.filter(r => r.severity === 'material').length
const lens_disagreement = rows.filter(r => new Set(Object.values(r.lens_verdicts).filter(Boolean)).size > 1).length
if (hits + cleared + split + unconfirmed + skipped_faithful !== rows.length) log(`WARNING: row buckets do not sum to n (${hits} hits + ${cleared} cleared + ${split} split + ${unconfirmed} unconfirmed + ${skipped_faithful} skipped-faithful != ${rows.length}) -- trust the rows, not the counts`)
if (rows.length !== queue.claims.length || (queue.printed_n && rows.length !== queue.printed_n)) log(`WARNING: n=${rows.length} is short of the queue (${queue.claims.length} queued, printed_n ${queue.printed_n}) -- claims were lost; this is not a clean run`)
log(`${rows.length} read, ${escalated} escalated (${chart_read} with a chart read), ${hits} confirmed non-FAITHFUL (${material} material), ${cleared} cleared by the arbiter, ${split} split, ${unconfirmed} unconfirmed; spot-check ${spot.length ? spot.map(s => (s.agree === null ? 'failed' : s.agree ? 'agree' : 'MISS')).join(',') : 'none'}`)

const tally = { seed: queue.seed, n: rows.length, queued: queue.claims.length, printed_n: queue.printed_n, hits, cleared, split, unconfirmed, skipped_faithful, escalated, borderline, material, chart_read, lens_disagreement, escalation_reasons: escalationReasons(rows) }
const log_line = logLine(tally)
log(`log heading: ## [date] lint | claim audit ${log_line}`)

return {
  ...tally, spot_check: spot, coverage: queue.coverage, rows,
  log_line, run_record: runRecord(tally, rows, spot),
  followups: 'If n is less than queued or printed_n, claims were lost -- re-run before writing the log line; a lossy run is never a clean quarter. severity is advisory: it does not gate fix_ok. Apply fixes ONLY where fix_ok is true, and ONCE per file:line:slug (audit_claims.py can queue the same entry twice; rows carry idx). NEVER apply a split row (refute_status borderline: the arbiter judged the drift a call careful readers could split on -- decide by hand; arbiter-only: drift the Opus arbiter found that no upheld lens raised; spot-check-miss: the arbiter found drift in a claim all three readers cleared -- resolve by hand, and report the spot-check miss) or an unconfirmed row (arbiter-failed / panel-failed: re-run or judge by hand). `cleared` rows needed no fix -- the arbiter overturned the dissent; leave the page alone. Then sweep other pages citing the same source for the same drift, bump updated:, log `lint | claim audit (seed, n, hits; split S, unconfirmed U; escalated E/N (reasons: k1=n1, k2=n2))` -- paste log_line, which is that shape already filled in -- the 0-hits-for-3-quarters cadence stretch only applies when split and unconfirmed are 0 too -- then persist the escalation telemetry: write run_record (this object\'s field, verbatim JSON) to a file in a system temp dir and run `python scripts/audit_claims.py --record-run <that file>`, which appends it to _meta/claim-audit-ledger.json AND writes each adjudicated row\'s verdict into the ledger\'s judged-claims map, so the next non-quarterly run skips claims judged FAITHFUL this quarter (regression R52; UNCONFIRMED rows stay unjudged) (regression R70: collect several real audits before tuning escalation -- never loosen it to cut usage) -- and re-stamp Last run / Next due on the recurring-items page.',
}
