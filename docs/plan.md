# Build plan: M0 and M1

Source: `docs/spec.md` chapter 12 (milestones), with the defaults in `docs/decisions.md`.

## M0 - Foundation (about 1 week)

**Scope (spec 12, 5.0, 6, 7.4, 8)**
- Data store for the chapter 6 model: SQLite, integer cents, UTC ISO times, `dedupe_key` uniqueness (`core/store.py`).
- Gmail scanner: query templates (`config/gmail_queries.json`), 13-month first scan, incremental cursor, idempotent
  ingest, no full bodies stored (`core/gmail_scanner.py`).
- Classification and extraction framework: rule-based classifier over the 15 FR-0-2 categories, regex extraction with
  `evidence_span` per field, and a validator for LLM extraction output (7.6) (`core/classifier.py`, `core/extractor.py`).
- `merchant_registry` v1 with 50 merchants plus 4 billing aggregators, and the resolver with alias, domain, and
  aggregator split (FR-1-2) (`config/merchant_registry.json`, `core/merchant_resolver.py`).
- Confirmation gate: ActionRequest/ActionRun state machine, explicit-confirmation check, batch confirm, revoke window,
  re-confirmation on material change, expiry, purchase disabled, audit timeline (`core/action_gate.py`,
  `executors/base.py`).
- Evidence store: files with SHA-256, emails by reference (`core/evidence.py`).
- Savings ledger: pending/estimated -> verified with reference, immutable verified amounts, reversals, monthly math
  (12-month cap, promo months, travel credit and points kept separate) (`core/ledger.py`).
- Notification formatting: confirmation cards (Appendix A), batch list, monthly headline (`core/notifier.py`).
- Privacy guards: refuse to store full card numbers, SSNs, or credentials; masking (`core/privacy.py`).
- MCP server (stdlib JSON-RPC over stdio, same as workout-tracker) exposing the above (`server/server.py`).
- `skills/money-saver-core` (English), `bot/instructions.md` draft, `AGENTS.md`.
- Sample library: 300 synthetic, PII-free emails (20 per category) plus a 31-message handwritten holdout.

**Acceptance criteria (spec 12)**
| Criterion | How it is checked |
|---|---|
| Classification accuracy >= 90% on anonymized samples | `tests/test_classifier.py` (sample set and holdout, plus >= 80% per category) |
| ActionRequest state machine unit tests all pass | `tests/test_action_gate.py` |
| Unconfirmed actions cannot execute (negative tests) | `tests/test_action_gate.py`, `tests/test_server.py` (pending, vague reply, rejected, expired, revoked, amended, purchase, forged `approved` row, revoke window) |

**Not in M0:** routines/scheduling, real executors (browser, Bland, email), module logic, reports. Deploy config
(connector launch file, routine provisioning) was held back because it needs Charles's approval under `AGENTS.md`.
Charles approved enabling R1, R3, R4, and R10 by default on 2026-10-22 (M1b, D31).

## M1 - Subscriptions and free trials, module 1 (about 2 weeks)

**Scope (spec 12, 5.1)** - suggested PR split:
1. **M1a Subscription inventory** (done in `modules/subscriptions.py`; see D16-D20): build `Subscription` + `Charge` from indexed messages; aggregator split; period
   inference (FR-1-3); `next_charge_date` (FR-1-4); last-used signal labelled "inferred" (FR-1-5); price-increase
   detection (FR-1-16); conversational list ("what subscriptions do I have?").
2. **M1b Duplicates and trials** (done in `modules/duplicates.py`, `modules/trials.py`, and `routines/`; see D21-D25): duplicate rules a/b/c (FR-1-6); trial tracking with inferred lengths flagged low
   confidence (FR-1-7); `Reminder` rows at T-2 and T-1 at 09:00 local (FR-1-8); pre-authorized auto-cancel with the
   2-hour revoke window (FR-1-9, already supported by the gate); routines R1, R3, R4, R10 as code, enabled by default
   (`routines/schedule.py`, approved by Charles 2026-10-22, D31).
3. **M1c Card statement upload** (done in `modules/statements.py`; see D26-D29): PDF (`pdftotext`) and CSV parsing, keep only likely recurring lines, delete the
   original by default (FR-0-8, PV-3).
4. **M1d Cancellation execution:** browser playbooks for the top 30 merchants (FR-1-10), Bland cancel call script under the
   Q3 honesty policy (FR-1-11; Q3 resolved 2026-10-22, D30: disclose up front that the caller is an AI (locked on),
   truthful if asked, never claims to be human; post-call transcript audits for these rules), email-cancel drafts handed to the user's approval flow (FR-1-12), evidence and 48-hour
   confirmation watch -> `cancel_unconfirmed` (FR-1-13), manual fallback guide (FR-1-14), post-charge check
   (FR-1-15), ledger entries per the 5.1 savings rules. All executors subclass `GuardedExecutor`; tests use fakes.
   Split into stacked PRs (D32-D39):
   - **M1d-1 execution core:** live switches in `config/execution.json` (all off), `LiveGuard` in
     `begin_execution`, the first-live-cancel flag per merchant, Charles's first-paid-call approval, reviewed-only
     targets, Q11 proposals (`merchant_method_proposals`), and the `plan_cancellation`, `dry_run_action_request`,
     `manual_cancel_guide` (FR-1-14), and `execution_status` tools.
   - **M1d-2 executors:** the browser playbook runner with a fake driver and recorded pages (FR-1-10), the Bland
     adapter with a fake client, call limits, and the transcript audit (FR-1-11), and the email handoff (FR-1-12).
   - **M1d-3 after the run:** subscription status, ledger entry `cancel:<sub_id>`, the 48-hour confirmation watch
     (FR-1-13), and the post-charge check with a refund request (FR-1-15). These are tools only; there are no new
     routines.

**Acceptance criteria (spec 12)**
| Criterion | How it will be checked |
|---|---|
| Recall >= 90% / precision >= 85% for subscription detection | Labelled sample set (needs real anonymized samples; see decisions B1) |
| Top-30 merchant web cancel success >= 70% | Playbook dry runs against recorded pages in CI, plus a supervised live check that needs Charles's approval (B2) |
| Trial reminders fire on time | Unit tests with a fake clock: T-2/T-1 at 09:00 local, quiet hours, revoke window |
| Cancellation evidence complete | Tests assert screenshot + confirmation number or email + ledger entry per successful run |
