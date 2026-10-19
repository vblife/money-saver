from core.evidence import EvidenceStore


def test_file_evidence_is_copied_and_hashed(store, tmp_path):
    src = tmp_path / "shot.png"
    src.write_bytes(b"\x89PNG fake")
    ev_store = EvidenceStore(store, root=str(tmp_path / "evidence"))
    ev = ev_store.add_file("screenshot", str(src), "sub_hulu", "cancel confirmation page")
    assert ev["path_or_ref"].startswith(str(tmp_path / "evidence" / "sub" / "sub_hulu"))
    assert len(ev["sha256"]) == 64 and ev_store.verify(ev["id"])
    with open(ev["path_or_ref"], "ab") as fh:
        fh.write(b"tamper")
    assert not ev_store.verify(ev["id"])


def test_email_evidence_stores_reference_and_redacted_snippet_only(store, tmp_path):
    ev = EvidenceStore(store, root=str(tmp_path)).add_email(
        "18c1f", "sub_hulu", "Cancelled. Card 4111 1111 1111 1111 will not be charged. " + "x" * 1000)
    assert ev["path_or_ref"] == "gmail:18c1f"
    assert "4111 1111" not in ev["notes"] and len(ev["notes"]) <= 280


def test_wipe_files(store, tmp_path):
    src = tmp_path / "t.txt"
    src.write_text("transcript")
    es = EvidenceStore(store, root=str(tmp_path / "ev"))
    es.add_file("call_transcript", str(src), "run_1")
    es.wipe_files()
    assert not (tmp_path / "ev").exists()
