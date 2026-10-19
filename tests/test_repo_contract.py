"""Repository contract: English skills, agent rules present, no secrets or personal data committed."""
import os
import re

from core import privacy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".venv", ".venv-test", "__pycache__", ".pytest_cache"}


def repo_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            yield os.path.join(base, f)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_skill_files_are_english_only():
    skills = [d for d in os.listdir(os.path.join(ROOT, "skills")) if os.path.isdir(os.path.join(ROOT, "skills", d))]
    assert skills
    for name in skills:
        text = read(f"skills/{name}/SKILL.md")
        assert re.search(r"[\u3400-\u9fff]", text) is None, name


def test_skills_pin_the_confirmation_gate():
    text = read("skills/money-saver-core/SKILL.md")
    for phrase in ("create_action_request", "confirm_action_request", "start_action_run", "explicit confirmation",
                   "untrusted", "Never"):
        assert phrase in text


def test_agents_md_rules():
    text = read("AGENTS.md")
    for phrase in ("Never push to `main`", "Never merge your own PR", "Charles's explicit approval",
                   "sends messages, spends money, or cancels services", "python -m pytest -q"):
        assert phrase in text


def test_no_secrets_or_card_numbers_committed():
    token_rx = re.compile(r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[bap]-[A-Za-z0-9-]{10,})")
    for path in repo_files():
        if path.endswith((".png", ".jpg", ".pdf", ".db")):
            continue
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
        assert not token_rx.search(text), path
        if "/tests/" in path:
            continue  # tests deliberately contain test card numbers to prove redaction
        assert not privacy.find_card_numbers(text), path


def test_fixtures_use_example_recipient_only():
    text = read("tests/fixtures/emails/samples.jsonl") + read("tests/fixtures/emails/holdout.jsonl")
    recipients = set(re.findall(r'"to": "([^"]+)"', text))
    assert recipients == {"alex@example.com"}
