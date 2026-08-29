from app.api.stream import (
    _iter_file_range,
    _parse_byte_range,
    _srt_to_webvtt,
    prefer_vertical_output_path,
)


def test_prefer_vertical_output_picks_tiktok_over_master(tmp_path):
    master = tmp_path / "job_abc.mp4"
    tiktok = tmp_path / "job_abc.tiktok.mp4"
    master.write_bytes(b"master")
    tiktok.write_bytes(b"tiktok-9x16")
    picked = prefer_vertical_output_path("job_abc", out_dir=str(tmp_path))
    assert picked == str(tiktok)
    assert prefer_vertical_output_path("job_abc.tiktok", out_dir=str(tmp_path)) is None
    assert prefer_vertical_output_path("job_missing", out_dir=str(tmp_path)) is None


def test_stream_resolve_does_not_silently_swap_master(tmp_path, monkeypatch):
    from app.api import stream as stream_mod
    from app.config import settings

    master = tmp_path / "job_abc.mp4"
    tiktok = tmp_path / "job_abc.tiktok.mp4"
    master.write_bytes(b"master-file")
    tiktok.write_bytes(b"tiktok-9x16")
    monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "RAW_INPUT_DIR", str(tmp_path / "raw"), raising=False)
    monkeypatch.setattr(settings, "PREVIEW_DIR", str(tmp_path / "prev"), raising=False)
    (tmp_path / "raw").mkdir()
    (tmp_path / "prev").mkdir()
    resolved = stream_mod._resolve_media_file_path("job_abc")
    assert resolved == str(master)
    variant = stream_mod._resolve_media_file_path("job_abc.tiktok")
    assert variant == str(tiktok)


def test_open_ended_video_range_reads_to_eof():
    file_size = 20 * 1024 * 1024

    start, end = _parse_byte_range("bytes=0-", file_size)

    assert start == 0
    assert end == file_size - 1
    assert _parse_byte_range("bytes=10-", file_size) == (10, file_size - 1)


def test_explicit_and_suffix_video_ranges_are_supported():
    assert _parse_byte_range("bytes=10-19", 100) == (10, 19)
    assert _parse_byte_range("bytes=-10", 100) == (90, 99)
    assert _parse_byte_range("bytes=95-999", 100) == (95, 99)


def test_file_range_iterator_yields_only_requested_bytes(tmp_path):
    media = tmp_path / "sample.mp4"
    media.write_bytes(bytes(range(100)))

    payload = b"".join(_iter_file_range(str(media), 10, 19))

    assert payload == bytes(range(10, 20))


def test_srt_to_webvtt_for_browser_cc():
    payload = _srt_to_webvtt(
        "1\r\n00:00:00,000 --> 00:00:01,250\r\nXin chào\r\n"
    )

    assert payload.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:01.250 line:-2 align:center size:94%" in payload
    assert "Xin chào" in payload
