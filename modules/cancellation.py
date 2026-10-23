"""Cancellation planning, dry runs, and the manual fallback guide (M1d: FR-1-10..FR-1-14, D32-D36).

``plan_cancellation`` turns a subscription plus a channel into the exact plan the bot puts in
``create_action_request``: the steps, the reviewed cancel target from ``config/merchant_registry.json``, the
constraints the live gates check, and (for email and calls) a starting draft. It never acts. Cancel targets come
only from the reviewed registry; runtime proposals (``modules/merchant_proposals.py``) are never used here (D32).
"""
from __future__ import annotations

from typing import Any, Mapping

from core import call_policy
from core.execution_policy import LIVE_EXECUTORS, TARGET_FIELD, LiveGuard, reviewed_target

# Billing channels whose subscriptions are cancelled in the aggregator's account settings, not with the merchant.
AGGREGATOR_BY_CHANNEL = {"apple": "apple", "google_play": "google_play", "paypal": "paypal", "amazon": "amazon"}
CANCEL_EXECUTORS = (*LIVE_EXECUTORS, "manual")
TALKING_POINTS = {
    "en": ["I'd like to cancel my {name} subscription, effective at the end of the current billing period.",
           "If they offer a discount or a pause, ask for the details in writing and decide later; you don't have to "
           "accept on the spot.",
           "Ask for a cancellation confirmation number or a confirmation email.",
           "Ask for the date the service ends and confirm there will be no further charges."],
    "zh": ["我想取消 {name} 的订阅，在本计费周期结束时生效。",
           "如果对方提供折扣或暂停方案，请让对方书面说明，之后再决定；不必当场接受。",
           "请对方提供取消确认号或确认邮件。",
           "确认服务结束日期，并确认之后不会再扣费。"],
}


class CancellationError(ValueError):
    pass


def _subscription(store: Any, sub_id: str) -> dict[str, Any]:
    sub = store.get("subscriptions", sub_id)
    if sub is None:
        raise CancellationError(f"subscription {sub_id} not found")
    return sub


def call_task_steps(name: str) -> list[str]:
    """Identity-free task steps for build_call_script (D30). Validated here so a bad edit fails loudly."""
    steps = [f"Ask to cancel the {name} subscription on this account, effective at the end of the billing period.",
             "If a discount or pause is offered, do not accept it; ask them to describe it and note the details.",
             "Ask for a cancellation confirmation number and the date the service ends.",
             "Ask them to confirm that there will be no further charges."]
    for step in steps:
        call_policy.validate_task_step(step)
    return steps


def email_draft(name: str, account_email: str | None, lang: str = "en") -> str:
    who = account_email or "this email address"
    if lang == "zh":
        return (f"您好：\n\n请取消注册邮箱为 {who} 的账户的 {name} 订阅，在本计费周期结束时生效，并通过邮件确认取消。"
                "\n\n谢谢。")
    return (f"Hello,\n\nPlease cancel the {name} subscription for the account registered to {who}, effective at the "
            "end of the current billing period, and confirm the cancellation by email.\n\nThank you.")


def manual_guide(store: Any, registry: Mapping[str, Mapping[str, Any]], sub_id: str, lang: str = "en") -> dict[str, Any]:
    """FR-1-14: steps, link, phone, and talking points the user can follow themselves. Reviewed registry data only."""
    sub = _subscription(store, sub_id)
    name = sub["display_name"]
    entry = registry.get(sub.get("merchant_id") or "") or {}
    via = AGGREGATOR_BY_CHANNEL.get(sub.get("billing_channel") or "direct")
    billed = registry.get(via) if via else None
    source = billed or entry
    link, phone = source.get("cancel_url"), source.get("support_phone")
    steps = []
    if billed:
        steps.append(f"{name} is billed through {billed['name']}, so cancel it in your {billed['name']} account, "
                     f"not with {name} directly.")
    if link:
        steps.append(f"Open {link} and sign in yourself.")
    else:
        steps.append(f"Open {name}'s official website or app and sign in yourself (the reviewed merchant list has no "
                     "cancel link for it yet).")
    steps += ["Look for Account, Subscription, Membership or Billing, then Cancel.",
              "If a retention offer appears, you decide; declining it is usually a 'No thanks' or 'Continue to cancel' "
              "button.",
              "Finish until you see a confirmation page; take a screenshot and keep the confirmation email.",
              "Tell me when it's done (or forward the confirmation email) so I can record it and watch the next charge."]
    if phone:
        steps.append(f"Or call {phone} and use the talking points below.")
    notes = [source.get("cancel_notes")] if source.get("cancel_notes") else []
    if source and source.get("verification") != "verified":
        notes.append("The merchant details on file are not verified against the official site yet; double-check the "
                     "link before signing in.")
    return {"subscription_id": sub_id, "name": name, "link": link, "phone": phone, "steps": steps,
            "talking_points": [p.format(name=name) for p in TALKING_POINTS.get(lang, TALKING_POINTS["en"])],
            "notes": notes, "source": "merchant_registry"}


def plan_cancellation(store: Any, guard: LiveGuard, sub_id: str, executor: str, lang: str = "en") -> dict[str, Any]:
    if executor not in CANCEL_EXECUTORS:
        raise CancellationError(f"executor must be one of {', '.join(CANCEL_EXECUTORS)}")
    sub = _subscription(store, sub_id)
    name, merchant = sub["display_name"], sub.get("merchant_id")
    guide = manual_guide(store, guard.registry, sub_id, lang)
    constraints: dict[str, Any] = {"subscription_id": sub_id}
    if merchant:
        constraints["merchant_id"] = merchant
    out: dict[str, Any] = {"subscription_id": sub_id, "merchant_id": merchant, "executor": executor,
                           "cancel_target": None, "draft_content": None, "call_task_steps": None,
                           "manual_guide": guide}
    if executor == "manual" or (sub.get("billing_channel") or "direct") != "direct":
        if executor != "manual":
            out["executor"] = "manual"
            out["note"] = (f"{name} is billed through {sub['billing_channel']}; only the manual guide is available.")
        out.update(plan_steps=[f"Send you the exact steps, link and talking points to cancel {name}",
                               "Record the confirmation when you forward it, then watch the next charge date"],
                   constraints=constraints, live_blockers=[],
                   risk_notes="You cancel it yourself; I don't open, call or send anything.")
        return out
    target, problems = reviewed_target(guard.registry, merchant, executor)
    if target:
        constraints.update(cancel_target=target, target_source="merchant_registry")
    if executor == "browser":
        steps = [f"Open {target or 'the reviewed cancel page'} in the browser; you sign in yourself if asked",
                 "Follow the cancellation flow; if a retention offer appears I record it and ask you, I never accept it",
                 "Stop and hand back to you on a CAPTCHA, a two-factor prompt, a fee or a notice period",
                 "Finish the cancellation and save a screenshot and the confirmation number",
                 "Watch for the confirmation email for 48 hours, then check the next charge date"]
    elif executor == "bland_call":
        out["call_task_steps"] = call_task_steps(name)
        steps = [f"Call {name} at {target or 'the reviewed support number'} (an AI assistant calling on your behalf, "
                 "which it says up front)",
                 "Ask to cancel, decline retention offers, and get a confirmation number",
                 "Save the call transcript as evidence, then watch the next charge date"]
    else:
        out["draft_content"] = email_draft(name, sub.get("account_email"), lang)
        steps = [f"Prepare the cancellation email to {target or 'the reviewed cancel address'} shown below",
                 "You approve the exact email in your own mail app before anything is sent",
                 "Watch for the merchant's confirmation reply for 48 hours, then check the next charge date"]
    probe = {"id": "plan", "action_type": "cancel_subscription", "target_ref": sub_id, "executor": executor,
             "constraints": dict(constraints)}
    guard.annotate(store, probe)
    out.update(plan_steps=steps, constraints=probe["constraints"], cancel_target=target,
               live_blockers=guard.preflight(store, probe),
               risk_notes=("Retention offers are recorded and brought to you, never accepted. "
                           + ("" if target else f"No reviewed {TARGET_FIELD[executor]} on file, so this can only be "
                              "dry-run; use the manual guide. ")).strip())
    if problems and not target:
        out["note"] = "No reviewed target: only a dry run or the manual guide is possible."
    return out


def dry_run(store: Any, guard: LiveGuard, req: Mapping[str, Any]) -> dict[str, Any]:
    """What a run would do and what blocks it. Read-only: no run is started and nothing external is touched."""
    c = req.get("constraints") or {}
    return {"request_id": req["id"], "mode": "dry_run", "status": req["status"],
            "approved": req["status"] == "approved", "live_blockers": guard.preflight(store, req),
            "would_execute": {"executor": req["executor"], "cancel_target": c.get("cancel_target"),
                              "plan_steps": req["plan_steps"], "draft_content": req.get("draft_content")},
            "note": "Dry run: nothing was opened, called, sent or submitted, and no run was started."}
