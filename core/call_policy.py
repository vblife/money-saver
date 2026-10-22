"""Call honesty policy (Q3, decided by Charles 2026-10-22; D30).

* Calls do NOT proactively say they are an AI (configurable, off by default).
* If the other party directly asks whether they are talking to an AI, the answer is always truthful: yes, an AI
  assistant calling on behalf of the user. The caller never claims to be human and never claims to be the user.
  This half is not configurable: a policy that tries to change it fails to load.

Every call script must pass :func:`validate_script`. :func:`build_call_instructions` produces the rules that go at
the top of every Bland prompt or pathway. This module places no calls.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from .util import CONFIG_DIR

POLICY_PATH = os.path.join(CONFIG_DIR, "call_scripts", "policy.json")
ALLOWED_KEYS = {"version", "note", "proactive_ai_disclosure", "if_asked_whether_ai"}
TRUTHFUL = "answer_truthfully"

# Statements a script may never make (English and Chinese). Case-insensitive.
FORBIDDEN_CLAIMS = [
    r"\bi(?:'m| am)\s+(?:a\s+)?(?:real\s+)?(?:human|person|real person)\b",
    r"\bi(?:'m| am)\s+not\s+(?:an?\s+)?(?:ai|bot|robot|machine|computer|recording|automated)\b",
    r"\bnot\s+an?\s+(?:ai|bot|robot)\b",
    r"\bthis is (?:a\s+)?(?:real\s+)?(?:human|person)\b",
    r"\b(?:deny|never admit|don't admit|do not admit|pretend)\b[^.\n]{0,40}\b(?:ai|bot|human|robot)\b",
    r"\bi(?:'m| am)\s+(?:the\s+)?(?:account\s*holder|account\s*owner)\b",
    r"\bthis is \{\{?\s*user(?:_name)?\s*\}?\}\s+(?:speaking|calling)\b",
    r"我是真人|我不是\s*AI|我不是机器人|我是本人|我就是账户持有人|假装(?:是)?(?:真人|人类)",
]
_FORBIDDEN = [re.compile(p, re.I) for p in FORBIDDEN_CLAIMS]

TRUTHFUL_ANSWER = {
    "en": "Yes, I'm an AI assistant calling on behalf of {user_name}.",
    "zh": "是的，我是代表 {user_name} 致电的 AI 助手。",
}
OPENING = {
    "en": {False: "Hi, I'm calling on behalf of {user_name} about their account.",
           True: "Hi, I'm an AI assistant calling on behalf of {user_name} about their account."},
    "zh": {False: "您好，我代表 {user_name} 来电，咨询其账户相关事宜。",
           True: "您好，我是代表 {user_name} 致电的 AI 助手，咨询其账户相关事宜。"},
}


class CallPolicyError(ValueError):
    """The policy or a script would allow a dishonest answer."""


@dataclass(frozen=True)
class CallPolicy:
    proactive_ai_disclosure: bool = False
    # Fixed by design (not a constructor argument): asked -> truthful; never claim to be human or the user.
    truthful_if_asked: bool = True

    def __post_init__(self):
        if self.truthful_if_asked is not True:
            raise CallPolicyError("truthful_if_asked cannot be turned off (Q3)")


def parse_policy(doc: Any) -> CallPolicy:
    if not isinstance(doc, dict):
        raise CallPolicyError("call policy must be a JSON object")
    unknown = set(doc) - ALLOWED_KEYS
    if unknown:
        raise CallPolicyError(f"unknown call policy keys (refusing to load): {sorted(unknown)}")
    asked = doc.get("if_asked_whether_ai", TRUTHFUL)
    if asked != TRUTHFUL:
        raise CallPolicyError(f"if_asked_whether_ai must be '{TRUTHFUL}'; denying being an AI is never allowed")
    proactive = doc.get("proactive_ai_disclosure", False)
    if not isinstance(proactive, bool):
        raise CallPolicyError("proactive_ai_disclosure must be true or false")
    return CallPolicy(proactive_ai_disclosure=proactive)


def load_policy(path: str | None = None) -> CallPolicy:
    with open(path or POLICY_PATH, encoding="utf-8") as fh:
        return parse_policy(json.load(fh))


def validate_script(text: str) -> None:
    """Reject any script text that claims to be human, denies being an AI, or claims to be the user."""
    if not isinstance(text, str) or not text.strip():
        raise CallPolicyError("call script is empty")
    for rx in _FORBIDDEN:
        m = rx.search(text)
        if m:
            raise CallPolicyError(f"call script makes a forbidden claim: {m.group(0)!r}")


def build_call_instructions(user_name: str, policy: CallPolicy | None = None, lang: str = "en") -> dict[str, Any]:
    """Opening line + the honesty rules to put at the top of every call prompt/pathway."""
    policy = policy or load_policy()
    lang = lang if lang in OPENING else "en"
    opening = OPENING[lang][policy.proactive_ai_disclosure].format(user_name=user_name)
    truthful = TRUTHFUL_ANSWER[lang].format(user_name=user_name)
    rules = [
        f"Open with: \"{opening}\"",
        ("Do not volunteer that you are an AI unless asked." if not policy.proactive_ai_disclosure
         else "Say you are an AI assistant in the opening."),
        f"If anyone asks whether you are an AI, a bot, a robot, a recording, or a real person, answer truthfully: \"{truthful}\"",
        "Never claim to be human, and never claim to be the account holder or the user. You are calling on their behalf.",
        "If they will not continue with an AI assistant, end politely and report back. Do not argue or mislead.",
    ]
    for r in rules:
        validate_script(r)
    return {"opening": opening, "truthful_answer": truthful, "rules": rules,
            "proactive_ai_disclosure": policy.proactive_ai_disclosure, "truthful_if_asked": True}
