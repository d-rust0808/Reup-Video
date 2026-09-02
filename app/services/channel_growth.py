"""Fanpage growth snapshots: follow / like / comment / view over time."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.core.database import get_db_connection
from app.services.facebook_client import FacebookAPIError, FacebookClient
from app.services.secret_store import get_secret

logger = logging.getLogger(__name__)

VN_UTC_OFFSET = timedelta(hours=7)
STALE_AFTER = timedelta(hours=6)
INSIGHT_BATCHES = (
    ("page_fans", "page_video_views", "page_post_engagements", "page_actions_post_reactions_like_total"),
    ("page_fans", "page_video_views"),
    ("page_fans",),
)
INSIGHT_KEYS = {
    "page_fans": "fans",
    "page_follows": "followers",
    "page_daily_follows": "follows_new",
    "page_fan_adds": "follows_new",
    "page_video_views": "views",
    "page_post_engagements": "engagements",
    "page_impressions": "impressions",
    "page_actions_post_reactions_like_total": "likes",
    "page_actions_post_reactions_total": "reactions",
}

_refresh_lock = threading.Lock()
_refresh_state: Dict[str, Any] = {
    "running": False,
    "done": 0,
    "total": 0,
    "errors": [],
    "started_at": None,
    "finished_at": None,
}


def vn_today() -> str:
    return (datetime.now(timezone.utc) + VN_UTC_OFFSET).date().isoformat()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def coerce_int(value: Any) -> int:
    if isinstance(value, dict):
        return sum(coerce_int(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(coerce_int(item) for item in value)
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def insight_day(end_time: str) -> str:
    """Map Graph's exclusive end_time (usually 07:00/08:00 UTC) onto the Pacific calendar day."""
    text = str(end_time or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace("+0000", "+00:00"))
    except ValueError:
        return text[:10]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed.astimezone(timezone.utc) - timedelta(hours=8)).date().isoformat()


def parse_insights(payload: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    daily: Dict[str, Dict[str, int]] = {}
    for metric in payload.get("data") or []:
        if not isinstance(metric, dict):
            continue
        key = INSIGHT_KEYS.get(str(metric.get("name") or ""))
        if not key:
            continue
        for point in metric.get("values") or []:
            if not isinstance(point, dict):
                continue
            day = insight_day(str(point.get("end_time") or ""))
            if not day:
                continue
            daily.setdefault(day, {})[key] = coerce_int(point.get("value"))
    return daily


def summary_count(node: Any, *keys: str) -> int:
    if not isinstance(node, dict):
        return 0
    for key in keys:
        block = node.get(key)
        if isinstance(block, dict):
            summary = block.get("summary") if isinstance(block.get("summary"), dict) else {}
            if "total_count" in summary:
                return coerce_int(summary.get("total_count"))
            if "count" in block:
                return coerce_int(block.get("count"))
        elif isinstance(block, (int, float, str)):
            return coerce_int(block)
    return 0


def normalize_media(raw: Dict[str, Any]) -> Dict[str, Any]:
    title = str(
        raw.get("message")
        or raw.get("story")
        or raw.get("title")
        or raw.get("description")
        or ""
    ).strip()
    created = str(raw.get("created_time") or "")
    return {
        "post_id": str(raw.get("id") or ""),
        "created_time": created,
        "title": title[:180],
        "permalink": str(raw.get("permalink_url") or raw.get("permalink") or ""),
        "likes": max(summary_count(raw, "likes"), summary_count(raw, "reactions")),
        "comments": summary_count(raw, "comments"),
        "views": coerce_int(raw.get("views")),
        "shares": summary_count(raw, "shares"),
    }


def merge_media(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for raw in rows:
        item = normalize_media(raw) if "likes" not in raw or "post_id" not in raw else {
            "post_id": str(raw.get("post_id") or raw.get("id") or ""),
            "created_time": str(raw.get("created_time") or ""),
            "title": str(raw.get("title") or "")[:180],
            "permalink": str(raw.get("permalink") or raw.get("permalink_url") or ""),
            "likes": coerce_int(raw.get("likes")),
            "comments": coerce_int(raw.get("comments")),
            "views": coerce_int(raw.get("views")),
            "shares": coerce_int(raw.get("shares")),
        }
        pid = item["post_id"]
        if not pid:
            continue
        current = by_id.get(pid)
        if current is None:
            by_id[pid] = item
            continue
        for field in ("likes", "comments", "views", "shares"):
            current[field] = max(coerce_int(current.get(field)), coerce_int(item.get(field)))
        if item.get("title") and len(item["title"]) > len(current.get("title") or ""):
            current["title"] = item["title"]
        if item.get("permalink") and not current.get("permalink"):
            current["permalink"] = item["permalink"]
        if item.get("created_time") and not current.get("created_time"):
            current["created_time"] = item["created_time"]
    return sorted(by_id.values(), key=lambda row: row.get("created_time") or "", reverse=True)


def posts_to_daily(posts: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    daily: Dict[str, Dict[str, int]] = {}
    for post in posts:
        day = str(post.get("created_time") or "")[:10]
        if not day:
            continue
        bucket = daily.setdefault(day, {"likes": 0, "comments": 0, "views": 0, "posts": 0})
        bucket["likes"] += coerce_int(post.get("likes"))
        bucket["comments"] += coerce_int(post.get("comments"))
        bucket["views"] += coerce_int(post.get("views"))
        bucket["posts"] += 1
    return daily


def date_range(days: int, *, end: Optional[str] = None) -> List[str]:
    last = datetime.fromisoformat((end or vn_today()) + "T00:00:00")
    span = max(1, min(int(days or 30), 180))
    return [(last - timedelta(days=span - 1 - index)).date().isoformat() for index in range(span)]


def fill_series(
    points: Dict[str, int],
    days: List[str],
    *,
    carry: bool = False,
) -> List[Dict[str, Any]]:
    series: List[Dict[str, Any]] = []
    last = 0
    seen = False
    for day in days:
        if day in points:
            last = coerce_int(points[day])
            seen = True
            series.append({"date": day, "value": last})
        elif carry and seen:
            series.append({"date": day, "value": last})
        else:
            series.append({"date": day, "value": 0})
    if carry:
        first = next((point["value"] for point in series if point["value"]), 0)
        if first:
            for point in series:
                if point["value"]:
                    break
                point["value"] = first
    return series


def summarize_stock(series: List[Dict[str, Any]]) -> Dict[str, Any]:
    values = [coerce_int(point.get("value")) for point in series]
    current = next((value for value in reversed(values) if value > 0), values[-1] if values else 0)
    start = next((value for value in values if value > 0), 0)
    delta = current - start if start or current else 0
    pct = round((delta / start) * 100, 1) if start else None
    return {
        "current": current,
        "start": start,
        "total": current,
        "delta": delta,
        "delta_pct": pct,
        "series": series,
    }


def summarize_flow(series: List[Dict[str, Any]], previous: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    total = sum(coerce_int(point.get("value")) for point in series)
    prev_total = sum(coerce_int(point.get("value")) for point in (previous or []))
    delta = total - prev_total if previous is not None else 0
    pct = round((delta / prev_total) * 100, 1) if previous is not None and prev_total else None
    return {
        "current": total,
        "start": 0,
        "total": total,
        "delta": delta,
        "delta_pct": pct,
        "series": series,
    }


def refresh_status() -> Dict[str, Any]:
    with _refresh_lock:
        return {
            "running": bool(_refresh_state["running"]),
            "done": int(_refresh_state["done"] or 0),
            "total": int(_refresh_state["total"] or 0),
            "errors": list(_refresh_state["errors"] or []),
            "started_at": _refresh_state["started_at"],
            "finished_at": _refresh_state["finished_at"],
        }


def collect_page_growth(
    client: FacebookClient,
    page_id: str,
    page_token: str,
    *,
    days: int = 90,
) -> Dict[str, Any]:
    errors: List[str] = []
    profile: Dict[str, Any] = {}
    try:
        profile = client.get_page_profile(page_id, page_token) or {}
    except FacebookAPIError as exc:
        errors.append(f"profile: {exc}")

    fans = coerce_int(profile.get("fan_count"))
    followers = coerce_int(profile.get("followers_count")) or fans
    since = int((datetime.now(timezone.utc) - timedelta(days=max(7, days))).timestamp())
    until = int(datetime.now(timezone.utc).timestamp())
    insight_daily: Dict[str, Dict[str, int]] = {}
    for batch in INSIGHT_BATCHES:
        try:
            payload = client.get_page_insights(
                page_id,
                page_token,
                list(batch),
                period="day",
                since=since,
                until=until,
            )
        except FacebookAPIError as exc:
            errors.append(f"insights {','.join(batch)}: {exc}")
            continue
        parsed = parse_insights(payload)
        for day, values in parsed.items():
            insight_daily.setdefault(day, {}).update(values)
        if parsed:
            break

    posts: List[Dict[str, Any]] = []
    videos: List[Dict[str, Any]] = []
    try:
        posts = client.list_published_posts(page_id, page_token, limit=50)
    except FacebookAPIError as exc:
        errors.append(f"posts: {exc}")
    media_preview = merge_media(posts)
    need_videos = not any(coerce_int(item.get("views")) for item in media_preview)
    if need_videos:
        try:
            videos = client.list_page_videos(page_id, page_token, limit=50)
        except FacebookAPIError as exc:
            errors.append(f"videos: {exc}")

    media = merge_media([*posts, *videos])
    post_daily = posts_to_daily(media)
    today = vn_today()
    days_map: Dict[str, Dict[str, int]] = {}

    for day, values in insight_daily.items():
        bucket = days_map.setdefault(day, {"followers": 0, "fans": 0, "likes": 0, "comments": 0, "views": 0, "posts": 0})
        fans_point = coerce_int(values.get("fans"))
        follow_point = coerce_int(values.get("followers")) or fans_point
        if follow_point:
            bucket["followers"] = follow_point
        if fans_point:
            bucket["fans"] = fans_point
        if coerce_int(values.get("likes")):
            bucket["likes"] = coerce_int(values.get("likes"))
        if coerce_int(values.get("views")):
            bucket["views"] = coerce_int(values.get("views"))

    insight_has_likes = any(coerce_int(row.get("likes")) for row in insight_daily.values())
    insight_has_views = any(coerce_int(row.get("views")) for row in insight_daily.values())
    for day, values in post_daily.items():
        bucket = days_map.setdefault(day, {"followers": 0, "fans": 0, "likes": 0, "comments": 0, "views": 0, "posts": 0})
        bucket["comments"] += coerce_int(values.get("comments"))
        bucket["posts"] += coerce_int(values.get("posts"))
        if not insight_has_likes:
            bucket["likes"] += coerce_int(values.get("likes"))
        if not insight_has_views:
            bucket["views"] += coerce_int(values.get("views"))

    today_bucket = days_map.setdefault(
        today, {"followers": 0, "fans": 0, "likes": 0, "comments": 0, "views": 0, "posts": 0}
    )
    if followers:
        today_bucket["followers"] = followers
    if fans:
        today_bucket["fans"] = fans

    source = "profile"
    if insight_daily:
        source = "insights"
    elif media:
        source = "posts"

    return {
        "fans": fans,
        "followers": followers,
        "daily": days_map,
        "posts": media,
        "errors": errors,
        "source": source,
        "name": str(profile.get("name") or ""),
    }


def upsert_snapshot(
    conn,
    channel_id: str,
    day: str,
    *,
    page_id: str = "",
    followers: Optional[int] = None,
    fans: Optional[int] = None,
    likes: Optional[int] = None,
    comments: Optional[int] = None,
    views: Optional[int] = None,
    posts: Optional[int] = None,
    source: str = "live",
) -> None:
    existing = conn.execute(
        "SELECT * FROM channel_growth_snapshots WHERE channel_id = ? AND snapshot_date = ?",
        (channel_id, day),
    ).fetchone()
    now = utc_now()

    def stock(incoming: Optional[int], field: str) -> int:
        value = coerce_int(incoming) if incoming is not None else 0
        if existing is None:
            return value
        if value > 0:
            return value
        return coerce_int(existing[field])

    def flow(incoming: Optional[int], field: str) -> int:
        if incoming is None and existing is not None:
            return coerce_int(existing[field])
        return coerce_int(incoming)

    payload = (
        page_id or (existing["page_id"] if existing else ""),
        stock(followers, "followers"),
        stock(fans, "fans"),
        flow(likes, "likes"),
        flow(comments, "comments"),
        flow(views, "views"),
        flow(posts, "posts"),
        source or (existing["source"] if existing else "live"),
        now,
        channel_id,
        day,
    )
    if existing:
        conn.execute(
            """
            UPDATE channel_growth_snapshots
            SET page_id = ?, followers = ?, fans = ?, likes = ?, comments = ?,
                views = ?, posts = ?, source = ?, created_at = ?
            WHERE channel_id = ? AND snapshot_date = ?
            """,
            payload,
        )
        return
    conn.execute(
        """
        INSERT INTO channel_growth_snapshots (
            page_id, followers, fans, likes, comments, views, posts, source, created_at,
            channel_id, snapshot_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )


def record_profile_snapshot(
    conn,
    *,
    page_id: str,
    fans: int = 0,
    followers: int = 0,
    now: Optional[str] = None,
) -> None:
    pid = str(page_id or "").strip()
    if not pid:
        return
    row = conn.execute(
        """
        SELECT channel_id FROM channel_destinations
        WHERE provider = 'facebook' AND destination_id = ?
        LIMIT 1
        """,
        (pid,),
    ).fetchone()
    if not row:
        return
    upsert_snapshot(
        conn,
        row["channel_id"],
        vn_today(),
        page_id=pid,
        followers=followers or fans,
        fans=fans,
        source="profile",
    )


def seed_today_from_pages(db_path: str) -> None:
    today = vn_today()
    with get_db_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT cd.channel_id, fp.page_id, fp.fan_count, fp.followers_count
            FROM channel_destinations cd
            JOIN facebook_pages fp ON fp.page_id = cd.destination_id
            WHERE cd.provider = 'facebook'
            """
        ).fetchall()
        for row in rows:
            upsert_snapshot(
                conn,
                row["channel_id"],
                today,
                page_id=row["page_id"],
                followers=coerce_int(row["followers_count"]) or coerce_int(row["fan_count"]),
                fans=coerce_int(row["fan_count"]),
                source="profile",
            )
        conn.commit()


def persist_growth(
    db_path: str,
    channel_id: str,
    page_id: str,
    collected: Dict[str, Any],
) -> None:
    now = utc_now()
    with get_db_connection(db_path) as conn:
        if collected.get("followers") or collected.get("fans"):
            conn.execute(
                """
                UPDATE facebook_pages
                SET fan_count = CASE WHEN ? > 0 THEN ? ELSE fan_count END,
                    followers_count = CASE WHEN ? > 0 THEN ? ELSE followers_count END,
                    updated_at = ?
                WHERE page_id = ?
                """,
                (
                    coerce_int(collected.get("fans")),
                    coerce_int(collected.get("fans")),
                    coerce_int(collected.get("followers")),
                    coerce_int(collected.get("followers")),
                    now,
                    page_id,
                ),
            )
        for day, values in (collected.get("daily") or {}).items():
            upsert_snapshot(
                conn,
                channel_id,
                day,
                page_id=page_id,
                followers=values.get("followers"),
                fans=values.get("fans"),
                likes=values.get("likes"),
                comments=values.get("comments"),
                views=values.get("views"),
                posts=values.get("posts"),
                source=str(collected.get("source") or "live"),
            )
        for post in collected.get("posts") or []:
            pid = str(post.get("post_id") or "")
            if not pid:
                continue
            conn.execute(
                """
                INSERT INTO channel_growth_posts (
                    post_id, channel_id, page_id, created_time, title, permalink,
                    likes, comments, views, shares, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    page_id = excluded.page_id,
                    created_time = excluded.created_time,
                    title = excluded.title,
                    permalink = excluded.permalink,
                    likes = excluded.likes,
                    comments = excluded.comments,
                    views = excluded.views,
                    shares = excluded.shares,
                    updated_at = excluded.updated_at
                """,
                (
                    pid,
                    channel_id,
                    page_id,
                    str(post.get("created_time") or ""),
                    str(post.get("title") or ""),
                    str(post.get("permalink") or ""),
                    coerce_int(post.get("likes")),
                    coerce_int(post.get("comments")),
                    coerce_int(post.get("views")),
                    coerce_int(post.get("shares")),
                    now,
                ),
            )
        conn.commit()


def _targets(db_path: str, channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = """
        SELECT c.channel_id, c.name, cd.destination_id AS page_id,
               fp.page_token_ref, fp.fan_count, fp.followers_count,
               COALESCE(fc.graph_version, 'v24.0') AS graph_version
        FROM channels c
        JOIN channel_destinations cd
          ON cd.channel_id = c.channel_id AND cd.provider = 'facebook'
        JOIN facebook_pages fp ON fp.page_id = cd.destination_id
        LEFT JOIN facebook_connections fc ON fc.id = fp.connection_id
        WHERE c.status != 'ARCHIVED'
    """
    params: List[Any] = []
    if channel_id:
        sql += " AND c.channel_id = ?"
        params.append(channel_id)
    sql += " ORDER BY c.name COLLATE NOCASE"
    with get_db_connection(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def refresh_channel(
    db_path: str,
    channel_id: str,
    *,
    days: int = 90,
    client: Optional[FacebookClient] = None,
    page_token: Optional[str] = None,
) -> Dict[str, Any]:
    targets = _targets(db_path, channel_id)
    if not targets:
        return {"ok": False, "channel_id": channel_id, "error": "Kênh chưa gắn Fanpage Facebook"}
    target = targets[0]
    own_client = client is None
    token = page_token or (get_secret(target["page_token_ref"]) if target.get("page_token_ref") else "")
    if not token and client is None:
        seed_today_from_pages(db_path)
        return {"ok": False, "channel_id": channel_id, "error": "Thiếu page token — đồng bộ Facebook rồi thử lại"}
    if client is None:
        client = FacebookClient(target.get("graph_version") or "v24.0", timeout=12.0)
    try:
        collected = collect_page_growth(client, target["page_id"], token or "local", days=days)
        persist_growth(db_path, channel_id, target["page_id"], collected)
        return {
            "ok": True,
            "channel_id": channel_id,
            "page_id": target["page_id"],
            "followers": collected.get("followers") or 0,
            "fans": collected.get("fans") or 0,
            "posts": len(collected.get("posts") or []),
            "days": len(collected.get("daily") or {}),
            "source": collected.get("source"),
            "errors": collected.get("errors") or [],
        }
    except Exception as exc:
        logger.warning("Growth refresh failed for %s: %s", channel_id, exc)
        return {"ok": False, "channel_id": channel_id, "error": str(exc)}
    finally:
        if own_client:
            client.close()


def refresh_all(db_path: str, *, days: int = 90) -> Dict[str, Any]:
    targets = _targets(db_path)
    with _refresh_lock:
        _refresh_state.update(
            running=True,
            done=0,
            total=len(targets),
            errors=[],
            started_at=utc_now(),
            finished_at=None,
        )
    errors: List[Dict[str, str]] = []
    try:
        for index, target in enumerate(targets):
            result = refresh_channel(db_path, target["channel_id"], days=days)
            if not result.get("ok"):
                errors.append(
                    {
                        "channel_id": target["channel_id"],
                        "name": target.get("name") or "",
                        "message": str(result.get("error") or "Lỗi không rõ"),
                    }
                )
            with _refresh_lock:
                _refresh_state["done"] = index + 1
                _refresh_state["errors"] = list(errors)
            time.sleep(0.12)
    finally:
        with _refresh_lock:
            _refresh_state["running"] = False
            _refresh_state["finished_at"] = utc_now()
            _refresh_state["errors"] = list(errors)
    return refresh_status()


def start_refresh_all(db_path: str, *, days: int = 90) -> Dict[str, Any]:
    with _refresh_lock:
        if _refresh_state["running"]:
            return refresh_status()
        _refresh_state.update(
            running=True,
            done=0,
            total=0,
            errors=[],
            started_at=utc_now(),
            finished_at=None,
        )
    thread = threading.Thread(
        target=refresh_all,
        kwargs={"db_path": db_path, "days": days},
        name="channel-growth-refresh",
        daemon=True,
    )
    thread.start()
    return refresh_status()


def _metric_points(rows: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    points: Dict[str, int] = {}
    for row in rows:
        points[row["snapshot_date"]] = coerce_int(row.get(field))
    return points


def _is_stale(rows: List[Dict[str, Any]]) -> bool:
    today = vn_today()
    if not rows:
        return True
    latest = max(rows, key=lambda row: row.get("created_at") or row.get("snapshot_date") or "")
    if str(latest.get("snapshot_date") or "") < today:
        return True
    has_engagement = any(
        coerce_int(row.get("likes")) or coerce_int(row.get("comments")) or coerce_int(row.get("views"))
        for row in rows
    )
    if not has_engagement:
        return True
    stamp = str(latest.get("created_at") or "")
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - parsed > STALE_AFTER


def _channel_payload(
    channel: Dict[str, Any],
    snapshots: List[Dict[str, Any]],
    days: List[str],
    prev_days: List[str],
    group_ids: List[str],
) -> Dict[str, Any]:
    follow_field = "followers"
    if snapshots and not any(coerce_int(row.get("followers")) for row in snapshots):
        follow_field = "fans"
    followers = summarize_stock(fill_series(_metric_points(snapshots, follow_field), days, carry=True))
    likes = summarize_flow(
        fill_series(_metric_points(snapshots, "likes"), days),
        fill_series(_metric_points(snapshots, "likes"), prev_days),
    )
    comments = summarize_flow(
        fill_series(_metric_points(snapshots, "comments"), days),
        fill_series(_metric_points(snapshots, "comments"), prev_days),
    )
    views = summarize_flow(
        fill_series(_metric_points(snapshots, "views"), days),
        fill_series(_metric_points(snapshots, "views"), prev_days),
    )
    live_followers = coerce_int(channel.get("followers_count")) or coerce_int(channel.get("fan_count"))
    if live_followers:
        followers["current"] = live_followers
        if followers["series"]:
            followers["series"][-1]["value"] = live_followers
            start = followers.get("start") or 0
            followers["delta"] = live_followers - start if start else 0
            followers["delta_pct"] = round((followers["delta"] / start) * 100, 1) if start else None
            followers["total"] = live_followers
    page_id = str(channel.get("page_id") or "")
    return {
        "channel_id": channel["channel_id"],
        "name": channel.get("name") or "",
        "platform": channel.get("platform") or "",
        "page_id": page_id,
        "picture_url": f"/api/v1/facebook/pages/{page_id}/picture" if page_id else "",
        "username": channel.get("username") or "",
        "category": channel.get("category") or "",
        "group_ids": group_ids,
        "stale": _is_stale(snapshots),
        "last_snapshot_at": max((row.get("snapshot_date") or "" for row in snapshots), default=""),
        "followers": followers,
        "likes": likes,
        "comments": comments,
        "views": views,
    }


def growth_overview(db_path: str, *, days: int = 30) -> Dict[str, Any]:
    seed_today_from_pages(db_path)
    span = max(7, min(int(days or 30), 180))
    window = date_range(span)
    previous = date_range(span, end=(datetime.fromisoformat(window[0] + "T00:00:00") - timedelta(days=1)).date().isoformat())
    lookback = previous[0]
    with get_db_connection(db_path) as conn:
        channels = [
            dict(row)
            for row in conn.execute(
                """
                SELECT c.channel_id, c.name, c.platform, c.status, c.color,
                       cd.destination_id AS page_id,
                       fp.fan_count, fp.followers_count, fp.username, fp.category
                FROM channels c
                LEFT JOIN channel_destinations cd
                  ON cd.channel_id = c.channel_id AND cd.provider = 'facebook'
                LEFT JOIN facebook_pages fp ON fp.page_id = cd.destination_id
                WHERE c.status != 'ARCHIVED'
                ORDER BY COALESCE(fp.followers_count, fp.fan_count, 0) DESC, c.name COLLATE NOCASE
                """
            ).fetchall()
        ]
        snaps = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM channel_growth_snapshots
                WHERE snapshot_date >= ?
                ORDER BY snapshot_date ASC
                """,
                (lookback,),
            ).fetchall()
        ]
        groups = [
            dict(row)
            for row in conn.execute(
                "SELECT group_id, name FROM channel_groups ORDER BY name COLLATE NOCASE"
            ).fetchall()
        ]
        members = conn.execute("SELECT group_id, channel_id FROM channel_group_members").fetchall()
    by_channel: Dict[str, List[Dict[str, Any]]] = {}
    for row in snaps:
        by_channel.setdefault(row["channel_id"], []).append(row)
    grouped: Dict[str, List[str]] = {}
    for row in members:
        grouped.setdefault(row["channel_id"], []).append(row["group_id"])
    items = [
        _channel_payload(channel, by_channel.get(channel["channel_id"]) or [], window, previous, grouped.get(channel["channel_id"]) or [])
        for channel in channels
    ]
    return {
        "days": span,
        "today": vn_today(),
        "refresh": refresh_status(),
        "groups": groups,
        "channels": items,
    }


def growth_detail(db_path: str, channel_id: str, *, days: int = 30) -> Dict[str, Any]:
    overview = growth_overview(db_path, days=days)
    channel = next((item for item in overview["channels"] if item["channel_id"] == channel_id), None)
    if channel is None:
        return {"ok": False, "error": "Kênh không tồn tại"}
    since = date_range(max(7, min(int(days or 30), 180)))[0]
    with get_db_connection(db_path) as conn:
        posts = [
            dict(row)
            for row in conn.execute(
                """
                SELECT post_id, created_time, title, permalink, likes, comments, views, shares
                FROM channel_growth_posts
                WHERE channel_id = ? AND created_time >= ?
                ORDER BY created_time DESC
                LIMIT 40
                """,
                (channel_id, since),
            ).fetchall()
        ]
    return {"ok": True, "days": overview["days"], "refresh": overview["refresh"], "channel": channel, "posts": posts}
