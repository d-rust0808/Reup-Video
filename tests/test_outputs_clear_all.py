"""Clear-all in the output gallery must also wipe orphan .mp4 files (no DB row),
otherwise they reappear on the next app start via the OUTPUT_DIR scan in list_outputs()."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.api.outputs import clear_all_outputs, list_outputs
from app.config import settings


class _FakeQM:
    def __init__(self):
        self.deleted = []

    def list_jobs(self, status_filter=None):
        return []

    def get_job(self, job_id):
        return None

    def delete_job(self, job_id):
        self.deleted.append(job_id)
        return True


class _FakeState:
    pass


class _FakeApp:
    def __init__(self, qm):
        self.state = _FakeState()
        self.state.queue_manager = qm


class _FakeRequest:
    def __init__(self, qm):
        self.app = _FakeApp(qm)


def test_clear_all_removes_orphan_platform_exports(tmp_path, monkeypatch):
    out_dir = tmp_path / "outputs"
    out_dir.mkdir()
    names = [
        "job_9651f510.facebook.mp4",
        "job_9651f510.tiktok.mp4",
        "job_9651f510.youtube_shorts.mp4",
        "job_e42cfd0d_stage2.mp4",
    ]
    for n in names:
        (out_dir / n).write_bytes(b"x")
    keep = out_dir / "job_5bae1f78.vi.srt"
    keep.write_text("subtitle")

    monkeypatch.setattr(settings, "OUTPUT_DIR", str(out_dir))
    req = _FakeRequest(_FakeQM())

    res = asyncio.run(clear_all_outputs(req))

    assert res["deleted_count"] == len(names)
    assert sorted(os.listdir(out_dir)) == [keep.name]

    # And the gallery is empty afterwards, so nothing comes back on reload.
    listed = asyncio.run(list_outputs(_FakeRequest(_FakeQM())))
    assert listed["outputs"] == []
