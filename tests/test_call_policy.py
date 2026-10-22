"""Q3 call honesty policy (D30, revised 2026-10-22): disclose up front (locked), truthful if asked, never configurable
to lie."""
import json

import pytest

from core import call_policy

from core.call_policy import (CallPolicy, CallPolicyError, build_call_instructions, load_policy, parse_policy,
                              validate_script)


def test_shipped_policy_defaults():
    p = load_policy()
    assert p.proactive_ai_disclosure is True and p.truthful_if_asked is True
    with open(call_policy.POLICY_PATH, encoding="utf-8") as fh:
        assert json.load(fh)["proactive_ai_disclosure"] is True
    assert CallPolicy().proactive_ai_disclosure is True
    assert parse_policy({}).proactive_ai_disclosure is True   # loader default


def test_instructions_disclose_up_front_and_answer_truthfully():
    out = build_call_instructions("Alex")
    assert out["opening"] == "Hi, this is an AI assistant calling on behalf of Alex about their account."
    assert out["truthful_answer"] == "Yes, I'm an AI assistant calling on behalf of Alex."
    text = " ".join(out["rules"])
    assert "first sentence" in text and "AI assistant" in text
    assert "answer truthfully" in text and "Never claim to be human" in text and "account holder" in text
    assert out["truthful_if_asked"] is True and out["proactive_ai_disclosure"] is True


def test_zh_discloses_up_front():
    zh = build_call_instructions("Alex", lang="zh")
    assert zh["opening"].startswith("您好，我是代表 Alex 致电的 AI 助手")
    assert "是的，我是代表 Alex 致电的 AI 助手" in zh["truthful_answer"]


@pytest.mark.parametrize("value", [False, None, 0, 1, "true", "false", "no"])
def test_disclosure_cannot_be_disabled_in_policy_file(value, tmp_path):
    with pytest.raises(CallPolicyError):
        parse_policy({"proactive_ai_disclosure": value, "if_asked_whether_ai": "answer_truthfully"})
    f = tmp_path / "policy.json"
    f.write_text(json.dumps({"proactive_ai_disclosure": value, "if_asked_whether_ai": "answer_truthfully"}))
    with pytest.raises(CallPolicyError):
        load_policy(str(f))


def test_disclosure_cannot_be_disabled_in_code():
    with pytest.raises(CallPolicyError):
        CallPolicy(proactive_ai_disclosure=False)


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
    "Hi, this is Sam speaking.",
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
    opening = "Hi, this is an AI\nassistant calling on behalf of <user> about their account."
    assert "disclose up front" in skill and opening in skill and "never claim to be human" in skill.lower()
    assert "Do not volunteer" not in skill
    readme = read("config", "call_scripts", "README.md")
    assert "Disclose up front, always" in readme and "proactive_ai_disclosure: true" in readme
    assert "Truthful if asked" in readme and "No proactive AI disclosure" not in readme
    decisions = read("docs", "decisions.md")
    assert "D30" in decisions and "2026-10-22" in decisions and "supersedes" in decisions
    assert "proactive_ai_disclosure: false` (the default" not in decisions
    spec = read("docs", "spec.md")
    assert "不主动声明 AI 身份（Q3" not in spec and "开场就必须主动声明自己是 AI" in spec
    assert "我不会主动说明自己是 AI" not in spec
    plan = read("docs", "plan.md")
    assert "no proactive AI disclosure" not in plan and "disclose up front" in plan
