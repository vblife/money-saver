"""End-to-end over stdio, the way Grok Bot launches the MCP server."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server", "server.py")


class Client:
    def __init__(self, tmp_path):
        self.p = subprocess.Popen([sys.executable, SERVER, "--stdio", "--db", str(tmp_path / "msb.db"),
                                   "--evidence-dir", str(tmp_path / "evidence"),
                                   "--evidence-inbox", str(tmp_path / "inbox")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.n = 0

    def rpc(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name, **args):
        res = self.rpc("tools/call", {"name": name, "arguments": args})["result"]
        return res

    def close(self):
        self.p.stdin.close()
        self.p.wait(5)
        self.p.stdout.close()
        self.p.stderr.close()


def test_stdio_end_to_end(tmp_path):
    c = Client(tmp_path)
    try:
        init = c.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
        assert init["serverInfo"]["name"] == "money-saver"
        assert "explicit confirmation" in init["instructions"]
        names = {t["name"] for t in c.rpc("tools/list")["result"]["tools"]}
        assert {"scan_plan", "ingest_messages", "create_action_request", "confirm_action_request",
                "start_action_run", "ledger_add", "audit_timeline", "delete_all_data"} <= names

        plan = c.tool("scan_plan", account="alex@example.com")["structuredContent"]
        assert plan["mode"] == "initial"
        ing = c.tool("ingest_messages", account="alex@example.com", messages=[
            {"id": "m1", "from": "Hulu <account@hulu.com>", "subject": "Your Hulu receipt",
             "body": "You were charged $17.99 for your monthly plan.", "date": "2026-10-01T00:00:00Z"}])["structuredContent"]
        assert ing["results"][0]["category"] == "subscription_receipt" and ing["results"][0]["merchant"] == "hulu"

        c.tool("update_settings", settings={"approved_pii_fields": ["name"]})
        made = c.tool("create_action_request", action_type="cancel_subscription", target_ref="sub_hulu",
                      summary="Cancel Hulu", plan_steps=["Open account page", "Cancel"],
                      expected_saving_cents=1799)["structuredContent"]
        rid = made["request"]["id"]
        assert "[Confirm]" in made["confirmation_text"]

        # Negative: cannot start before confirmation; vague reply rejected.
        blocked = c.tool("start_action_run", request_id=rid, executor="browser")
        assert blocked["isError"] and "NotApprovedError" in blocked["content"][0]["text"]
        vague = c.tool("confirm_action_request", request_id=rid, user_message="maybe")
        assert vague["isError"]
        bought = c.tool("create_action_request", action_type="purchase", target_ref="x", summary="Buy", plan_steps=["buy"])
        assert bought["isError"]

        code = made["confirmation_code"]
        assert code in made["confirmation_text"]
        for bad in ("I can't confirm that", "我不批准", "Confirm cancel Hulu", f"don't confirm {code}"):
            assert c.tool("confirm_action_request", request_id=rid, user_message=bad)["isError"], bad
        # bare "confirm" without the shown-text digest is not bound to anything
        assert c.tool("confirm_action_request", request_id=rid, user_message="confirm")["isError"]
        assert c.tool("confirm_action_request", request_id=rid, user_message=f"confirm {code}",
                      source="chat:1")["structuredContent"]["request"]["status"] == "approved"
        wrong_channel = c.tool("start_action_run", request_id=rid, executor="email")
        assert wrong_channel["isError"] and "approved for 'browser'" in wrong_channel["content"][0]["text"]
        run = c.tool("start_action_run", request_id=rid, executor="browser")["structuredContent"]["run"]
        outside = tmp_path / "id_rsa"
        outside.write_text("PRIVATE KEY")
        stolen = c.tool("record_evidence", kind="screenshot", related_ref="sub_hulu", path=str(outside))
        assert stolen["isError"] and "EvidencePathError" in stolen["content"][0]["text"]
        (tmp_path / "inbox").mkdir()
        (tmp_path / "inbox" / "done.png").write_bytes(b"png")
        shot = c.tool("record_evidence", kind="screenshot", related_ref="sub_hulu", path=str(tmp_path / "inbox" / "done.png"))
        assert shot["structuredContent"]["evidence"]["sha256"]
        ev = c.tool("record_evidence", kind="confirmation_email", related_ref="sub_hulu", message_id="m-confirm")["structuredContent"]["evidence"]
        done = c.tool("finish_action_run", run_id=run["id"], outcome="success", evidence_ids=[ev["id"]])["structuredContent"]
        assert done["run"]["outcome"] == "success"

        entry = c.tool("ledger_add", module="subscriptions", saving_type="recurring", description="Cancel Hulu",
                       amount_cents=1799, source_ref="sub_hulu", dedupe_key="cancel:sub_hulu", occurred_at="2026-10-19",
                       action_run_id=run["id"], evidence_ids=[ev["id"]])["structuredContent"]["entry"]
        c.tool("ledger_verify", entry_id=entry["id"], verification_ref="gmail:m-confirm")
        summary = c.tool("ledger_month_summary", year=2026, month=10)["structuredContent"]
        assert summary["verified_cents"] == 1799 and "$17.99" in summary["headline"]

        # digest binding: approve a second request with a bare "confirm" plus the digest of the shown text
        m2 = c.tool("create_action_request", action_type="cancel_subscription", target_ref="sub_p", summary="Cancel Peacock",
                    plan_steps=["Open account page", "Cancel"])["structuredContent"]
        assert c.tool("confirm_action_request", request_id=m2["request"]["id"], user_message="Confirm",
                      shown_text_sha256="0" * 64)["isError"]
        assert c.tool("confirm_action_request", request_id=m2["request"]["id"], user_message="Confirm",
                      shown_text_sha256=m2["confirmation_sha256"])["structuredContent"]["request"]["status"] == "approved"
        amend_bad = c.tool("amend_action_request", request_id=m2["request"]["id"],
                           changes={"disclosed_fields": [{"field": "ssn", "value_masked": "123-45-6789"}]}, reason="x")
        assert amend_bad["isError"]

        # bounded settings and dates return clean tool errors (no JSON-RPC -32603)
        for hours in (100000, 1e9, 0):
            assert c.tool("update_settings", settings={"thresholds": {"approval_ttl_hours": hours}})["isError"]
        for field in ("expires_at", "execute_not_before"):
            far = c.tool("create_action_request", action_type="cancel_subscription", target_ref="s9", summary="x",
                         plan_steps=["a"], **{field: "9999-12-31T00:00:00Z"})
            assert far["isError"] and "GateError" in far["content"][0]["text"]

        tl = c.tool("audit_timeline")["structuredContent"]["timeline"]
        assert {t["request"]["id"]: t["request"]["status"] for t in tl}[rid] == "executed"

        refused = c.tool("delete_all_data", confirm="yes")
        assert refused["isError"]
        wiped = c.tool("delete_all_data", confirm="DELETE ALL MY DATA")["structuredContent"]
        assert wiped["deleted"]["action_requests"] == 2
        assert c.rpc("tools/call", {"name": "nope", "arguments": {}})["error"]["code"] == -32602
    finally:
        c.close()


def test_stdio_subscription_inventory(tmp_path):
    c = Client(tmp_path)
    try:
        c.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        names = {t["name"] for t in c.rpc("tools/list")["result"]["tools"]}
        assert {"build_subscription_inventory", "list_subscriptions", "subscription_price_changes",
                "update_subscription", "add_subscription"} <= names
        msgs = [{"id": f"n{i}", "from": "Netflix <billing@netflix.com>", "subject": "Your Netflix receipt",
                 "body": f"Thanks for your payment. You were charged ${amt} for your monthly Netflix plan on {day}.",
                 "date": iso} for i, (amt, day, iso) in enumerate([
                     ("15.49", "Aug 3, 2026", "2026-08-03T16:00:00Z"), ("17.99", "Sep 3, 2026", "2026-09-03T16:00:00Z")])]
        c.tool("ingest_messages", account="alex@example.com", messages=msgs)
        built = c.tool("build_subscription_inventory", today="2026-09-10")["structuredContent"]
        assert len(built["created"]) == 1 and built["charges_added"] == 2
        assert built["price_changes"][0]["new_cents"] == 1799
        again = c.tool("build_subscription_inventory", today="2026-09-10")["structuredContent"]
        assert again["created"] == [] and again["updated"] == [] and again["charges_added"] == 0
        listing = c.tool("list_subscriptions", lang="en")["structuredContent"]
        sub = listing["subscriptions"][0]
        assert sub["next_charge_date"] == "2026-10-03" and "(inferred)" in listing["text"]
        pc = c.tool("subscription_price_changes")["structuredContent"]
        assert "$17.99" in pc["text"]
        bad = c.tool("update_subscription", subscription_id=sub["id"], status="cancelled")
        assert bad["isError"] and "confirmation gate" in bad["content"][0]["text"]
        ok = c.tool("update_subscription", subscription_id=sub["id"], user_decision="cancel")["structuredContent"]
        assert ok["subscription"]["status"] == "active"   # recording a decision cancels nothing
        added = c.tool("add_subscription", display_name="Local gym", amount_cents=4000, period="monthly")
        assert added["structuredContent"]["subscription"]["source"] == ["user_added"]
        assert c.tool("add_subscription", display_name="x", amount_cents=-5, period="monthly")["isError"]
        ctx = c.tool("get_context")["structuredContent"]
        assert ctx["counts"]["subscriptions"] == 2 and ctx["counts"]["action_requests"] == 0
    finally:
        c.close()


def test_stdio_trial_flow_needs_confirmation(tmp_path):
    from datetime import datetime, timedelta, timezone
    end = (datetime.now(timezone.utc) + timedelta(days=10)).date()
    c = Client(tmp_path)
    try:
        c.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        names = {t["name"] for t in c.rpc("tools/list")["result"]["tools"]}
        assert {"refresh_after_scan", "detect_duplicates", "dispatch_reminders", "run_trial_guard",
                "respond_to_trial_reminder", "preauthorize_trial_cancel", "stop_trial_auto_cancel"} <= names
        c.tool("ingest_messages", account="alex@example.com", messages=[{
            "id": "t1", "from": "Peacock <hello@peacocktv.com>", "subject": "Welcome to your Peacock free trial",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": f"Your free trial has started. Your trial ends on {end.strftime('%b %d, %Y')}. Then $7.99/month."}])
        out = c.tool("refresh_after_scan")["structuredContent"]
        assert len(out["inventory"]["created"]) == 1 and len(out["reminders"]["scheduled"]) == 2
        sub_id = out["inventory"]["created"][0]
        pre = c.tool("preauthorize_trial_cancel", subscription_id=sub_id)["structuredContent"]
        assert pre["request"]["status"] == "pending_user" and "confirm " in pre["confirmation_text"]
        guard = c.tool("run_trial_guard")["structuredContent"]
        assert guard["ready_to_execute"] == [] and guard["warnings"] == []
        bad = c.tool("stop_trial_auto_cancel", subscription_id=sub_id, user_message="sounds good")
        assert bad["isError"]
        stop = c.tool("stop_trial_auto_cancel", subscription_id=sub_id, user_message="stop")["structuredContent"]
        assert stop["revoked_request_ids"] == [pre["request"]["id"]]
        cancel = c.tool("respond_to_trial_reminder", subscription_id=sub_id, choice="cancel")["structuredContent"]
        assert cancel["request"]["status"] == "pending_user"
        started = c.tool("start_action_run", request_id=cancel["request"]["id"], executor="browser")
        assert started["isError"]
        dup = c.tool("detect_duplicates")["structuredContent"]
        assert dup["text"] == "No duplicate subscriptions found."
        exp = c.tool("expire_action_requests")["structuredContent"]
        assert exp == {"expired": [], "messages": []}
    finally:
        c.close()
