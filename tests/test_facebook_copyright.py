from app.services.facebook_copyright import evaluate_copyright, summarize_matches


def test_copyright_in_progress_is_pending():
    verdict = evaluate_copyright({
        "copyright_check_information": {"status": {"status": "in_progress"}},
    })
    assert verdict.state == "pending"
    assert verdict.pending


def test_copyright_complete_without_matches_is_clear():
    verdict = evaluate_copyright({
        "copyright_check_information": {
            "status": {"status": "complete", "matches_found": False},
        },
    })
    assert verdict.state == "clear"
    assert not verdict.matches_found


def test_copyright_block_match_is_blocked():
    verdict = evaluate_copyright({
        "copyright_check_information": {
            "status": {"status": "complete", "matches_found": True},
            "copyright_matches": [
                {
                    "content_title": "Phim cung đình",
                    "owner_copyright_policy": {
                        "name": "Studio A",
                        "actions": [{"action": "BLOCK", "geos": ["Vietnam"]}],
                    },
                    "matched_segments": [
                        {"start_time_in_seconds": 2, "duration_in_seconds": 40, "segment_type": "VIDEO"},
                    ],
                }
            ],
        },
    })
    assert verdict.blocked
    assert "Phim cung đình" in verdict.summary
    assert "BLOCK" in verdict.summary
    assert "VIDEO" in verdict.summary


def test_copyright_track_only_still_blocks_publish():
    verdict = evaluate_copyright({
        "copyright_check_status": {
            "status": "complete",
            "matches_found": True,
            "copyright_matches": [
                {
                    "content_title": "Bài hát",
                    "author": "Label",
                    "action": "TRACK",
                    "matched_segments": [{"segment_type": "AUDIO"}],
                }
            ],
        },
    })
    assert verdict.blocked
    assert "Bài hát" in verdict.summary


def test_copyright_missing_field_is_unknown():
    verdict = evaluate_copyright({"id": "1", "status": {"video_status": "ready"}})
    assert verdict.state == "unknown"


def test_summarize_matches_caps_at_three():
    matches = [{"content_title": f"T{i}", "action": "BLOCK"} for i in range(5)]
    text = summarize_matches(matches)
    assert "T0" in text and "T2" in text
    assert "T3" not in text


def test_skip_copyright_when_no_facebook_page(tmp_path):
    from app.core.database import init_db
    from app.services.facebook_copyright import assert_source_copyright_clear

    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    src = tmp_path / "src.mp4"
    src.write_bytes(b"x" * 8000)
    verdict = assert_source_copyright_clear(str(src), db_path=db, job_id="job_x")
    assert verdict.state == "unknown"
    assert "Fanpage" in verdict.summary


def test_assert_source_copyright_clear_raises_on_match(tmp_path, monkeypatch):
    import pytest
    from app.services.facebook_copyright import (
        CopyrightBlockedError,
        assert_source_copyright_clear,
    )

    src = tmp_path / "src.mp4"
    src.write_bytes(b"x" * 8000)
    monkeypatch.setattr(
        "app.services.facebook_copyright.build_copyright_probe",
        lambda *a, **k: str(src),
    )
    monkeypatch.setattr(
        "app.services.facebook_copyright.list_connected_publish_pages",
        lambda _db: [{
            "page_id": "page_1",
            "name": "Review",
            "page_token_ref": "tok.ref",
            "graph_version": "v24.0",
        }],
    )
    monkeypatch.setattr("app.services.secret_store.get_secret", lambda _ref: "page-token")

    class FakeFacebookClient:
        def __init__(self, graph_version, timeout=30.0):
            self.deleted = []

        def close(self):
            pass

        def start_reel(self, page_id, token):
            return {"video_id": "probe_video", "upload_url": "https://upload.invalid"}

        def upload_reel_binary(self, upload_url, token, source_path):
            return {"success": True}

        def finish_reel(self, page_id, token, video_id, description, video_state="PUBLISHED"):
            assert video_state == "DRAFT"
            return {"success": True}

        def get_video_status(self, video_id, token):
            return {
                "id": video_id,
                "copyright_check_information": {
                    "status": {"status": "complete", "matches_found": True},
                    "copyright_matches": [{
                        "content_title": "Phim cung đình",
                        "action": "BLOCK",
                        "matched_segments": [{"segment_type": "VIDEO"}],
                    }],
                },
            }

        def delete_object(self, object_id, token):
            self.deleted.append(object_id)
            return {"success": True}

    monkeypatch.setattr("app.services.facebook_client.FacebookClient", FakeFacebookClient)
    with pytest.raises(CopyrightBlockedError, match="Phim cung đình"):
        assert_source_copyright_clear(str(src), db_path="unused.sqlite", job_id="job_1")


def test_pipeline_does_not_reup_when_source_is_copyrighted(tmp_path, monkeypatch):
    from app.services.facebook_copyright import CopyrightBlockedError
    from app.services.queue_manager import BatchQueueManager

    src = tmp_path / "src.mp4"
    src.write_bytes(b"x" * 8000)
    out = tmp_path / "out.mp4"
    manager = BatchQueueManager(db_path=str(tmp_path / "jobs.sqlite"), max_concurrent_jobs=1)
    reup_called = {"n": 0}

    def _no_reup(*_a, **_k):
        reup_called["n"] += 1
        raise AssertionError("reup must not run after copyright block")

    monkeypatch.setattr(
        "app.services.queue_manager.assert_source_copyright_clear",
        lambda *a, **k: (_ for _ in ()).throw(
            CopyrightBlockedError("Trùng bản quyền: Phim cung đình — BLOCK [VIDEO]")
        ),
    )
    monkeypatch.setattr("app.services.queue_manager.process_reup_video", _no_reup)
    try:
        job_id = manager.enqueue_job(str(src), str(out))
        manager._claim_pending_job(job_id)
        manager._run_pipeline_stages(job_id)
        job = manager.get_job(job_id)
        assert job["status"] == "FAILED"
        assert "bản quyền" in (job.get("error_message") or "").lower()
        assert reup_called["n"] == 0
        logs = " ".join(str(item.get("message") or item) for item in (job.get("logs") or []))
        assert "Dính bản quyền" in logs or "bản quyền" in logs.lower()
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)
