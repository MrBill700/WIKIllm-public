# anchor-backfill's decisions tier is applied inside the run; the maintenance pass reconciles it on disk instead of parking it

## Status

Accepted, 2026-09-18 (regression R92; acceptance is the merge of the PR that
carries it). Widens ADR-0003 item 3's exception to all of one workflow's anchor
writes and narrows item 2 accordingly; every other clause of ADR-0003 stands.

## Context

ADR-0003 decided that an unattended `/wiki-maintenance` pass launches due
workflows but applies none of their output, with a single exception:
`anchor-backfill`'s AUTO bucket. It said in so many words that HIGH and REVIEW
park like everything else, and that any growth of the exception is a new ADR.

The shipped workflow does not do that. `.claude/workflows/anchor-backfill.js`
assembles its decisions JSON from the HIGH_REVIEW tier (agent-verified, plus
any AUTO entry the post-`--apply-auto` re-scan still lists), the REVIEW tier
(agent-decided per citing file) and the NOMATCH findings the drift check
returns as SUPPORTED-UNSECTIONED, then hands that JSON to
`suggest_anchors.py --apply` inside the run. That is the workflow's design
(its `meta.description` says "apply via decisions JSON"); regression R90
hardened exactly that step; and regression R86's acceptance test required those
writes to land. On the first real run after regression R90 (sample-vault-a, 2026-09-18, run
`wf_example`) the workflow wrote 42 decisions-tier anchors into 18 pages
before the calling session saw a result.

So the skill's rule and the workflow's behaviour contradicted each other, and
the pass had three readings to choose from: revert the run (undoing what regression R86
was waiting on), park one item per anchor (42 ledger lines for changes a script
had already proven exact), or reconcile on disk and record the run once. It
took the third, and nothing written down said it could.

The difference from the `claim-audit` case that motivated ADR-0003 is the
shape of the mutation, not the quality of the judgment. A `fix_ok: true`
claim-audit row is free prose that an arbiter ruled safe. An anchor decision
is a heading name that a guarded script either inserts into one wikilink on
one recorded line, or refuses:

- The only edit `--apply` can make is inserting `#<heading>` into the cited
  `[[sources/<slug>...]]` link on the recorded line; display text and `../`
  prefixes are preserved.
- It SKIPs any entry whose recorded line (the stripped line cut to 200
  characters) no longer matches the file's stripped line over the recorded
  length, `len(recorded)`, so a stale or retyped decision under-applies
  loudly. Mis-application needs two different lines at the same line number
  sharing an identical prefix of that length.
- `--check` proves every anchored source link in the claim population
  resolves (vault-wide, pre-existing breakage included), and lint's LOCATOR
  line gives a before/after count the pass can hold the run to. The delta is
  the run-specific evidence; the `--check` absolute is a floor.

The agents' judgment picks the heading; the script bounds what that judgment
can do to the page. That is the same contract the AUTO bucket met, with the
choice of heading moved from a scorer to an agent.

## Decision

1. `anchor-backfill`'s decisions tier -- HIGH_REVIEW (plus any AUTO entry the
   re-scan still lists), REVIEW, and SUPPORTED-UNSECTIONED NOMATCH findings --
   is applied by the workflow, inside the run, by `suggest_anchors.py
   --apply`. The maintenance pass does not park these and does not revert
   them.
2. The pass owes the run a disk reconciliation before it parks or closes out:
   the files newer than the pre-launch marker are only wiki pages named in
   `_meta/anchor-decisions-latest.json` (written by the apply agent) or in
   `_meta/anchor-auto-applied-latest.json` (written by the Scan step's
   `--apply-auto`, regression R94: an exact page set, replacing the
   `auto_applied` count bound this decision originally carried -- the
   re-scan overwrites the suggestions file, so that record is the only
   thing naming those pages), plus `_meta/anchor-suggestions.json`,
   `_meta/anchor-decisions-latest.json`,
   `_meta/anchor-auto-applied-latest.json` and
   `_meta/locator-coverage.json`; a record that is absent, older than the
   marker (by its own `generated` timestamp), or whose length disagrees
   with a parsed `auto_applied` makes the check UNRECONCILED rather than
   falling back to a bound -- but an unparsed `auto_applied: null` does
   not, because the record's own `applied` field answers on disk the
   question the count was standing in for;
   lint's covered count minus the pre-launch baseline equals `auto_applied`
   + `applied`; each non-SKIPped decision's current line is the recorded
   line with only `#<heading>` inserted, compared stripped and at the
   recorded line's length. Any file or line outside that shape is
   unapproved: reported and parked, as ADR-0003 already says.
3. The run is recorded as ONE ledger item with a stable key
   (`anchor-backfill-run:<run id>`): run ID, pages touched, coverage before
   and after, a pointer to the decisions JSON, "spot-check optional". A
   check that fails or cannot run (`applied: null`, lint unavailable, a
   count that does not add up) makes that item say UNRECONCILED and name the
   check; it is a finding, not grounds to revert.
4. Everything else the run proposes still parks with its own stable key:
   drift candidates, null decisions, other NOMATCH verdicts, and every SKIP.
5. This is the whole exception. It names one workflow and one script-guarded
   mutation. A second workflow, or a change to what `--apply` is allowed to
   write, is a new ADR.

## Consequences

An unattended pass can now change wiki pages beyond the AUTO bucket -- but only
link locators, only where a decide agent and a line-prefix guard agree, and
only with a postcondition a scanner can check. The operator gets locator
coverage that actually moves (sample-vault-a: 25% -> 89% in one run) instead of a
46-item decision list per pass.

What the operator gives up: a heading chosen by an agent can be the wrong
section of the right source page. `--check` proves the anchor exists, not that
it is the best one. The ledger item's "spot-check optional" is honest about
that. The drift check still catches the worse case (a claim the cited page does
not support at all) and parks it rather than anchoring it.

Known gap: a line carrying two source citations normally gets only its first
anchor per run, because the first insertion changes the line before the second
entry's prefix compare (regression R91; the exception is a line whose citations both
sit past the 200-character prefix). Until that is fixed, the pass parks the
SKIP and the next run picks it up.
