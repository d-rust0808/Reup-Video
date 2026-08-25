from app.api.stream import (
    MAX_OPEN_ENDED_RANGE_SIZE,
    _iter_file_range,
    _parse_byte_range,
    _srt_to_webvtt,
)


def test_open_ended_video_range_is_capped():
    file_size = 20 * 1024 * 1024

    start, end = _parse_byte_range("bytes=0-", file_size)

    assert start == 0
    assert end == MAX_OPEN_ENDED_RANGE_SIZE - 1


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
    assert "00:00:00.000 --> 00:00:01.250" in payload
    assert "Xin chào" in payload
