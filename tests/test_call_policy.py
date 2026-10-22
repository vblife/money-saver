"""Q3 call honesty policy (D30): no proactive disclosure by default, truthful if asked, never configurable to lie."""
import json

import pytest

from core.call_policy import (CallPolicy, CallPolicyError, build_call_instructions, load_policy, parse_policy,
                              validate_script)


def test_shipped_policy_defaults():
    p = load_policy()
    assert p.proactive_ai_disclosure is False and p.truthful_if_asked is True


def test_default_instructions_do_not_volunteer_but_answer_truthfully():
    out = build_call_instructions("Alex")
    assert out["opening"] == "Hi, I'm calling on behalf of Alex about their account."
    assert "AI" not in out["opening"]
    assert out["truthful_answer"] == "Yes, I'm an AI assistant calling on behalf of Alex."
    text = " ".join(out["rules"])
    assert "answer truthfully" in text and "Never claim to be human" in text and "account holder" in text
    assert out["truthful_if_asked"] is True


def test_opt_in_disclosure_and_zh():
    out = build_call_instructions("Alex", CallPolicy(proactive_ai_disclosure=True))
    assert "AI assistant" in out["opening"]
    zh = build_call_instructions("Alex", lang="zh")
    assert "AI" not in zh["opening"] and "是的，我是代表 Alex 致电的 AI 助手" in zh["truthful_answer"]


@pytest.mark.parametrize("doc", [
    {"if_asked_whether_ai": "deny"},
    {"if_asked_whether_ai": "deflect"},
    {"if_asked_whether_ai": None},
    {"if_asked_whether_ai": False},
    {"claim_human": True},
    {"allow_deny_ai": True},
    {"truthful_if_asked": False},
    {"proactive_ai_disclosure": "no"},
    [],
])
def test_policy_cannot_be_configured_to_lie(doc):
    with pytest.raises(CallPolicyError):
        parse_policy(doc)


def test_truthful_flag_cannot_be_disabled_in_code():
    with pytest.raises(CallPolicyError):
        CallPolicy(truthful_if_asked=False)


def test_bad_policy_file_fails_to_load(tmp_path):
    f = tmp_path / "policy.json"
    f.write_text(json.dumps({"proactive_ai_disclosure": False, "if_asked_whether_ai": "deny"}))
    with pytest.raises(CallPolicyError):
        load_policy(str(f))


@pytest.mark.parametrize("script", [
    "If asked, say: I'm a real person.",
    "Say I am human if they ask.",
    "If they ask, reply: I'm not an AI.",
    "No, I am not a bot.",
    "Tell them this is a real person calling.",
    "Never admit you are an AI.",
    "Pretend to be a human.",
    "I am the account holder.",
    "Hi, this is {user_name} speaking.",
    "如果被问，就说我是真人。",
    "我不是 AI。",
    "就说我是本人。",
])
def test_scripts_with_dishonest_claims_are_rejected(script):
    with pytest.raises(CallPolicyError):
        validate_script(script)


@pytest.mark.parametrize("script", [
    "Hi, I'm calling on behalf of Alex about their account.",
    "Yes, I'm an AI assistant calling on behalf of Alex.",
    "Please confirm the cancellation number and send a confirmation email.",
    "I'd like to cancel the subscription on this account.",
])
def test_honest_scripts_pass(script):
    validate_script(script)


def test_empty_script_rejected():
    with pytest.raises(CallPolicyError):
        validate_script("  ")


def test_module_places_no_calls():
    import core.call_policy as m
    src = open(m.__file__, encoding="utf-8").read()
    for banned in ("import socket", "import urllib", "import http", "import subprocess", "import requests"):
        assert banned not in src


def test_docs_and_skill_match_the_decision():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    read = lambda *p: open(os.path.join(root, *p), encoding="utf-8").read()
    skill = read("skills", "money-saver-core", "SKILL.md")
    assert "always say you are an AI" not in skill
    assert "never claim to be human" in skill.lower()
    readme = read("config", "call_scripts", "README.md")
    assert "must open with an AI-assistant disclosure" not in readme
    assert "Truthful if asked" in readme
    decisions = read("docs", "decisions.md")
    assert "D30" in decisions and "2026-10-22" in decisions
