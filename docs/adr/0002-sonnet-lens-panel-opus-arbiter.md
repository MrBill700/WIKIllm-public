# Every sampled claim gets a three-lens Sonnet panel; Opus arbitrates dissent

## Status

Accepted, 2026-09-15. Supersedes [0001](0001-codex-witness-opus-arbiter.md).

## Context

ADR-0001 made an optional Codex read a witness for the opus refuter. The refuter only ever saw
claims a single opus judge had already called non-FAITHFUL. Its first live run (sample-vault-c,
2026-09-15, wf_example, regression R62) showed two problems.

1. **The single judge is the noise source.** The same seed and the same 10 claims were judged on
   2026-09-14 and on 2026-09-15. The opus judges disagreed on 3 of 10. A claim one judge calls
   FAITHFUL is never challenged, and that was 6 of 10 claims in each run. More refute votes cannot
   fix a miss that never reaches the refute stage.
2. **The Codex channel is lossy.** The `codex:codex-rescue` forwarder relayed 0 of 4 Codex outputs
   verbatim (checked against the `~/.codex/sessions` rollouts). It paraphrased them, rewrote 3 of 4
   prompts, and once reduced a ~600-word answer with a replacement text to "given above". The header
   agreement ADR-0001 relied on was the forwarder's transcription, not Codex's stdout.
   `shellSafe`'s 600-char cap also truncated a proposed fix that Codex then rejected as unauditable.
   Codex's reads were good; the integration was not.

## Decision

- **Panel.** Every sampled claim gets three independent Sonnet reads, each with ONE lens:
  - `wording` -- quotation and paraphrase fidelity: degree words, modals, hedges, splices.
  - `scope` -- clause-to-citation mapping, over/under-characterization, wrong arm or author,
    contradiction with the source page's own characterization, currency (STALE).
  - `numbers` -- figures, dates, units, periods, rank words, and definitions applied across periods.
  Every lens returns a verdict plus a required `needs_arbiter` flag with a reason, used for
  out-of-lens suspicions or unverifiable lens checks, and a `primary_kind`.
- **Not a vote.** A claim is cleared without Opus only when all three lenses say FAITHFUL, none
  sets `needs_arbiter`, every lens could open the primary, and the script sees a raw primary that
  is not an image. (A lens reporting it read a chart does not escalate by itself: in validation
  that trigger sent 8 of 10 claims to Opus.) Anything else escalates to one Opus arbiter at high effort. The arbiter
  re-reads everything with all three analyses as evidence. A 2-1 majority never suppresses a
  dissent.
- **A hit needs two readers on the same finding.** An escalated claim the arbiter rules
  non-FAITHFUL is a hit only if the arbiter upholds (`lens_findings_upheld`) a lens that itself
  ruled non-FAITHFUL or raised a flag. Upheld flags count, so a scope-level problem a reader flagged
  as outside its lens can still become a hit. Drift the arbiter found that no upheld lens raised is
  a `split`: never auto-applied, resolved by hand.
- **Borderline goes to a human.** The arbiter also rules `certainty` (clear / borderline) and
  `severity` (material / minor / none). Non-FAITHFUL + borderline is a `split` (`refute_status:
  borderline`), never a hit. Validation reason: a blind Fable adjudication found all three
  contested benchmark labels borderline, and two Fable passes disagreed with each other on one of
  them. Forcing borderline calls either way is noise, not signal. To overturn a lens finding, the
  arbiter must quote the refuting primary passage.
- **Spot-check.** One unanimously cleared claim per run (deterministic pick from the seed) also
  goes to the arbiter with a find-what-all-three-missed brief. Agreement is logged. A miss becomes
  a `split` row and is reported as `spot_check.agree: false`, so a shared panel blind spot is a
  measured rate, not a silent clean result.
- **Codex leaves the routine audit.** It stays available for code review and ad-hoc second
  opinions. `args.refuter` is removed.
- **Replay inputs.** `args.claims` supplies a fixed queue instead of running `audit_claims.py`.
  `args.root` / `args.raw_root` point readers at a snapshot tree. Together they let a variant be
  benchmarked on a labeled sample.

- **Scope boundary (the owner, 2026-09-15).** The audit judges only whether the cited source supports
  what is asserted about it. Whether a claim coheres with the vault's own positions and plans is out
  of scope -- an argument question for a human. Validation had shown every reader and every arbiter
  (3 of 3 replicates) declining to flag a claim whose defect was internal contradiction with the
  register's own phase-bounded logic; that is now the specified behaviour, not a miss. The
  source-side half of such a claim -- does the source support the characterization drawn from it --
  is still audited.

## Alternatives rejected

**A. Keep ADR-0001 and fix the forwarder** (capture Codex stdout out-of-band). Rejected for the
routine audit: more integration to maintain, and it still leaves single-judge FAITHFUL calls
unchallenged.

**B. Five identical Sonnet refuters.** Rejected: same-model copies share blind spots, and they only
see claims the single judge flagged.

**C. An Opus read on every claim in addition to the panel.** Rejected for routine runs on cost. It
was used once as the benchmark during validation. Two Opus judges on the same sample already
disagreed on 3 of 10, so a single Opus read is not ground truth either.

## Consequences

- Around 3n Sonnet agents plus one Opus per escalated claim plus one spot-check per run. The
  `refute_status` vocabulary changes (see `.claude/workflows/README.md`). The bucket partition
  `hits + cleared + split + unconfirmed + skipped_faithful == n` still holds.
- `skipped_faithful` now means cleared by a unanimous panel, not by one judge.
- An image or absent raw POINTER always escalates, as does the `numbers` lens reading a figure off a
  chart on a claim that states numbers, so vaults heavy in scans pay more Opus. A chart read by any
  lens is recorded per row (`lens_primary_kind`) and counted (`chart_read`) whether or not it
  escalated. The run also returns `borderline` and `material` counts, and `queued` / `printed_n`
  beside `n` so a run that lost claims cannot be logged as a clean quarter.
- Validation evidence lives on the PR that landed this ADR.
