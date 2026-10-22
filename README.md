# Money Saver Bot

A Grok Bot that finds money you are losing and, **only after you explicitly confirm**, gets it back:
cancels subscriptions and free trials, negotiates bills, compares prices before you buy, tracks
prices after you buy and files price-adjustment claims, watches travel bookings for price drops
and delay compensation, and recovers forgotten money (refunds, settlements, unclaimed property,
expiring gift cards and points).

Every saving is recorded in an auditable **savings ledger** with evidence, summarized in a weekly
digest and a monthly "you saved $X" report.

- Gmail only; no bank linking required.
- Every consequential action (cancel, call, claim, email, rebook, form with personal data) goes
  through a confirmation gateway and needs your explicit approval. Purchases are disabled.

## Status

M0 (foundation) is in progress. See [`docs/plan.md`](docs/plan.md) and [`docs/decisions.md`](docs/decisions.md).
The product spec (Simplified Chinese PRD) is [`docs/spec.md`](docs/spec.md).

## Layout

```text
config/       merchant_registry.json, gmail_queries.json, call_scripts/
core/         store, gmail_scanner, classifier, extractor, merchant_resolver, action_gate, evidence, ledger, notifier, privacy
executors/    gate-enforcing executor base (browser / Bland / email executors arrive in M1+)
modules/      module logic (M1+)
routines/     scheduled routines R1-R10 (M1+)
reports/      weekly digest and monthly report (M2)
server/       MCP server (Python stdlib, JSON-RPC over stdio)
skills/       agent skills (English only)
bot/          bot instructions
tests/        pytest suite and synthetic email fixtures
docs/         spec, plan, decisions
```

## Running

```text
python3 server/server.py --stdio            # MCP server; data in $MSB_HOME (default /workspace/money-saver)
python3 -m pytest -q                         # tests (needs pytest)
```

`MSB_DB` and `MSB_EVIDENCE_DIR` override the database and evidence paths. `MSB_EVIDENCE_INBOX` (default `$MSB_HOME/inbox`) is the only place evidence files may be copied from.

## License

MIT
