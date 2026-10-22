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
           "per_year": "per year", "channel": "How", "draft": "Exact text",
           "reply": "To approve, reply exactly: confirm {code}",
           "reply_batch": "To approve all of them, reply exactly: confirm {codes}"},
    "zh": {"confirm": "确认", "keep": "保留", "later": "稍后提醒", "cancel": "取消",
           "edit": "修改条件", "saving": "预计节省", "steps": "我将执行", "disclose": "将披露",
           "risks": "注意", "expires": "请求有效期至", "header": "请确认以下操作",
           "batch": "请确认以下 {n} 项操作", "per_year": "年化", "channel": "方式", "draft": "完整内容",
           "reply": "如需批准，请回复：确认 {code}", "reply_batch": "如需全部批准，请回复：确认 {codes}"},
}


CHANNELS = {"browser": "web browser", "bland_call": "phone call (Bland)", "email": "email", "manual": "manual steps"}


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
    if req.get("executor"):
        lines.append(f"{L['channel']}: {CHANNELS.get(req['executor'], req['executor'])}")
    if req.get("draft_content"):
        lines.append(f"{L['draft']}:")
        lines += [f"  | {line}" for line in req["draft_content"].splitlines()]
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
    if req.get("confirmation_code"):
        lines.append(L["reply"].format(code=req["confirmation_code"]))
    return "\n".join(lines)


def format_batch(reqs: list[dict[str, Any]], lang: str = "en") -> str:
    """Batch confirmation lists every item (8.2)."""
    L = _labels(lang)
    lines = [L["batch"].format(n=len(reqs))]
    for i, req in enumerate(reqs, 1):
        saving = f" ({money(req['expected_saving_cents'])})" if req.get("expected_saving_cents") else ""
        lines.append(f"{i}. [{req['id']}] {req['summary']}{saving}")
    lines.append(f"[{L['confirm']}] [{L['cancel']}]")
    codes = [r.get("confirmation_code") for r in reqs]
    if reqs and all(codes):
        lines.append(L["reply_batch"].format(codes=" ".join(codes)))
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


# --- subscriptions (US-1-1, FR-1-16) ---------------------------------------------------------------
PERIOD_SUFFIX = {
    "en": {"weekly": "/week", "monthly": "/month", "quarterly": "/quarter", "semiannual": "/6 months",
           "annual": "/year", "unknown": " (period unknown)"},
    "zh": {"weekly": "/周", "monthly": "/月", "quarterly": "/季度", "semiannual": "/半年", "annual": "/年",
           "unknown": "（周期未知）"},
}
SUB_LABELS = {
    "en": {"header": "Your subscriptions ({n})", "next": "next charge", "used": "last used",
           "inferred": "inferred", "conf": "confidence", "trial": "free trial ends", "then": "then",
           "total": "Active total: {m}/month (about {y}/year)", "empty": "I haven't found any subscriptions yet.",
           "review": "Please check these (low confidence): {names}",
           "via": {"apple": "via Apple", "google_play": "via Google Play", "paypal": "via PayPal",
                   "amazon": "via Amazon"},
           "signals": {"active": "recently active", "likely_unused": "possibly unused", "unknown": "unknown"},
           "price": "Price increase: {name} went from {old} to {new}{per}.",
           "price_opts": "Options: {opts}. Similar services: {peers}."},
    "zh": {"header": "你的订阅（{n} 项）", "next": "下次扣费", "used": "最后使用", "inferred": "推断",
           "conf": "置信度", "trial": "免费试用结束", "then": "之后",
           "total": "活跃订阅合计：{m}/月（约 {y}/年）", "empty": "暂未发现订阅。",
           "review": "请核实以下项目（置信度低）：{names}",
           "via": {"apple": "经 Apple", "google_play": "经 Google Play", "paypal": "经 PayPal", "amazon": "经 Amazon"},
           "signals": {"active": "近期活跃", "likely_unused": "可能未使用", "unknown": "未知"},
           "price": "价格上涨：{name} 从 {old} 涨到 {new}{per}。",
           "price_opts": "可选：{opts}。同类服务：{peers}。"},
}


def format_subscription_list(listing: dict[str, Any], lang: str = "en") -> str:
    """US-1-1: name, amount, period, next charge, last-used signal (labelled inferred), confidence."""
    L = SUB_LABELS.get(lang, SUB_LABELS["en"])
    P = PERIOD_SUFFIX.get(lang, PERIOD_SUFFIX["en"])
    subs = listing.get("subscriptions") or []
    if not subs:
        return L["empty"]
    lines = [L["header"].format(n=len(subs))]
    for s in subs:
        via = L["via"].get(s.get("billing_channel"), "")
        name = f"{s['display_name']} ({via})" if via else s["display_name"]
        price = money(s.get("amount_cents"), s.get("currency") or "USD") + P.get(s.get("period") or "unknown", "")
        if s.get("status") == "trial":
            price = (f"{L['trial']} {s.get('trial_end_date') or '?'}, {L['then']} "
                     f"{money(s.get('trial_converts_to_cents') or s.get('amount_cents'))}{P.get(s.get('period') or 'unknown', '')}")
        parts = [name, price]
        if s.get("next_charge_date") and s.get("status") != "trial":
            parts.append(f"{L['next']} {s['next_charge_date']}")
        signal = L["signals"].get(s.get("last_used_signal") or "unknown", "")
        parts.append(f"{L['used']}: {signal} ({L['inferred']})")
        parts.append(f"{L['conf']} {s.get('confidence', 0):.2f}")
        lines.append("• " + " · ".join(parts))
    m = listing.get("monthly_total_cents") or 0
    lines.append(L["total"].format(m=money(m), y=money(m * 12)))
    review = [s["display_name"] for s in subs if s["id"] in set(listing.get("needs_review") or [])]
    if review:
        lines.append(L["review"].format(names=", ".join(review)))
    return "\n".join(lines)


def format_price_changes(changes: list[dict[str, Any]], lang: str = "en") -> str:
    L = SUB_LABELS.get(lang, SUB_LABELS["en"])
    P = PERIOD_SUFFIX.get(lang, PERIOD_SUFFIX["en"])
    lines = []
    for c in changes:
        lines.append(L["price"].format(name=c["display_name"], old=money(c["old_cents"]), new=money(c["new_cents"]),
                                       per=P.get(c.get("period") or "unknown", "")))
        alt = c.get("alternatives") or {}
        lines.append(L["price_opts"].format(opts="; ".join(alt.get("options") or []),
                                            peers=", ".join(alt.get("similar_services") or []) or "—"))
    return "\n".join(lines)


# --- trials (FR-1-8, FR-1-9) -----------------------------------------------------------------------
TRIAL_LABELS = {
    "en": {"ends": "Your {name} free trial ends on {date} ({when}).", "then": "After that it costs {price}.",
           "when": {0: "today", 1: "tomorrow"}, "days": "in {n} days",
           "inferred": "The end date is a guess (the email gave no date or length); please check it in your account.",
           "length": "The end date is worked out from the trial length in the email.",
           "pending": "Your cancellation request is still waiting for your confirmation.",
           "buttons": "[Cancel] [Keep] [Remind me later]",
           "warn": "I will cancel {name} at {at} (in about 2 hours), as you pre-authorized. Reply stop to keep it.",
           "expired": "Your request to cancel {name} expired without confirmation. The trial ends on {date}. "
                      "Reply cancel if you still want me to cancel it."},
    "zh": {"ends": "你的 {name} 免费试用将于 {date}（{when}）结束。", "then": "之后每期 {price}。",
           "when": {0: "今天", 1: "明天"}, "days": "{n} 天后",
           "inferred": "结束日期为推断（邮件中没有日期或时长），请在账户页核实。",
           "length": "结束日期根据邮件中的试用时长推算。",
           "pending": "你的取消请求仍在等待确认。",
           "buttons": "[取消] [保留] [稍后提醒]",
           "warn": "按你的预授权，我将在 {at}（约 2 小时后）取消 {name}。回复「停止」可撤销。",
           "expired": "取消 {name} 的请求已过期未确认。试用将于 {date} 结束。如仍需取消，请回复「取消」。"},
}


def format_trial_reminder(sub: dict[str, Any], days_left: int, basis: str | None, pending: bool,
                          lang: str = "en") -> str:
    L = TRIAL_LABELS.get(lang, TRIAL_LABELS["en"])
    P = PERIOD_SUFFIX.get(lang, PERIOD_SUFFIX["en"])
    when = L["when"].get(days_left) or L["days"].format(n=days_left)
    lines = [L["ends"].format(name=sub["display_name"], date=sub["trial_end_date"], when=when)]
    price = sub.get("trial_converts_to_cents") or sub.get("amount_cents")
    if price:
        lines.append(L["then"].format(price=money(price, sub.get("currency") or "USD") + P.get(sub.get("period") or "unknown", "")))
    if basis == "default_length":
        lines.append(L["inferred"])
    elif basis == "stated_length":
        lines.append(L["length"])
    if pending:
        lines.append(L["pending"])
    lines.append(L["buttons"])
    return "\n".join(lines)


def format_preauth_warning(sub: dict[str, Any], at, tz, lang: str = "en") -> str:
    L = TRIAL_LABELS.get(lang, TRIAL_LABELS["en"])
    return L["warn"].format(name=sub["display_name"], at=at.astimezone(tz).strftime("%Y-%m-%d %H:%M"))


def format_expired_trial_request(sub: dict[str, Any], lang: str = "en") -> str:
    L = TRIAL_LABELS.get(lang, TRIAL_LABELS["en"])
    return L["expired"].format(name=sub["display_name"], date=sub["trial_end_date"])


def format_duplicates(result: dict[str, Any], lang: str = "en") -> str:
    """FR-1-6 suggestions. The advice text is English; amounts are formatted."""
    lines = []
    for g in result.get("duplicate_groups") or []:
        extra = ""
        if g.get("possible_monthly_saving_cents"):
            extra = f" Possible saving: {money(g['possible_monthly_saving_cents'])}/month."
        lines.append(f"• {g['advice']} You pay {money(g['current_monthly_total_cents'])}/month in total.{extra}")
    for o in result.get("overlaps") or []:
        lines.append(f"• {o['advice']} ({', '.join(o['names'])}: {money(o['monthly_total_cents'])}/month)")
    if not lines:
        return "未发现重复订阅。" if lang == "zh" else "No duplicate subscriptions found."
    return "\n".join(lines)
