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

        tl = c.tool("audit_timeline")["structuredContent"]["timeline"]
        assert {t["request"]["id"]: t["request"]["status"] for t in tl}[rid] == "executed"

        refused = c.tool("delete_all_data", confirm="yes")
        assert refused["isError"]
        wiped = c.tool("delete_all_data", confirm="DELETE ALL MY DATA")["structuredContent"]
        assert wiped["deleted"]["action_requests"] == 2
        assert c.rpc("tools/call", {"name": "nope", "arguments": {}})["error"]["code"] == -32602
    finally:
        c.close()
