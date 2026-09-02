from datetime import datetime, timezone

from app.core.database import get_db_connection, init_db
from app.services.channel_growth import (
    collect_page_growth,
    fill_series,
    growth_overview,
    insight_day,
    merge_media,
    parse_insights,
    persist_growth,
    posts_to_daily,
    refresh_channel,
    summarize_flow,
    summarize_stock,
    upsert_snapshot,
)
from app.services.facebook_client import FacebookAPIError


def test_insight_day_shifts_facebook_end_time():
    assert insight_day("2026-08-31T07:00:00+0000") == "2026-08-30"
    assert insight_day("2026-09-01T07:00:00+0000") == "2026-08-31"
    assert insight_day("") == ""


def test_parse_insights_and_posts_to_daily():
    payload = {
        "data": [
            {
                "name": "page_fans",
                "values": [{"value": 1200, "end_time": "2026-09-02T07:00:00+0000"}],
            },
            {
                "name": "page_video_views",
                "values": [{"value": 80, "end_time": "2026-09-02T07:00:00+0000"}],
            },
        ]
    }
    daily = parse_insights(payload)
    assert daily["2026-09-01"]["fans"] == 1200
    assert daily["2026-09-01"]["views"] == 80

    posts = [
        {
            "id": "p1",
            "created_time": "2026-09-01T10:00:00+0000",
            "message": "A",
            "likes": {"summary": {"total_count": 12}},
            "comments": {"summary": {"total_count": 3}},
        },
        {
            "id": "v1",
            "created_time": "2026-09-01T11:00:00+0000",
            "title": "reel",
            "views": 400,
            "likes": {"summary": {"total_count": 20}},
            "comments": {"summary": {"total_count": 4}},
        },
    ]
    media = merge_media(posts)
    assert {row["post_id"] for row in media} == {"p1", "v1"}
    by_day = posts_to_daily(media)
    assert by_day["2026-09-01"]["likes"] == 32
    assert by_day["2026-09-01"]["comments"] == 7
    assert by_day["2026-09-01"]["views"] == 400
    assert by_day["2026-09-01"]["posts"] == 2


def test_fill_series_carry_and_summaries():
    series = fill_series({"2026-09-01": 100, "2026-09-03": 130}, ["2026-09-01", "2026-09-02", "2026-09-03"], carry=True)
    assert [p["value"] for p in series] == [100, 100, 130]
    stock = summarize_stock(series)
    assert stock["current"] == 130
    assert stock["delta"] == 30
    assert stock["delta_pct"] == 30.0
    backfilled = fill_series({"2026-09-03": 130}, ["2026-09-01", "2026-09-02", "2026-09-03"], carry=True)
    assert [p["value"] for p in backfilled] == [130, 130, 130]
    assert summarize_stock(backfilled)["delta"] == 0

    flow = summarize_flow(
        [{"date": "d1", "value": 10}, {"date": "d2", "value": 20}],
        [{"date": "p1", "value": 5}, {"date": "p2", "value": 5}],
    )
    assert flow["total"] == 30
    assert flow["delta"] == 20


def test_upsert_snapshot_does_not_zero_followers(tmp_path):
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    with get_db_connection(db) as conn:
        upsert_snapshot(conn, "chan_a", "2026-09-01", page_id="p1", followers=1000, fans=900, likes=10, source="insights")
        upsert_snapshot(conn, "chan_a", "2026-09-01", page_id="p1", followers=0, fans=0, likes=12, source="posts")
        row = dict(conn.execute("SELECT * FROM channel_growth_snapshots").fetchone())
    assert row["followers"] == 1000
    assert row["fans"] == 900
    assert row["likes"] == 12


class FakeClient:
    def get_page_profile(self, page_id, token):
        return {"name": "Review Phim", "fan_count": 12500, "followers_count": 13000}

    def get_page_insights(self, page_id, token, metrics, **kwargs):
        names = set(metrics)
        if "page_follows" in names:
            raise FacebookAPIError("metric not available")
        data = []
        if "page_fans" in names:
            data.append({
                "name": "page_fans",
                "values": [
                    {"value": 12000, "end_time": "2026-09-01T07:00:00+0000"},
                    {"value": 12500, "end_time": "2026-09-02T07:00:00+0000"},
                ],
            })
        if "page_video_views" in names:
            data.append({
                "name": "page_video_views",
                "values": [
                    {"value": 800, "end_time": "2026-09-01T07:00:00+0000"},
                    {"value": 1200, "end_time": "2026-09-02T07:00:00+0000"},
                ],
            })
        return {"data": data}

    def list_published_posts(self, page_id, token, limit=80):
        return [{
            "id": "p1",
            "created_time": "2026-09-01T10:00:00+0000",
            "message": "Tóm tắt tập 3",
            "permalink_url": "https://facebook.com/p1",
            "likes": {"summary": {"total_count": 40}},
            "comments": {"summary": {"total_count": 9}},
        }]

    def list_page_videos(self, page_id, token, limit=80):
        return [{
            "id": "v1",
            "created_time": "2026-09-01T12:00:00+0000",
            "title": "Reel",
            "views": 900,
            "likes": {"summary": {"total_count": 22}},
            "comments": {"summary": {"total_count": 4}},
        }]

    def close(self):
        pass


def _seed_channel(db):
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection(db) as conn:
        conn.execute(
            """INSERT INTO channels (channel_id, name, platform, handle, tags, description, color, overlays, status, created_at, updated_at)
               VALUES ('chan_fb', 'Review Phim', 'facebook', 'reviewphim', '[]', '', 'blue', '[]', 'ACTIVE', ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO facebook_connections (id, app_id, graph_version, user_id, user_name, scopes,
                app_secret_ref, user_token_ref, status, last_error, created_at, updated_at)
               VALUES ('facebook_default', 'app', 'v24.0', 'u1', 'User', '[]', '', '', 'CONNECTED', '', ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO facebook_pages (page_id, connection_id, name, category, tasks, picture_url,
                page_token_ref, can_publish, last_synced_at, updated_at, fan_count, followers_count)
               VALUES ('page_1', 'facebook_default', 'Review Phim', 'Entertainment', '[]', '',
                'tok', 1, ?, ?, 12000, 12100)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO channel_destinations (channel_id, provider, destination_id, auto_publish, created_at, updated_at)
               VALUES ('chan_fb', 'facebook', 'page_1', 0, ?, ?)""",
            (now, now),
        )
        conn.commit()


def test_collect_and_overview(tmp_path):
    collected = collect_page_growth(FakeClient(), "page_1", "tok", days=30)
    assert collected["followers"] == 13000
    assert collected["fans"] == 12500
    assert collected["daily"]["2026-08-31"]["fans"] == 12000
    assert collected["daily"]["2026-08-31"]["views"] == 800
    assert collected["daily"]["2026-09-01"]["comments"] == 13
    assert len(collected["posts"]) == 2

    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    _seed_channel(db)
    persist_growth(db, "chan_fb", "page_1", collected)
    overview = growth_overview(db, days=30)
    channel = next(item for item in overview["channels"] if item["channel_id"] == "chan_fb")
    assert channel["followers"]["current"] == 13000
    assert channel["views"]["total"] >= 800
    assert channel["comments"]["total"] >= 13
    assert channel["picture_url"].endswith("/facebook/pages/page_1/picture")


def test_refresh_channel_with_injected_client(tmp_path):
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    _seed_channel(db)
    result = refresh_channel(db, "chan_fb", days=30, client=FakeClient(), page_token="tok")
    assert result["ok"] is True
    assert result["followers"] == 13000
    assert result["posts"] == 2
    with get_db_connection(db) as conn:
        snaps = conn.execute("SELECT COUNT(*) FROM channel_growth_snapshots WHERE channel_id = 'chan_fb'").fetchone()[0]
        posts = conn.execute("SELECT COUNT(*) FROM channel_growth_posts WHERE channel_id = 'chan_fb'").fetchone()[0]
    assert snaps >= 2
    assert posts == 2
