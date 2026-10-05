> Optional integration: these files require a host exposing the Workflow API and its agent/tool bindings. They are not standalone Node.js programs. The Python scanners work independently; this repository does not bundle a Workflow host. See SETUP.md.

# .claude/workflows/ -- named multi-agent workflows (template-synced)

Reusable Workflow-tool scripts for jobs that need many independent readers or adversarial
judgment -- the layer ABOVE the `scripts/` scanners. The scanners stay the cheap per-session
close-out check; these swarms are for backlogs and the quarterly audit. They cost real tokens
(~1M-equivalents for a full run of both on a 120-claim vault, 2026-08-08 baseline), so run
them deliberately, not per-session.

Both are **vault-relative**: run them from a session whose working directory is the vault
root. They are read-only except through the vault's own scripts (`suggest_anchors.py
--apply`) or a single JSON the script consumes -- agents never hand-edit wiki pages.

| Workflow | Invoke | What it does |
|---|---|---|
| `anchor-backfill` | `Workflow({name: "anchor-backfill"})` | Applies the AUTO anchor tier, agent-verifies HIGH_REVIEW, fans out per-citing-file sonnet agents to decide the REVIEW tier, opus drift-checks NOMATCH (never forces an anchor), applies everything in one `--apply` pass, self-checks. Returns drift candidates for the session to fix. Result fields: `auto_applied` (the AUTO tier written during Scan by `--apply-auto`), `auto_record` (path to `_meta/anchor-auto-applied-latest.json`: the pages and lines that AUTO tier actually anchored -- the re-scan one command later drops them from the suggestions file, so read this off disk rather than inferring them from the count; `entries: []` = the tier wrote nothing, an ABSENT file = the vault's `suggest_anchors.py` predates regression R94, sync it), `submitted` (decisions sent to `--apply`) vs `applied` (anchors the decisions pass wrote, parsed from suggest_anchors.py's own `=== APPLIED ===` line; AUTO is not in it; `null` = the line never printed, verify on disk -- regression R86); `apply_status` names a submitted-vs-applied gap; `check` is the vault-wide `--check` count (pre-existing anchors included); `coverage_after`, `apply_report` (raw command output). Disk-touching agents are told the decisions JSON is the workflow's own output and are forbidden to write log.md / the ledger or run a close-out. |
| `claim-audit` | `Workflow({name: "claim-audit", args: {n: 10}})` | Draws the risk-weighted sample via `audit_claims.py`, then reads EVERY sampled claim with a three-lens Sonnet panel -- `wording`, `scope`, `numbers`, one independent reader each, primary-first. Any dissent, a `needs_arbiter` flag, an image/absent raw pointer, a reader that could not open the primary, or the `numbers` lens reading a figure off a chart on a claim that states numbers escalates it to one Opus ARBITER at high effort, which re-reads everything with the three analyses as evidence (not votes), writes the one proposed fix and rules `fix_ok`. One unanimous clear per run is spot-checked by the arbiter (`args.spot_check`, default 1). **Queue control:** `args.seed` (a bare token such as `"2026-Q4"`) overrides `audit_claims.py`'s default current-quarter seed. In the default `risk` mode the seed does two things: it draws the seeded-random remainder (`args.n` minus `ceil(args.n * 0.7)`), **and** it breaks ties inside the risk ranking, because the population is shuffled under the seed before a *stable* sort by risk score -- the script's own comment says "ties broken by the seed so it is stable within a quarter". So whether a queue moves under a new seed depends on whether the claims at the rank cut are TIED, not on `args.n` alone. Two measurements, offered as observations rather than a rule: on sample-vault-a (65 claims, 2026-09-18) `--n 10` changed 5 of 10 entries while `--n 3` returned an identical queue; on a 10-claim fixture whose claims are all tied at `risk=3` (2026-09-19), `--n 1` -- where `ceil(1 * 0.7) = 1` puts the whole sample inside the ranking -- returned three distinct claims across five seeds. A seed override is therefore a lever, not a fix for the re-served same-quarter queue; the fix is the judged-claims ledger (regression R52, `scripts/README.md`): this workflow is the quarterly deep audit and passes `--include-judged` by default, so it re-reads claims already judged FAITHFUL this quarter; `args.include_judged: false` (a boolean -- anything else throws) opts an ad-hoc run into the skip, so it samples only claims not yet judged FAITHFUL this quarter. To put a specific claim in the sample deliberately, use `args.include`. `args.include` is an ARRAY of `"FILE:LINE"` specs force-included in the sample -- forced entries are prepended, so printed `n=` legitimately EXCEEDS `args.n`, and `n` in the result counts what was actually queued. Both are validated before they reach the shell (seed: `[A-Za-z0-9._-]`; include: repo-relative path, `:`, line number) and an unsafe value throws rather than silently serving a different queue -- as does an `args.include` that is present but is not an array of non-empty strings, since dropping the force-includes would run the DEFAULT queue while looking like a normal audit. With neither, the command is `python scripts/audit_claims.py --n <n> --include-judged` (the ledger default below); with `args.include_judged: false` as well it is byte-identical to the pre-ledger (pre-R52) `python scripts/audit_claims.py --n <n>`. Replay a fixed queue with `args.claims` against a snapshot tree with `args.root` / `args.raw_root`. Returns `n`, `queued`, `printed_n`, `hits`, `cleared`, `split`, `unconfirmed`, `skipped_faithful`, `escalated`, `borderline`, `material`, `chart_read`, `lens_disagreement`, `escalation_reasons` (escalated-claim count per stable reason key such as `wording.flag`, `scope.verdict`, `numbers.chart`, `script.no_raw` -- regression R70), `log_line` (the canonical log parenthesis, filled in), `run_record` (the per-run telemetry record for `audit_claims.py --record-run`), `spot_check` and a per-row `refute_status` (table below) with `lens_verdicts`, `lens_flags`, `lens_primary_kind`, `escalation` reasons, `certainty`, `severity` and (on splits) `arbiter_proposed_fix`. **`n` < `queued` or `printed_n` means claims were lost -- re-run; a lossy run is never a clean quarter.** |

`claim-audit` `refute_status`, one row per sampled claim:

| `refute_status` | Meaning |
|---|---|
| `panel-cleared` | All three lenses FAITHFUL, no flag, text primary. No Opus read. Counted in `skipped_faithful`. |
| `spot-check-agree` | A unanimous clear the spot-check arbiter also ruled FAITHFUL. Counted in `skipped_faithful`. |
| `arbiter` | Escalated and adjudicated. A hit when `final_verdict` is non-FAITHFUL AND the arbiter upheld (`lens_findings_upheld`) a lens that itself ruled non-FAITHFUL or raised a flag -- two readers on the same finding. Only these can carry `fix_ok: true`. Counts in `cleared` when the arbiter ruled FAITHFUL. |
| `arbiter-only` | The arbiter ruled non-FAITHFUL but upheld no lens that raised the finding -- drift escalated only by an image/absent primary, a missing lens result, or a different finding than the lenses raised. Drift resting on one reader: `fix_ok` false, counted in `split`, never auto-applied; `arbiter_proposed_fix` carries its text for the human. |
| `borderline` | The arbiter ruled non-FAITHFUL with `certainty: borderline` -- a call careful readers could reasonably split on. Counted in `split`, `fix_ok` false, `arbiter_proposed_fix` carries its text; decide by hand. (A borderline FAITHFUL stays `arbiter`/`cleared` but carries `certainty: borderline`; the run returns a `borderline` count over both.) |
| `spot-check-miss` | The spot-check arbiter found drift in a claim all three lenses cleared. Counted in `split`, reported as `spot_check[].agree: false` -- a measured panel blind spot. Resolve by hand. |
| `arbiter-failed` | Escalated, but the arbiter returned nothing (see `arbiter_error`). Fails closed: counted in `unconfirmed`. Two of these before any arbiter has succeeded marks the stage dead: remaining escalations are not attempted, and the run throws at the end only if no arbiter ever succeeded -- re-run it. |
| `panel-failed` | All three lens readers returned nothing. Counted in `unconfirmed`. (One or two missing readers escalate instead.) |

The five buckets partition the run -- `hits + cleared + split + unconfirmed + skipped_faithful == n`,
asserted in script (a mismatch logs a WARNING with the numbers). A spot-check whose arbiter fails
leaves the row `panel-cleared` with `spot_check[].agree: null` (logged). The field keeps its
historical name `refute_status` for log/tooling compatibility; there is no separate refuter any more.
Rows carry `idx`: `audit_claims.py` can queue one file:line:slug twice, so apply a fix once per entry.

Division of labor the scripts assume: **sonnet** for mechanical read-and-decide fan-out and for the
lens panel, **opus** for arbitration (high effort). The panel is not a vote: every reader answers
a different question, and a single well-evidenced dissent escalates rather than being outvoted.
Rationale: `docs/adr/0002-sonnet-lens-panel-opus-arbiter.md` (supersedes 0001, the Codex witness),
template repo only -- the ADRs are not in the sync set and do not reach vaults. Codex is not part
of the routine audit; use it for code review and ad-hoc second opinions.

**The calling session still does the close-out**: apply fixes (only where `fix_ok` is true;
`split` rows -- `borderline` (a call careful readers could split on) / `arbiter-only` / `spot-check-miss`, drift resting on one reader or on a borderline judgement -- are
resolved by hand and NEVER auto-applied, and `arbiter-failed` / `panel-failed` rows are
unconfirmed and go in the log's `unconfirmed` count, also never applied), sweep sibling pages
citing the same source, bump `updated:`, write the log entry as
`lint | claim audit (seed, n, hits; split S, unconfirmed U; escalated E/N (reasons: k1=n1, k2=n2))` -- the returned
`log_line` is that parenthesis filled in -- persist the escalation telemetry by writing the returned
`run_record` to a temp file and running `python scripts/audit_claims.py --record-run <file>` (appends it to
`_meta/claim-audit-ledger.json`'s `runs`, regression R70, and writes each adjudicated row's verdict -- keyed by the
queue's `id=` token, carried as `key` on each row -- into its judged-claims map, regression R52), re-stamp `Last run / Next due`, run
the scanners. The workflows return a `followups` field restating this. The cadence rule printed by
`audit_claims.py` NEXT STEPS (0 hits x 3 quarters -> stretch to semiannual) only applies when
`split` and `unconfirmed` were 0 in all three quarters -- a run with disagreed or unconfirmed rows
is not a clean run.

Origin: built in one wiki, where the pair took locator coverage 2% -> 95%
and the audit went 5-hits-in-10 on the never-anchored claims after two 0-hit runs on the
checkable set -- the drift hides in whatever has never been cheap to verify.

Synced from the WIKIllm template like `scripts/` -- improve there, then re-sync. Instance-
specific workflows are welcome beside these; the sync only touches files in its SYNC_SET.
