# Codex is a witness, opus the arbiter, in claim-audit refutes

## Status

Superseded, 2026-09-15, by [0002](0002-sonnet-lens-panel-opus-arbiter.md). Accepted earlier the same day. Its first live run showed the forwarder relayed 0 of 4 Codex outputs verbatim, and that the single judge upstream of it was the larger noise source (regression R62).

## Context

The `claim-audit` workflow can run its refute stage with a Codex read via `args.refuter: "codex"`.
The only way to reach Codex from a workflow is the `codex:codex-rescue` agent, and that agent is a
thin sonnet Bash forwarder: it is forbidden to read files, it shells out to the codex-companion
runtime, and it returns Codex's stdout verbatim (or nothing at all on failure). It cannot read the
vault, cannot check a citation, and cannot be given a structured-output schema without forcing it to
fabricate a verdict it did not compute.

The first live run (2026-09-14, regression R60) showed the failure concretely: asked in prose to wait
for Codex, the forwarder returned without waiting -- a "refuter verdict" that no Codex process had
produced. Nothing in the output distinguished that from a real read, and the workflow had no
machine-visible record of what Codex actually concluded, so a disagreement between Codex and the
opus refuter could only ever surface as prose buried in `refute_reasoning`.

## Decision

Codex is a **witness** and the opus refuter is the **arbiter**.

- The Codex read is gathered first and handed to the opus refuter as evidence to verify. Only the
  arbiter emits `final_verdict`.
- Codex is asked to lead its output with two exact lines, `VERDICT: <FAITHFUL|DRIFTED|UNSUPPORTED|
  STALE>` and `FIX_OK: <yes|no>`. The script parses only those header lines, anchored at the start of
  the trimmed output, into the row fields `codex_verdict` and `codex_fix_ok`. The format instruction
  is addressed to the investigator, not to the forwarder, so the pass-through has nothing to invent.
- The arbiter separately reports what the Codex block said in its own `codex_verdict` /
  `codex_fix_ok` fields. The script cross-checks the two. A mismatch means the arbiter misread the
  evidence it was given: `refute_status: codex-unparsed`, `fix_ok` false, unconfirmed, never applied.
  If Codex answered but without the headers, the arbiter's report is used and the row is marked
  `codex+opus (unheaded)` -- a softer, still-countable status -- but ONLY when the arbiter reports
  both `codex_verdict` and `codex_fix_ok`. If it reports neither, or only one, no witness position was
  recorded anywhere and the row degrades to `opus-only (codex unavailable)` rather than banking an
  agreement nothing wrote down.
- **Any disagreement is a split.** If the witness's verdict differs from the arbiter's, or they
  differ on whether the fix is safe, the row is `refute_status: split`, `fix_ok` is forced false, the
  row is excluded from `hits` and counted in a new `split` field. `codex+opus` now means the two
  readers agreed on both the verdict and the fix. `fix_ok` is true only when every reader that saw
  the fix said yes.
- **Codex unavailable is not a failure.** No answer, a background-queue receipt, or a thrown
  forwarder leaves the arbiter to refute alone: `refute_status: opus-only (codex unavailable)`, the
  row can still be a hit with `fix_ok` true. The count is returned as `codex_unavailable`, and the
  run logs a WARNING when it equals the number of rows a Codex read was actually attempted on. So
  `refuter: "codex"` degrades exactly to `refuter: "opus"` and is never worse than the default.

## Alternatives rejected

**A. Prose-only witness (the 2026-09-14 shape).** Hand Codex's output to the opus refuter and let the
refuter mention any disagreement in `reasoning`. Rejected: the disagreement is invisible in the
output. Nothing in `rows` says what Codex concluded, so a split reads as a clean `codex+opus` hit,
and a session skimming `fix_ok` applies a fix a second reader rejected. Prose in a reasoning field is
not a signal anything can count, filter, or gate on.

**C. Two independent refuters voting.** Treat the Codex read and the opus read as peer verdicts and
resolve by vote or majority. Rejected: the forwarder is not a peer reader. It cannot read the vault,
it cannot verify a citation, and it returns whatever stdout it got -- including nothing. Giving it a
vote equal to a reader that actually opened the files launders an unverified string into a verdict,
which is the exact defect this ADR exists to close.

## Consequences

- Codex's position is machine-visible in every row (`codex_verdict`, `codex_fix_ok`), so runs can be
  compared and disagreement rates tracked over time.
- `hits` gets stricter: splits no longer count. A `refuter: "codex"` run will usually report fewer
  hits than the same claims under `refuter: "opus"`, with the difference sitting in `split`. That is
  the point -- those rows needed a human, and previously got applied.
- The return object grows `split` and `codex_unavailable`; the calling session's log entry names
  both when nonzero, alongside `unconfirmed`.
- The header contract is a soft dependency on Codex's compliance. When Codex ignores it the run
  still works, one notch weaker, via `codex+opus (unheaded)` -- the arbiter's reading of the witness
  rather than the witness's own words.
- A cross-check mismatch is charged to the arbiter, not to Codex: the row becomes unconfirmed rather
  than being silently trusted. That trades a small number of usable rows for the guarantee that a
  `codex+opus` row means both readers genuinely agreed.
