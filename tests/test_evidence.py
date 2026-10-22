import os

import pytest

from core.evidence import EvidencePathError, EvidenceStore


@pytest.fixture
def inbox(tmp_path):
    d = tmp_path / "inbox"
    d.mkdir()
    return d


@pytest.fixture
def es(store, tmp_path, inbox):
    return EvidenceStore(store, root=str(tmp_path / "evidence"), allowed_dirs=[str(inbox)])


def test_file_evidence_is_copied_and_hashed(es, inbox, tmp_path):
    src = inbox / "shot.png"
    src.write_bytes(b"\x89PNG fake")
    ev = es.add_file("screenshot", str(src), "sub_hulu", "cancel confirmation page")
    assert ev["path_or_ref"].startswith(str(tmp_path / "evidence" / "sub" / "sub_hulu"))
    assert len(ev["sha256"]) == 64 and es.verify(ev["id"])
    with open(ev["path_or_ref"], "ab") as fh:
        fh.write(b"tamper")
    assert not es.verify(ev["id"])


def test_nested_inbox_file_is_allowed(es, inbox):
    (inbox / "calls").mkdir()
    src = inbox / "calls" / "t.txt"
    src.write_text("transcript")
    assert es.add_file("call_transcript", str(src), "run_1")["kind"] == "call_transcript"


# --- regression: record_evidence must not copy arbitrary files (review non-blocking, D14) -----------
def test_rejects_file_outside_inbox(es, tmp_path):
    secret = tmp_path / ".ssh" / "id_rsa"
    secret.parent.mkdir()
    secret.write_text("-----BEGIN OPENSSH PRIVATE KEY-----")
    with pytest.raises(EvidencePathError):
        es.add_file("screenshot", str(secret), "sub_x")
    assert es.store.count("evidence") == 0


def test_rejects_home_ssh_key_path(es):
    with pytest.raises((EvidencePathError, FileNotFoundError)):
        es.add_file("screenshot", "~/.ssh/id_rsa", "sub_x")


def test_rejects_dotdot_traversal(es, inbox, tmp_path):
    outside = tmp_path / "token.txt"
    outside.write_text("ghs_secret")
    with pytest.raises(EvidencePathError):
        es.add_file("form_receipt", str(inbox / ".." / "token.txt"), "clm_1")


def test_rejects_symlink_escaping_inbox(es, inbox, tmp_path):
    outside = tmp_path / "creds.json"
    outside.write_text("{}")
    link = inbox / "innocent.png"
    os.symlink(outside, link)
    with pytest.raises(EvidencePathError):
        es.add_file("screenshot", str(link), "sub_x")


def test_rejects_symlink_even_inside_inbox(es, inbox):
    real = inbox / "real.png"
    real.write_bytes(b"x")
    link = inbox / "link.png"
    os.symlink(real, link)
    with pytest.raises(EvidencePathError):
        es.add_file("screenshot", str(link), "sub_x")


def test_rejects_symlinked_directory_escaping_inbox(es, inbox, tmp_path):
    outside_dir = tmp_path / "private"
    outside_dir.mkdir()
    (outside_dir / "key").write_text("secret")
    os.symlink(outside_dir, inbox / "sub")
    with pytest.raises(EvidencePathError):
        es.add_file("screenshot", str(inbox / "sub" / "key"), "sub_x")


def test_rejects_directories_and_missing_files(es, inbox):
    with pytest.raises(EvidencePathError):
        es.add_file("screenshot", str(inbox), "sub_x")
    with pytest.raises(FileNotFoundError):
        es.add_file("screenshot", str(inbox / "missing.png"), "sub_x")


def test_default_inbox_comes_from_env(store, tmp_path, monkeypatch):
    monkeypatch.setenv("MSB_EVIDENCE_INBOX", str(tmp_path / "a") + os.pathsep + str(tmp_path / "b"))
    es = EvidenceStore(store, root=str(tmp_path / "ev"))
    assert es.allowed_dirs == [os.path.realpath(tmp_path / "a"), os.path.realpath(tmp_path / "b")]
    monkeypatch.delenv("MSB_EVIDENCE_INBOX")
    monkeypatch.setenv("MSB_HOME", str(tmp_path / "home"))
    assert EvidenceStore(store).allowed_dirs == [os.path.realpath(tmp_path / "home" / "inbox")]


def test_email_evidence_stores_reference_and_redacted_snippet_only(store, tmp_path):
    ev = EvidenceStore(store, root=str(tmp_path)).add_email(
        "18c1f", "sub_hulu", "Cancelled. Card 4111 1111 1111 1111 will not be charged. " + "x" * 1000)
    assert ev["path_or_ref"] == "gmail:18c1f"
    assert "4111 1111" not in ev["notes"] and len(ev["notes"]) <= 280


def test_wipe_files(es, inbox, tmp_path):
    src = inbox / "t.txt"
    src.write_text("transcript")
    es.add_file("call_transcript", str(src), "run_1")
    es.wipe_files()
    assert not (tmp_path / "evidence").exists()


# --- regression: hard links and check/copy races (re-review 4) ---------------------------------------
def test_rejects_hardlink_to_outside_file(es, inbox, tmp_path):
    outside = tmp_path / "secret.key"
    outside.write_text("PRIVATE")
    os.link(outside, inbox / "looks_fine.png")
    with pytest.raises(EvidencePathError):
        es.add_file("screenshot", str(inbox / "looks_fine.png"), "sub_x")
    assert es.store.count("evidence") == 0


def test_rejects_hardlink_even_inside_inbox(es, inbox):
    (inbox / "a.png").write_bytes(b"x")
    os.link(inbox / "a.png", inbox / "b.png")
    with pytest.raises(EvidencePathError):
        es.add_file("screenshot", str(inbox / "b.png"), "sub_x")


def _swap_after_check(monkeypatch, es, swap):
    original = es._check

    def checked_then_swapped(path):
        result = original(path)
        swap()
        return result
    monkeypatch.setattr(es, "_check", checked_then_swapped)


def test_race_swap_to_symlink_after_check_is_rejected(es, inbox, tmp_path, monkeypatch):
    target, outside = inbox / "shot.png", tmp_path / "outside.txt"
    target.write_bytes(b"png")
    outside.write_text("SECRET")

    def swap():
        target.unlink()
        os.symlink(outside, target)
    _swap_after_check(monkeypatch, es, swap)
    with pytest.raises(EvidencePathError):
        es.add_file("screenshot", str(target), "sub_x")
    assert es.store.count("evidence") == 0


def test_race_swap_to_hardlink_after_check_is_rejected(es, inbox, tmp_path, monkeypatch):
    target, outside = inbox / "shot.png", tmp_path / "outside.txt"
    target.write_bytes(b"png")
    outside.write_text("SECRET")

    def swap():
        target.unlink()
        os.link(outside, target)
    _swap_after_check(monkeypatch, es, swap)
    with pytest.raises(EvidencePathError):
        es.add_file("screenshot", str(target), "sub_x")


def test_race_swap_to_other_file_after_check_is_rejected(es, inbox, monkeypatch):
    target = inbox / "shot.png"
    target.write_bytes(b"png")

    def swap():
        target.rename(inbox / "old.png")  # keep the old inode alive so the new file gets a new one
        target.write_bytes(b"different file, new inode")
    _swap_after_check(monkeypatch, es, swap)
    with pytest.raises(EvidencePathError):
        es.add_file("screenshot", str(target), "sub_x")


def test_copy_is_made_from_opened_fd_and_hash_matches(es, inbox):
    src = inbox / "receipt.pdf"
    src.write_bytes(b"%PDF fake" * 1000)
    ev = es.add_file("form_receipt", str(src), "clm_1")
    with open(ev["path_or_ref"], "rb") as fh:
        assert fh.read() == src.read_bytes()
    assert es.verify(ev["id"])
    assert oct(os.stat(ev["path_or_ref"]).st_mode & 0o777) == "0o600"


def test_rejects_fifo(es, inbox):
    os.mkfifo(inbox / "pipe")
    with pytest.raises(EvidencePathError):
        es.add_file("screenshot", str(inbox / "pipe"), "sub_x")
