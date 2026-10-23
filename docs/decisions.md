# Decisions

## Open questions (spec 13): suggested defaults adopted

| # | Question | Decision (spec default) | Where it shows up | Blocks M0/M1? |
|---|---|---|---|---|
| Q1 | Rocket Money ending pay-for-results negotiation on 11/2 (unverified) | Verify through official announcement/help center first; never cite unverified claims in product copy | Marketing only | No |
| Q2 | Business model | Free beta for the MVP; ledger "verified" rules per spec 6.11 | `core/ledger.py` | No |
| Q3 | Must Bland calls disclose AI identity? | **Resolved by Charles, 2026-10-22 (D30): disclose up front.** Every call opens by saying the caller is an AI assistant calling on behalf of the user (locked on). If asked, the caller answers truthfully and never claims to be human. This supersedes his earlier choice the same day (no proactive disclosure) | `config/call_scripts/policy.json`, `core/call_policy.py`, `skills/money-saver-core` | No |
| Q4 | Allow pre-authorized "cancel before trial ends"? | Allowed, with a 2-hour revoke window (FR-1-9) | `execute_not_before` in `core/action_gate.py` | No |
| Q5 | May the bot actually cancel as negotiation leverage? | Not in the MVP; it may only say the user is considering cancelling | M5 | No |
| Q6 | Change module order? | Keep the chapter 12 order | `docs/plan.md` | No |
| Q7 | Notification channel | Grok Bot chat, plus optional copy to the user's own mailbox only | `notification_prefs.email_copy_to_self = false` | No |
| Q8 | Label processed Gmail threads? | Off by default, opt-in | `notification_prefs.gmail_labels = false` | No |
| Q9 | Call recording retention | 90 days; transcript summaries kept as evidence (deletable) | M1 evidence retention job | No |
| Q10 | Count points/miles/travel credit in "you saved $X"? | No; listed separately | `Ledger.month_summary`, `notifier.format_month_headline` | No |
| Q11 | Who maintains merchant_registry? Review runtime additions? | Developer Bot maintains the top 150; runtime additions are `unverified` and need an extra warning before execution | `verification` field; every M0 seed entry is `unverified` and has no phone number | No for M0. For M1, verified cancel URLs/playbooks need checking against official pages (see B2) |
| Q12 | Licensed agent for insurance quotes? | No; only point to official comparison/insurer sites, never fill licensed flows | M5 | No |

**Result:** none of the 12 questions blocks M0 or M1 with the defaults above. Q3 was resolved by Charles on
2026-10-22 (D30): calls disclose up front that the caller is an AI. Q11 is still open and must be confirmed by
Charles before any live cancellation.

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
  need Charles's approval under `AGENTS.md`. Charles approved the R1, R3, R4, and R10 schedules on 2026-10-22 (D31).
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
- **D15 Schema defined directly, no migrations (Charles, 2026-10-22).** In Charles's words: "Don't worry about
  migration, this is still in dev and no one is consuming it, just update database schema directly in all PRs."
  `core/store.py` only creates the current schema (`DDL`, all `CREATE TABLE/INDEX IF NOT EXISTS`) when it opens a
  file. There is no `PRAGMA user_version` stamping, no schema version number, no v1 → v2 migration, and no
  detection of unversioned files. This replaces the earlier versioned design (frozen v1 schema, fail-closed v1 → v2
  rebuild of `action_requests`, refusing newer or unrecognized files), which was removed together with its tests.
  - A schema change edits `DDL` directly in the PR that needs it. It is not a migration, needs no migration code or
    version bump, and is not flagged `[RISKY: DB migration]`.
  - `CREATE TABLE IF NOT EXISTS` does not alter an existing table, so a database made by older code can be missing
    new columns. Only a test or development database (a temporary directory, or a local file set with `MSB_DB` or
    `MSB_HOME`) may be deleted and rebuilt. The real `msb.db` in the bot's persistent directory (default
    `$MSB_HOME/data/msb.db`) is a production data path under `AGENTS.md`. Deleting, rebuilding, or resetting it
    needs Charles's explicit approval, even in dev. No code deletes the database file: the store only creates
    missing tables, and `delete_all_data` (PV-8) deletes rows only when the user types the confirmation phrase.
  - Gate safety does not depend on migrations: a request without a bound confirmation code and text can never be
    approved (`tests/test_action_gate.py`).
  - This holds while money-saver is in development with no consumers. Migrations come back only by a new decision
    from Charles.

## Engineering decisions (M1a: subscription inventory)
- **D16 Inventory source and identity.** `modules/subscriptions.py` builds `subscriptions` and `charges` only from
  indexed `source_messages` (structured fields with evidence spans, never full bodies). One subscription is one
  `(merchant_id, account_email, billing_channel)`. Statement charges (M1c) attach through `charges.subscription_id`.
  Rebuilding is idempotent. A row is rewritten only when a derived field changes. Statuses owned by the user or the
  cancellation flow (`ignored`, `paused`, `cancel_*`, `cancelled`, `charged_after_cancel`) are never overwritten.
  M1a adds no schema change.
- **D17 Billing channel.** The receipt's sender decides the channel: Apple → `apple`, Google Play → `google_play`,
  PayPal → `paypal`, Amazon → `amazon`. A registry `billed_via_parent` (Apple Music → Apple) is used otherwise.
  Stripe is a payment processor, so Stripe receipts count as `direct` (the merchant is cancelled directly). An
  aggregator receipt that names no known service stays under the aggregator as "(service not identified)", with
  lower confidence.
- **D18 Period, next charge, expiry.** With two or more charges, the median gap picks the period from these bands:
  weekly 5-9 days, monthly 26-35, quarterly 84-98, semiannual 170-195, annual 350-380. Otherwise the email wording
  decides (FR-1-3). Next charge: the trial end date, else the newest explicit renewal or next-billing date after the
  last charge, else last charge + period. A date in the past rolls forward to today or later (FR-1-4). With no
  charge for two periods plus 3 days (or 400 days when the period is unknown) and no future renewal date, the
  status is `expired` (10.1).
- **D19 Last used (FR-1-5).** It is only ever an inference, and the evidence text always starts with "Inferred". The
  newest usage email for the merchant and account counts. Sign-in or activity emails give `active` if 60 days old
  or less. Win-back emails ("we miss you", "come back") give `likely_unused` if 180 days old or less. Otherwise it is
  `unknown`. A new `usage_signals` Gmail query template fetches these emails (subject-targeted only, PV-2).
- **D20 Price increases (FR-1-16).** An increase is reported when a charge is higher than the previous one, or when a
  price-change email ("from $X to $Y", "will increase to $Y") is higher than the current amount. Decreases are not
  reported. The alternatives are the generic options (cheaper tier or annual plan, keep, cancel with confirmation)
  plus up to three registry peers in the same category. Their prices are **not** checked, and the bot must say so.
  Increases are computed when asked for and are not stored. Deduplicating notifications belongs to the routines
  (M1b).

## Engineering decisions (M1b: duplicates, trials, reminders, undo)
- **D21 Duplicates (FR-1-6).**
  - Rule (a): the same merchant is active more than once, across accounts, card endings, or channels. On a single
    account it is flagged `suspected_same_subscription` and both rows are kept (10.1).
  - Rule (c): a declared household address appears among the recipients of mail in the user's own mailbox. Only
    that address is stored (`extracted.household_recipient`). Other recipients are dropped, and other mailboxes
    are never read (PV-7).
  - Rule (b): category overlap. Thresholds are video >= 3 and cloud storage >= 2 (spec), plus music >= 2 and
    VPN >= 2.
  - Group ids are deterministic. Family plan prices are not in the registry, so no saving amount is promised for
    them.
- **D22 Trial end inference (FR-1-7).** The basis is, in order: a stated end date, a stated length (start + N
  days), or else the shortest common length, 7 days. The short default makes reminders early rather than late. A
  guessed end is capped at confidence 0.45 (needs review), the reminder says it is a guess, and pre-authorization
  is refused for it.
- **D23 Reminders (FR-1-8, R3).**
  - `trial_end` reminders fire at T-2 and T-1, 09:00 in the user's time zone (DST-aware). If both slots have
    passed, one overdue T-1 reminder is scheduled.
  - A changed end date dismisses and reschedules. "Keep", or an approved cancellation, stops the reminders.
  - Quiet hours (default 22:00-08:00) hold delivery. At most one reminder per trial per run.
  - Remind-later snoozes until the next 09:00, capped at 20:00 on T-1, and is refused after that.
- **D24 Pre-authorization and undo (FR-1-9, Q4 default).**
  - The pre-authorization is an ordinary cancel request with `constraints.preauthorized` and `execute_not_before`
    = T-1 11:00 local. It still needs the user's explicit confirmation.
  - R4 posts the 2-hour warning at T-1 09:00. A request is `ready_to_execute` only after the warning has been out
    for 2 hours, so a late warning delays execution.
  - Undo (`stop_trial_auto_cancel`) accepts any stop or undo wording, because revoking is the safe direction.
    `取消` alone is ambiguous and does not count.
  - Pre-authorization is limited to the browser path. Store-billed, phone, and email cancellations are manual
    steps in M1b. A bot-placed call needs an approved script that passes the Q3 policy (D30). Real calls arrive in M1d.
    A bot-sent email needs its exact draft approved.
- **D25 Routines as code.** R1, R3, R4, and R10 are plain functions in `routines/`, also exposed as MCP tools. They
  return texts for the user's own chat and never send, call, cancel, or start a run. Scheduling them as Grok Bot
  routines was held for Charles's approval. He approved it on 2026-10-22; see D31.
- **Q11 (not decided here).** A cancel request for a merchant whose registry entry is not `verified` carries an
  "unverified" risk note (`modules/trials.py: WARN_UNVERIFIED_MERCHANTS`, TODO(Q11)). Who maintains the registry and
  how entries become verified is still Charles's decision.

## Engineering decisions (M1c: card statements)
- **D26 Statement intake.** `import_card_statement` reads only files in the upload inbox (the evidence inbox
  directories). It applies the D14 checks: no symlinks or hard links, re-verified through the open descriptor.
  Size cap is 10 MB. PDFs go through poppler `pdftotext -layout`, fed the verified bytes on stdin. CSVs use the
  stdlib. Header aliases cover common exports. A CSV the stdlib cannot read (a field over 131,072 characters, a
  malformed file) is a `StatementError` like any other parse error, so the D28 "upload was deleted" note applies.
  The inbox directories must be a list or tuple of path strings. A bare string is refused (iterating it would allow
  `/`, so any path), and so is the filesystem root.
  - Payments and credits are found by descriptor or by a type column, and are always dropped, whatever their sign.
    The descriptor match is precise: autopay, "thank you", "payment received", card/credit card/loan/mortgage
    payment, online/mobile/ACH/e-payment, a line that is only payment words and reference numbers, refunds and
    statement credits. A merchant name followed by PMT or PAYMENT ("GOOGLE *YOUTUBE PMT", "SPOTIFY PAYMENT") is a
    charge, and a line with a billing-channel prefix (D29) is a charge unless it says refund or statement credit.
    Bare "credit" is not matched, because credit monitoring is a real subscription.
  - Sign convention of a single amount column, never by majority vote: a type column decides from its sale rows;
    else the payment lines decide (negative payments: the usual convention; positive payments: purchases are
    negative, unless every other line is positive too); else purchases are negative only if every line is.
    Anything ambiguous keeps charges positive. (The majority rule turned three AUTOPAY payments into a $500/month
    subscription and dropped the real purchase.)
- **D27 What is kept (FR-0-8, PV-3).** A line is kept only if it is a positive charge and one of these holds:
  - it resolves to a registry merchant outside retail and food delivery;
  - its descriptor has subscription wording (subscription, membership, recurring, prime, ...);
  - the same descriptor repeats within 10% of the amount at a subscription interval (D18 bands).
  All other lines are discarded and never stored. A kept line stores only date, redacted descriptor, amount, and card
  last 4. Card numbers in a card column are cut to the last 4 digits. Limitation: an unknown merchant that appears
  once per statement is only caught when two or more months are uploaded, or through its wording.
  Rent, HOA dues, property management, and mortgage lines with no registry merchant repeat like subscriptions, so
  they are kept, but they are labelled "not a subscription" in the import result and in `list_subscriptions`
  (`not_a_subscription`, `not_subscriptions`). They are left out of the totals, the subscription count
  (`subscription_count`, the N in "Your subscriptions (N)") and price-change reports, sort after every real
  subscription, are shown in their own "Not subscriptions" section, and get confidence 0.3. Strong subscription
  wording (subscription, membership, premium) overrides rent wording, and a web domain is a brand, not rent
  wording, so "APARTMENTS.COM PREMIUM subscription" is a subscription. "Monthly" or "recurring" do not override it.
- **D28 Original file.** The upload is deleted right after it is read, by default, even if parsing then fails, and
  the result or error says so. `keep_original: true` keeps it.
- **D29 Linking.** PayPal/Apple/Google/Amazon descriptor prefixes set the billing channel. A charge links to the
  existing subscription with the same merchant and channel, preferring one with the same card ending. Otherwise a
  statement-only subscription is created (no account email; confidence 0.7, or 0.5 for an unknown merchant). A
  later Gmail receipt adopts that row instead of creating a duplicate. Statement charge references are deterministic
  letter-only digests, so re-importing the same statement adds nothing. Each import is one transaction
  (`Store.transaction()`): if any line fails (for example `ForbiddenDataError`), nothing from that statement is
  stored and the error says "nothing was imported". No schema change.

## Decisions by Charles (2026-10-22)
- **D30 Call honesty (resolves Q3): disclose up front.** Charles revised this decision on 2026-10-22, in his own
  words: calls MUST proactively disclose up front that the caller is an AI. This supersedes his earlier choice the same
  day (no proactive disclosure, truthful if asked).
  - Every call opens with: "Hi, this is an AI assistant calling on behalf of <user> about their account." (Chinese:
    「您好，我是代表 <user> 致电的 AI 助手，咨询其账户相关事宜。」). The generated rules also tell the caller to say it is an
    AI assistant in its first sentence on every call.
  - Truthful if asked still applies: if the other party asks whether they are talking to an AI (or a bot, a robot,
    a recording), the answer is "Yes, I'm an AI assistant calling on behalf of <user>." The caller never claims to be
    human and never claims to be the user.
  - Both settings are locked. `config/call_scripts/policy.json` has `proactive_ai_disclosure: true` and
    `if_asked_whether_ai: answer_truthfully`. `core/call_policy.py` refuses to load any other value (including
    `proactive_ai_disclosure: false`), defaults a missing value to true, and rejects unknown keys.
    `CallPolicy(proactive_ai_disclosure=False)` and `CallPolicy(truthful_if_asked=False)` raise. Locking it on
    breaks nothing: no code path needed the undisclosed opening. Turning it off again would need a new decision by
    Charles and a code change, not a config edit.
  - Call scripts are allowlist-structured. `build_call_script(user_name, task_steps)` writes a generated
    `[CALL IDENTITY RULES]` block (the disclosing opening line, the truthful answer, never claim to be human or the
    user, rules override task steps), then `[TASK]` with numbered steps. Task steps may not discuss the caller's
    identity: no AI, bot, human, person, pretending, or evasion wording, and no self-claim to be the user, the
    account holder, or an agent or representative.
    `validate_script` is a normalized deny-list backstop. It applies NFKC, straight quotes, A.I. -> ai, removes
    invisible characters, collapses whitespace, lowercases, and has English and Chinese patterns.
  - The action gate enforces this for every `bland_call` request (`place_call`, `negotiate_bill`, phone cancels and
    claims). It runs on create and amend, and again in `begin_execution`. `constraints.call_user_name` (plus
    `constraints.account_holder_name` when different) must be set. The identity block must match exactly for that
    name. The validator also checks, separately, that the opening line carries the AI disclosure, so a script
    without it is rejected. Every task step must pass. Any error, including a policy file that does not load or
    tries to turn disclosure off, rejects the request (fail closed). The bot gets a valid script from the
    `build_call_script` tool. The confirmation card shows "Calling on behalf of: <name>. The caller says up front
    that it is an AI assistant." as its first line.
  - Names are bound to the user's settings. The user's name, plus any other account-holder names the user lists,
    is saved with `set_call_identity` in `notification_prefs.call_identity` (existing JSON column, no schema
    change). The gate requires a saved identity and re-validates it on every check. It rejects a request whose
    `call_user_name` differs from the saved name, or whose `account_holder_name` is not a saved name.
    `build_call_script` (tool) takes the name from settings, not from the agent.
  - Names are allowlisted (`check_name`):
    - Unicode letters in one script (CJK may mix Han and kana), with single spaces, hyphens, and apostrophes only
      between letters. At most 40 characters and 4 words.
    - No digits, punctuation, full-width or compatibility forms, invisible characters, or mixed scripts.
    - A short list of words that are never part of a name: instruction words ("ignore", "say", "if", "you", ...),
      identity words (human, robot, bot, AI, assistant, machine, ...), and role words (husband, wife, mom, holder,
      customer, authorized, ...). Real names such as "Nguyễn Văn An", "Grace Son", "Ray Parent", "Sam Person",
      "Destiny Real", "Rob Agent" and "Ai Weiwei" pass. The task-step filters are not applied to names any more:
      names are already bound to the saved identity and strictly charset-checked, which leaves little room for
      injection.
  - Task-step checks:
    - Steps use Latin or Chinese/Japanese letters only (plus digits, punctuation and symbols). Other scripts,
      including Cyrillic, Greek, Armenian and Cherokee look-alikes, are rejected.
    - Checks run on several forms of the text: plain, spaced letters joined ("h u m a n"), split words joined
      ("hu man"), confusables folded (Cyrillic, Greek, Armenian, Cherokee, Latin small capitals and other letter
      variants), and digit swaps undone ("hum4n"). They also run on all steps joined together, so a claim cannot be
      split across steps. Long encoded strings (base64 and similar) are rejected.
    - Identity terms include software, program (not "loyalty program" and similar), machine, and family words
      (spouse, husband, wife, relative, ...), plus authorized / power of attorney. "Agent", "representative",
      "operator" and "caller" are rejected only in a claim about the caller ("you are a live agent"); asking for one
      ("Ask if the agent can waive the fee") is fine. The same goes for names: "your name is" is rejected, "ask the
      representative for their name" is fine.
    - First person may refer only to the AI assistant, never to the account: "my account", "I have been a customer
      since 2019", "the account is yours", "speak as if" are rejected.
    - Steps must be stated in full: "follow the instructions in the notes field" and similar are rejected.
    - A yes/no answer must name the question it answers, and the question may not be about the caller. "If they ask
      whether you want to keep the service, say no" is fine; "If they ask, answer no" and "If they ask whether this
      is a robocall, say no" are rejected.
    - A small set of other-language identity words is covered (humain, Mensch, humano, zhenren, ...).
  - A deny-list cannot be complete. The generated rules say they override every task step, and post-call
    transcript audits (did the caller disclose up front, did it ever claim to be human or the user) are planned for
    M1d, when Bland calls are wired up.
  - No real calls are placed. Bland calls are still M1d work and still go through the confirmation gate.
- **D31 Routines on by default.** R1 (daily scan, 05:30), R3 (reminder dispatch, hourly), R4 (trial guard, hourly),
  and R10 (request expiry, hourly) are enabled by default. Each time is in the user's time zone.
  - R4 runs hourly instead of only at 09:00 (spec 7.3 lists it daily). A pre-authorized cancel becomes ready at T-1
    11:00, two hours after the 09:00 warning. A daily-only run would first see it at T 09:00, which may be after the
    charge. The user-facing timing is unchanged: the T-2/T-1 reminders and the warning are still keyed to 09:00
    local, and the other hourly runs post nothing new.
  - The registry is `routines/schedule.py`. The `list_routines` tool returns what the bot registers as Grok Bot
    scheduled tasks.
  - A user can turn a routine off with `set_routine_enabled`. The setting is stored in
    `notification_prefs.routines`, so the schema does not change. A missing setting means on. Values must be real
    booleans: `update_settings` and `set_routine_enabled` reject `"false"`, `0`, and similar. On read, only `true`
    or a missing value counts as on; any other stored value counts as off.
  - If a disabled routine still fires, every tool it calls with `scheduled=true` returns `skipped`. For R1 this is
    the whole scan: `scan_plan` returns `skipped` with "do not read Gmail", and `ingest_messages`, `complete_scan`,
    and `refresh_after_scan` also skip. A scan the user asks for by hand still runs.
  - Routines only read and post texts to the user's own chat. They never send to third parties, call, cancel, or
    start an action run. Any cancellation still needs the user's explicit confirmation through the action gate.
    Tests check this.
- **Open decision for Charles: calling as family or an "authorized" person.** Whether a call may say it is
  the user's spouse or relative, or that it is authorized to act on the account (power of attorney, authorized
  user), is a business and legal question for M1d. Until Charles decides, it fails closed: task steps with those
  terms are rejected (`IDENTITY_TOPIC`, D30), and the identity block only ever says "calling on behalf of
  <user>".
- **Still open or deferred.** Q11 (who maintains the merchant registry) is still open. The accuracy test with real
  labeled emails (B1) is deferred.
