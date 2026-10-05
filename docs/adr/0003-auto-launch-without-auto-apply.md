# An unattended maintenance pass launches the expensive audits, but never applies their output

## Status

Accepted, 2026-09-17. Implemented by `.claude/skills/wiki-maintenance/SKILL.md`
and gated by `scripts/maintenance_preflight.py` (regression R77).

Amended 2026-09-18 by ADR-0004 (regression R92), the "new ADR" the last paragraph
asks for: item 3's exception is widened to all of `anchor-backfill`'s anchor
writes -- its decisions tier (HIGH_REVIEW, REVIEW, SUPPORTED-UNSECTIONED) is
applied inside the run by `suggest_anchors.py --apply` and reconciled on disk by
the pass, not parked -- and item 2's "every content change ... is parked" is
correspondingly narrowed to every other workflow. Item 3's "HIGH and REVIEW
park" sentence below is superseded. Everything else here stands.

Amended 2026-09-18 by regression R81 -- **the pass has two gates, and they run in
this order:**

1. **Gate 1, may this file be edited at all.** `scripts/maintenance_preflight.py`
   resolves an **edit-deny set** (`edit_deny`) in code: `wiki/log.md` by path
   rule, plus any page under `wiki/` or `_meta/` carrying `append_only: true`.
   A finding whose target is in that set is parked with its count regardless of
   how well the fix is proven, and the set is reported on every run -- when
   nothing resolves, `edit_deny.note` says so rather than showing an empty
   section that reads as protection.
2. **Gate 2, is the fix provable.** The `machine-verifiable` contract, plus
   everything the decision below says about applying what a workflow returns.
   Unchanged by this amendment.

Build 1 shipped gate 2 only and stated gate 1 as prose, including a parenthetical
that licensed correcting a link's `../` depth in `log.md` on the grounds that a
depth fix is not a content change. It is not a content change -- and that is gate
2 reasoning, which gate 1 does not accept. The first real unattended run
(sample-vault-e, 2026-09-17) produced 166 such repairs, every one an exact span with a
unique replacement and a scanner-verifiable postcondition, and the ruling was to
leave them latent: `log.md` is the only durable record in a non-git Dropbox
vault. The parenthetical is deleted from
`.claude/skills/wiki-maintenance/SKILL.md`, so the prose no longer licenses what
the resolver denies.

Gate 1 precedes the build-2 **fixer allowlist** that regression R82 tracks; the
allowlist must not land before it, or it ships able to justify exactly that
sweep. Three pieces of regression R81 are deliberately NOT in this build: the allowlist
itself; suppression accounting against parked ledger keys ("N findings
suppressed by an explicit standing decision"), which has no findings pipeline to
hang on yet; and gate 1 inside `anchor-backfill`, the pass's one in-run mutation
path -- `suggest_anchors.py --apply` / `--apply-auto` does not consult
`edit_deny`, and `wiki/log.md` escapes that path only because
`audit_claims.SKIP_FILES` skips the name, not because gate 1 protects it, so a
page a vault marks `append_only: true` stays reachable by that workflow until regression R82
wires the set through. Until then the pass covers the gap in prose: it checks the
run's touched pages against `edit_deny.paths` during the on-disk reconciliation
ADR-0004 already requires. Gate 1 is advisory to the readiness gate -- it never
makes a vault `ready: false`; the pass, not preflight, is what refuses the edit.

## Context

`/wiki-maintenance` runs unattended by design -- its stated use case is "I will
be away from the keyboard for an hour." Two of the template's periodic passes are
expensive multi-agent workflows with their own trigger conditions:

- `claim-audit`, due on the `Last run / Next due` recurring-items stamp that
  `check_stale.py` section D watches.
- `anchor-backfill`, due when locator coverage falls below the target lint
  reports.

Neither fires on its own. Both depend on a human noticing a stamp or a
percentage, which is exactly the class of upkeep that does not happen. The
maintenance pass is the natural place to notice.

Two positions were argued before the decision:

**Against auto-launch.** An unattended run that can start a multi-agent workflow
can silently become dozens of agents and a much larger bill than the operator
expected, on a schedule the operator is not watching. `claim-audit`'s findings
need a human to apply in any case, so the launch buys queueing, not completion.

**For auto-launch.** The triggers are the product of deliberate design and they
already encode when the work is worth doing. A pass that detects a due audit and
declines to run it has converted an automation into a reminder, and reminders in
a set of maintained wikis are what the periodic passes exist to replace.

the owner chose auto-launch, over the reviewing session's recommendation.

A subsequent adversarial review (Codex, read-only, 2026-09-17) accepted the
launch but raised a distinct objection that neither position had separated:
`fix_ok: true` is a ruling by the `claim-audit` arbiter that a proposed piece of
prose is safe to apply, not a proof that the repository establishes it. See the
`fix_ok`, `split` and `borderline` definitions in `CONTEXT.md`. Applying such a
change unattended would let an adjudicated judgment become a wiki claim with no
reviewer -- which is precisely what the pass's own `machine-verifiable` boundary
forbids for every other kind of edit.

## Decision

**Launching is automatic; applying is not.**

1. When a script-evaluated trigger fires, `/wiki-maintenance` launches that
   workflow without asking.
2. Every content change those workflows propose is **parked** with its run ID and
   the arbiter's proposed wording. This includes rows carrying `fix_ok: true`.
   `split`, `unconfirmed` and `fix_ok: false` were already never applicable.
3. One exception: `anchor-backfill`'s **AUTO** bucket, where a script has proved
   that an existing explicit locator maps to exactly one validated anchor, and
   lint's broken-anchor count verifies the postcondition. That meets the
   `machine-verifiable` contract on its own terms. **HIGH** and **REVIEW** park.
4. Trigger evaluation is not prose. `scripts/maintenance_preflight.py` emits the
   trigger state as structured output, and the pass acts on that. A run whose
   preflight does not report `ready: true` -- including one where preflight
   crashed, timed out, or emitted output that would not parse -- launches nothing
   at all. The gate is the presence of a `true`, not the absence of a `false`.

   This clause governs the two *automatic* launches. It does not reach
   `--triage`, which is a flag a human passes on the command line and which no
   trigger fires; the skill bounds the unattended case there with a
   report-and-stop rule instead.

## Consequences

The operator gets the queueing they asked for: a due audit runs while they are
away and its results are waiting. They do not get unattended prose edits, so the
expensive pass ends in a decision list rather than in changed wiki content.

The cost exposure is real and accepted. A due `claim-audit` will spend without
asking. No budget ceiling is imposed, deliberately: a cap that neither party can
calibrate yet would either block legitimate work or be set high enough to protect
nothing. Revisit once several real runs establish what a normal run costs.

Two known gaps are deferred to a second build and are recorded here so they are
not rediscovered as surprises:

- **Relaunch after partial failure.** Section D flags a due item at every
  close-out until both stamp dates are rewritten. Until run state is persisted
  separately from the human-readable stamp, a timeout or a failed log write can
  let a later invocation relaunch the same expensive audit.
- **Overlap with `/ingest`.** That skill already runs `audit_claims.py --n 5` at
  every close-out, unconditionally, rather than on the quarterly stamp. A vault
  receiving both policies may pay twice or build overlapping queues. Measure the
  overlap on real runs before legislating it.

If the exception in item 3 ever needs to grow -- a second "safe" bucket, a second
workflow -- that is a new decision and a new ADR, not a widening of this one. The
value here is the line, and lines widen quietly.
