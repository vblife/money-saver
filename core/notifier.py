"""Message formatting for chat notifications (FR-0-9, Appendix A).

The MVP pushes messages through the Grok Bot conversation; an optional copy may go only to the
user's own mailbox. This module only formats text; it never sends anything.
"""
from __future__ import annotations

from typing import Any

ACTION_LABELS = {
    "en": {"confirm": "Confirm", "keep": "Keep", "later": "Remind me later", "cancel": "Don't do it",
           "edit": "Change conditions", "saving": "Expected saving", "steps": "What I will do",
           "disclose": "I will share", "risks": "Note", "expires": "This request expires",
           "header": "Please confirm this action", "batch": "Please confirm all {n} actions below",
           "per_year": "per year"},
    "zh": {"confirm": "确认", "keep": "保留", "later": "稍后提醒", "cancel": "取消",
           "edit": "修改条件", "saving": "预计节省", "steps": "我将执行", "disclose": "将披露",
           "risks": "注意", "expires": "请求有效期至", "header": "请确认以下操作",
           "batch": "请确认以下 {n} 项操作", "per_year": "年化"},
}


def money(cents: int | None, currency: str = "USD") -> str:
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    symbol = {"USD": "$", "EUR": "€"}.get(currency, currency + " ")
    return f"{sign}{symbol}{cents // 100:,}.{cents % 100:02d}"


def _labels(lang: str) -> dict[str, str]:
    return ACTION_LABELS.get(lang, ACTION_LABELS["en"])


def format_action_request(req: dict[str, Any], lang: str = "en") -> str:
    """A confirmation card for one ActionRequest. Every consequential detail is shown (8.1/8.2)."""
    L = _labels(lang)
    lines = [f"{L['header']} [{req['id']}]", f"• {req['summary']}", f"{L['steps']}:"]
    lines += [f"  {i}. {step}" for i, step in enumerate(req["plan_steps"], 1)]
    if req.get("disclosed_fields"):
        shown = ", ".join(f"{d['field']}={d.get('value_masked', '')}" for d in req["disclosed_fields"])
        lines.append(f"{L['disclose']}: {shown}")
    if req.get("expected_saving_cents"):
        cents = req["expected_saving_cents"]
        lines.append(f"{L['saving']}: {money(cents)}")
    if req.get("risk_notes"):
        lines.append(f"{L['risks']}: {req['risk_notes']}")
    lines.append(f"{L['expires']}: {req['expires_at']}")
    if req["action_type"] in ("negotiate_bill", "place_call"):
        buttons = [L["confirm"], L["edit"], L["cancel"]]
    else:
        buttons = [L["confirm"], L["keep"], L["later"]]
    lines.append(" ".join(f"[{b}]" for b in buttons))
    return "\n".join(lines)


def format_batch(reqs: list[dict[str, Any]], lang: str = "en") -> str:
    """Batch confirmation lists every item (8.2)."""
    L = _labels(lang)
    lines = [L["batch"].format(n=len(reqs))]
    for i, req in enumerate(reqs, 1):
        saving = f" ({money(req['expected_saving_cents'])})" if req.get("expected_saving_cents") else ""
        lines.append(f"{i}. [{req['id']}] {req['summary']}{saving}")
    lines.append(f"[{L['confirm']}] [{L['cancel']}]")
    return "\n".join(lines)


def format_month_headline(summary: dict[str, Any], month_label: str, lang: str = "en") -> str:
    """Appendix B headline: only verified counts in the headline; pending, credits, points separate."""
    if lang == "zh":
        text = f"{month_label} 为你节省 {money(summary['verified_cents'])}（已核实）"
        extra = f"另有 {money(summary['pending_cents'])} 待核实"
    else:
        text = f"In {month_label} you saved {money(summary['verified_cents'])} (verified)"
        extra = f"Plus {money(summary['pending_cents'])} pending verification"
    parts = [text, extra]
    if summary.get("estimated_cents"):
        parts.append(("估算 " if lang == "zh" else "Estimated (not counted): ") + money(summary["estimated_cents"]))
    if summary.get("travel_credit_cents"):
        parts.append(("旅行信用（未计入）" if lang == "zh" else "Travel credit (not counted): ")
                     + money(summary["travel_credit_cents"]))
    if summary.get("points"):
        parts.append(("积分/里程（未计入）" if lang == "zh" else "Points/miles (not counted): ") + f"{summary['points']:,}")
    return "\n".join(parts)
