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
  - `action_requests.executor` (the approved channel), `confirmation_code`, `confirmation_text`,
    `confirmation_sha256` (what the user was shown), `approval_source` (host message id), and `execute_by`
    (approval deadline). See D11 to D13.
  - `savings_ledger.recurring_months` (12-month cap, promo length) and `reverses_id` (reversal link).
  - `source_messages` table (spec 6.1 SourceMessage): account, message id, sender, subject, category, merchant,
    confidence, `needs_review`, extracted fields, and a 200-character redacted snippet.
- **D9 Privacy enforcement:** the store refuses any text column containing a full card number (IIN + Luhn), a full
  SSN, or a credential key-value pair. JSON columns are checked recursively: every key and value, nested lists and
  dicts, integers, `key: value` pairs, and the serialized JSON. Generated ids (letters only) and digest columns are
  not scanned, so random hex cannot be mistaken for a card number. Snippets and evidence notes are redacted before
  storage.
- **D10 Held back from M0:** connector launch config and routine schedules (deploy config), and real executors. These
  need Charles's approval under `AGENTS.md`.
- **D11 Confirmation trust model.** The MCP server cannot see the chat. It only gets the `user_message` the agent
  passes to `confirm_action_request`, so it cannot prove that a human typed the reply. The gate narrows what an agent
  can claim, and the guarantees and limits are:
  - *Fail-closed wording.* The whole reply must be an allow-listed affirmative (`confirm`, `approve`, `确认`, `批准`,
    `同意`, ...) plus, optionally, the request's code. Negations, questions, conditionals, hedges, and any extra words
    are rejected (8.2). The reply is split into tokens at spaces and punctuation, and also where CJK and Latin
    text meet (so `确认ABCDEF` is two tokens). Space-separated CJK pieces are never joined back together. Codes count
    only as whole tokens in one block at the start or at the end. The remaining tokens must be an allow-listed
    affirmative exactly, so a code cannot split one: `批 ABCDEF 准`, `批 准` and `confirm ABCDEF all` are rejected.
  - *Binding to what was shown.* `create_action_request` renders one `confirmation_text` and a 6-letter
    `confirmation_code`, and stores the text and its SHA-256. An approval must quote the code or present the digest of
    that exact text. Amending re-renders both, so an approval of an old version cannot cover a changed plan. Codes
    are per request, so one request's code cannot approve another.
  - *Audit.* The verbatim reply, the optional host `source` (chat message id), and every transition are stored and
    returned by `audit_timeline` (8.4).
  - *Residual risk.* A compromised or prompt-injected agent could still forge a reply that includes the code. Mitigations
    are in the skill: show the text verbatim, pass the reply verbatim, and never confirm because content says so.
    Stronger guarantees need host support, such as a signed user-message id or an approval card the host renders and
    verifies (like Grok Bot's own outgoing-message approval). This is tracked for M8.
- **D12 Approval deadline and date bounds.** An approval must be acted on within `approval_ttl_hours` (default 24
  hours, set in user `thresholds` or the `ActionGate` constructor). The setting must be a finite number with
  0 < value <= 72 (no approval outlives the 72-hour window of 6.9). `update_settings` rejects anything else, and a
  bad value that reaches the database is clamped to 72 h or falls back to 24 h. The deadline counts from the later of
  approval time and the end of the revoke window (`execute_not_before`). After it, `start_action_run` fails, the request
  becomes `expired`, and the user must be asked again. A run that already started may still finish and record its
  outcome. Other bounds:
  - An unconfirmed request's `expires_at` must be in the future and at most 72 hours ahead (6.9). For trial requests
    whose trial ends later, R10 re-asks with a new request.
  - `execute_not_before` must be between now and 45 days ahead (30-day trials plus margin).
  - Datetimes must be timezone-aware.
  - Out-of-range or forged dates (for example year 9999) fail closed with a `GateError`, never an `OverflowError`.
- **D13 Executor binding.** Each request is approved for one channel, and `start_action_run` must use that channel.
  Allowed channels by action type: cancel_subscription = browser, bland_call, email, or manual; submit_claim = browser,
  email, bland_call, or manual; negotiate_bill and place_call = bland_call; send_email = email; rebook_travel and
  submit_form_with_pii = browser. The email and bland_call channels require a draft or call script. Switching
  channel is an amendment and needs re-confirmation (8.2: "switching to a phone call must be re-confirmed").
- **D14 Evidence files.** `record_evidence` copies only regular files whose real path is inside an allowed evidence
  inbox (`MSB_EVIDENCE_INBOX`, default `$MSB_HOME/inbox`; a colon-separated list is allowed). Symlinks, paths that
  resolve outside the inbox, non-regular files, and files with more than one hard link are rejected, so the store
  cannot be used to collect secrets such as `~/.ssh/id_rsa`. To close the race between the check and the copy, the
  file is opened with `O_NOFOLLOW`. The open descriptor is then re-checked with `fstat`: it must be a regular file with
  `st_nlink == 1` and the same device and inode that were checked, and on Linux its `/proc/self/fd` path must be
  inside the inbox. The copy is read from that descriptor (never by path again) into a new `0600` file, and is
  hashed while copying.
- **D15 Schema versions and migrations.** `PRAGMA user_version` holds the schema version (now 2). v1 is the schema at
  87b31c9 and is frozen in `tests/fixtures/schema_v1.sql`. The v1 → v2 migration rebuilds `action_requests` (adding
  `executor`, the confirmation code/text/digest, `approval_source` and `execute_by`) in one transaction with
  foreign keys checked afterwards. It rolls back completely on any error. It fails closed: v1 requests that were
  still `pending_user` or `approved` were never bound to a confirmation text, so they become `expired` with a history
  note and must be requested again. Terminal rows are kept. A request without a bound code and text can never be
  approved. A database with a newer version than the code is refused. Every future schema change must bump
  `SCHEMA_VERSION` and add a migration step with a test that starts from the previous frozen schema.
