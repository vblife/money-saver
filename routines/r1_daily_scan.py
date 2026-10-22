"""R1 daily_email_scan (05:30): incremental Gmail scan -> inventory -> duplicates -> trial reminders.

In Grok Bot the bot runs the Gmail queries itself (D5) and then calls ``after_ingest`` through the MCP
server. ``run`` chains everything against any client with ``search(query)``; tests pass a fake client.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from core import gmail_scanner
from core.action_gate import ActionGate
from core.merchant_resolver import MerchantResolver
from core.store import Store
from modules import duplicates
from modules.subscriptions import SubscriptionInventory, _tz
from modules.trials import Trials


def after_ingest(store: Store, gate: ActionGate, resolver: MerchantResolver | None = None) -> dict[str, Any]:
    resolver = resolver or MerchantResolver()
    today = gate.now_fn().astimezone(_tz(store.get_user().get("timezone"))).date()
    inventory = SubscriptionInventory(store, resolver).build(today)
    dups = duplicates.detect(store, resolver)
    reminders = Trials(store, gate, resolver).schedule_reminders()
    return {"inventory": inventory, "duplicates": dups, "reminders": reminders}


def run(store: Store, gate: ActionGate, client: gmail_scanner.GmailClient, accounts: list[str],
        resolver: MerchantResolver | None = None) -> dict[str, Any]:
    resolver = resolver or MerchantResolver()
    from core.classifier import Classifier
    classifier = Classifier(resolver)
    now: datetime = gate.now_fn()
    scans = {acct: gmail_scanner.run_scan(store, client, acct, now, classifier) for acct in accounts}
    return {"scans": {a: {k: v for k, v in s.items() if k != "results"} for a, s in scans.items()},
            **after_ingest(store, gate, resolver)}
