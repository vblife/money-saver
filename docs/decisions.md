# Decisions

## Open questions (spec 13): suggested defaults adopted

| # | Question | Decision (spec default) | Where it shows up | Blocks M0/M1? |
|---|---|---|---|---|
| Q1 | Rocket Money ending pay-for-results negotiation on 11/2 (unverified) | Verify through official announcement/help center first; never cite unverified claims in product copy | Marketing only | No |
| Q2 | Business model | Free beta for the MVP; ledger "verified" rules per spec 6.11 | `core/ledger.py` | No |
| Q3 | Must Bland calls disclose AI identity? | Yes, always disclose; if refused, switch to a three-way call or user self-call with a script | M1 call script, `skills/money-saver-core` | No (default is workable; Charles should confirm before the first real call) |
| Q4 | Allow pre-authorized "cancel before trial ends"? | Allowed, with a 2-hour revoke window (FR-1-9) | `execute_not_before` in `core/action_gate.py` | No |
| Q5 | May the bot actually cancel as negotiation leverage? | Not in the MVP; it may only say the user is considering cancelling | M5 | No |
| Q6 | Change module order? | Keep the chapter 12 order | `docs/plan.md` | No |
| Q7 | Notification channel | Grok Bot chat, plus optional copy to the user's own mailbox only | `notification_prefs.email_copy_to_self = false` | No |
| Q8 | Label processed Gmail threads? | Off by default, opt-in | `notification_prefs.gmail_labels = false` | No |
| Q9 | Call recording retention | 90 days; transcript summaries kept as evidence (deletable) | M1 evidence retention job | No |
| Q10 | Count points/miles/travel credit in "you saved $X"? | No; listed separately | `Ledger.month_summary`, `notifier.format_month_headline` | No |
| Q11 | Who maintains merchant_registry? Review runtime additions? | Developer Bot maintains the top 150; runtime additions are `unverified` and need an extra warning before execution | `verification` field; every M0 seed entry is `unverified` and has no phone number | No for M0. For M1, verified cancel URLs/playbooks need checking against official pages (see B2) |
| Q12 | Licensed agent for insurance quotes? | No; only point to official comparison/insurer sites, never fill licensed flows | M5 | No |

**Result:** none of the 12 questions blocks M0 or M1 with the defaults above. Q3 and Q11 matter most for M1 and
should be confirmed by Charles before any real call or live cancellation.

## Blockers for M1 acceptance (not in spec 13)
- **B1 - real labelled samples.** M1 recall/precision (>= 90% / >= 85%) is defined on human-labelled samples. The repo
  has only synthetic fixtures. Measuring it needs Charles to provide anonymized samples, or to approve a read-only
  labelling pass over his own Gmail that stores only labels and message ids (nothing committed).
- **B2 - live cancellation validation.** "Top-30 web cancel success >= 70%" cannot be measured without real accounts.
  Development uses fakes and recorded pages. A supervised live check needs Charles's explicit approval per merchant
  (it cancels real services) and is a risky change under `AGENTS.md`.
- **B3 - Bland usage.** Real calls cost money and contact merchants. M1 ships the call script and a mocked executor;
  the first real call needs Charles's approval.

## Engineering decisions (M0)
- **D1 Stack:** the spec says "Python + SQLite" and gives the 7.4 layout. Following vblife/workout-tracker, the MCP
  server is a stdlib-only JSON-RPC stdio server (`server/server.py`) and tests use pytest. The runtime needs no
  third-party packages.
- **D2 Config format:** spec 7.4 names `merchant_registry.yaml` and `gmail_queries.yaml`. They ship as `.json` to keep
  the runtime stdlib-only (no PyYAML). JSON is valid YAML 1.2, so YAML tooling can still read them. This can be revisited
  if PyYAML becomes a dependency.
- **D3 Layout:** spec 7.4 directories at the repo root (`config/`, `core/`, `modules/`, `executors/`, `routines/`,
  `reports/`, `tests/`), plus `server/`, `skills/`, `bot/`, `docs/` as in workout-tracker. `classifier.py` and
  `extractor.py` are separate files.
- **D4 Data location:** `MSB_HOME` defaults to `/workspace/money-saver` (spec 6). `MSB_DB` and `MSB_EVIDENCE_DIR`
  override it. Tests always use temporary directories.
- **D5 Execution model:** in Grok Bot the agent drives the Gmail connector, browser, and Bland itself. The server
  therefore plans scans and ingests results (it does not call Gmail), and gates execution: the agent must call
  `start_action_run` (which enforces approval) before acting and `finish_action_run` with evidence after.
- **D6 Classifier:** a deterministic rule-based classifier is the M0 baseline and the validation layer. LLM extraction
  plugs in through `validate_llm_extraction` (schema check, evidence spans that must appear in the source, one retry,
  then `confidence < 0.5`). Trial-ending reminder emails are classified as `trial_start` (the trial lifecycle category)
  because they carry the trial end date FR-1-7 needs.
- **D7 Fixtures:** spec 7.4 asks for at least 20 anonymized emails per category. M0 ships 300 synthetic, PII-free
  samples from `tests/fixtures/build_email_fixtures.py`, plus a separate handwritten holdout of 31 messages, all to
  `alex@example.com`. Real anonymized samples are tracked as B1.
- **D8 Data model additions** (fields the spec needs implicitly):
  - `action_requests.execute_not_before` (revoke window for pre-authorization) and `history` (audit trail of
    transitions, 8.4).
  - `savings_ledger.recurring_months` (12-month cap, promo length) and `reverses_id` (reversal link).
  - `source_messages` table (spec 6.1 SourceMessage): account, message id, sender, subject, category, merchant,
    confidence, `needs_review`, extracted fields, and a 200-character redacted snippet.
- **D9 Privacy enforcement:** the store refuses any text column containing a full card number (IIN + Luhn), a full
  SSN, or a credential key-value pair. Snippets and evidence notes are redacted before storage.
- **D10 Held back from M0:** connector launch config and routine schedules (deploy config), and real executors. These
  need Charles's approval under `AGENTS.md`.
