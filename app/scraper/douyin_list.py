"""List public Douyin profile videos.

Douyin's web `/aweme/v1/web/aweme/post/` endpoint rejects unsigned HTTP
calls (empty body). Opening the profile in Chrome lets the page's own
request interceptor sign `fetch()`, which is enough to pull the catalog.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

_CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-dev-shm-usage",
]

_CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/microsoft-edge",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)

_FETCH_DETAIL_JS = """
async (awemeId) => {
  const params = new URLSearchParams({
    device_platform: 'webapp',
    aid: '6383',
    channel: 'channel_pc_web',
    aweme_id: String(awemeId),
    request_source: '600',
    origin_type: 'video_page',
    update_version_code: '170400',
    pc_client_type: '1',
    pc_libra_divert: 'Windows',
    version_code: '190500',
    version_name: '19.5.0',
    cookie_enabled: 'true',
    screen_width: '1280',
    screen_height: '900',
    browser_language: 'zh-CN',
    browser_platform: 'Win32',
    browser_name: 'Chrome',
    browser_version: '131.0.0.0',
    browser_online: 'true',
    engine_name: 'Blink',
    engine_version: '131.0.0.0',
    os_name: 'Windows',
    os_version: '10',
    cpu_core_num: '8',
    device_memory: '8',
    platform: 'PC',
    downlink: '10',
    effective_type: '4g',
    round_trip_time: '50',
  });
  const res = await fetch('/aweme/v1/web/aweme/detail/?' + params.toString(), {
    credentials: 'include',
    headers: { accept: 'application/json, text/plain, */*' },
  });
  const text = await res.text();
  if (!text) return { ok: false, empty: true, http: res.status };
  let data = null;
  try { data = JSON.parse(text); } catch (err) {
    return { ok: false, http: res.status, snippet: text.slice(0, 80) };
  }
  return { ok: true, http: res.status, data };
}
"""

_FETCH_POST_JS = """
async ({sec, cursor, count}) => {
  const params = new URLSearchParams({
    device_platform: 'webapp',
    aid: '6383',
    channel: 'channel_pc_web',
    sec_user_id: sec,
    max_cursor: String(cursor || 0),
    locate_query: 'false',
    show_live_replay_strategy: '1',
    need_time_list: '1',
    time_list_query: '0',
    whale_cut_token: '',
    cut_version: '1',
    count: String(count || 50),
    publish_video_strategy_type: '2',
    from_user_page: '1',
  });
  const res = await fetch('/aweme/v1/web/aweme/post/?' + params.toString(), {
    credentials: 'include',
    headers: { accept: 'application/json, text/plain, */*' },
  });
  const text = await res.text();
  if (!text) return { ok: false, empty: true, http: res.status };
  let data = null;
  try { data = JSON.parse(text); } catch (err) {
    return { ok: false, http: res.status, snippet: text.slice(0, 80) };
  }
  const list = Array.isArray(data.aweme_list) ? data.aweme_list : [];
  const items = list.map((item) => ({
    aweme_id: String((item && (item.aweme_id || item.id)) || ''),
    desc: (item && (item.desc || item.title)) || '',
    mix_id: String((item && item.mix_info && item.mix_info.mix_id) || ''),
    create_time: (item && (item.create_time || item.createTime)) || 0,
  }));
  return {
    ok: true,
    http: res.status,
    status: data.status_code,
    has_more: data.has_more,
    max_cursor: data.max_cursor,
    time_list: data.time_list || [],
    items,
  };
}
"""

_FETCH_MIX_JS = """
async ({mixId, cursor, count}) => {
  const params = new URLSearchParams({
    device_platform: 'webapp',
    aid: '6383',
    channel: 'channel_pc_web',
    mix_id: String(mixId),
    cursor: String(cursor || 0),
    count: String(count || 20),
  });
  const res = await fetch('/aweme/v1/web/mix/aweme/?' + params.toString(), {
    credentials: 'include',
    headers: { accept: 'application/json, text/plain, */*' },
  });
  const text = await res.text();
  if (!text) return { ok: false, empty: true, http: res.status };
  let data = null;
  try { data = JSON.parse(text); } catch (err) {
    return { ok: false, http: res.status };
  }
  const list = Array.isArray(data.aweme_list) ? data.aweme_list : [];
  const items = list.map((item) => ({
    aweme_id: String((item && (item.aweme_id || item.id)) || ''),
    desc: (item && (item.desc || item.title)) || '',
    mix_id: String(mixId),
  }));
  return {
    ok: true,
    status: data.status_code,
    has_more: data.has_more,
    cursor: data.cursor || data.max_cursor || 0,
    items,
  };
}
"""

_CLICK_MONTHS_JS = """
async (months) => {
  const wanted = (months || []).map((m) => String(m || '').trim()).filter(Boolean);
  const clicked = [];
  for (const label of wanted) {
    const nodes = Array.from(document.querySelectorAll('span, div, button, li, p, a'));
    const el = nodes.find((node) => (node.innerText || '').trim() === label);
    if (!el) continue;
    try { el.click(); clicked.push(label); } catch (err) {}
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
  return clicked;
}
"""


def aweme_from_detail_payload(payload: Any, item_id: str = "") -> Optional[Dict[str, Any]]:
    """Pull `aweme_detail` (or a matching list item) from a Douyin detail JSON body."""
    if not isinstance(payload, dict):
        return None
    wanted = str(item_id or "").strip()
    direct = payload.get("aweme_detail")
    if isinstance(direct, dict):
        aid = str(direct.get("aweme_id") or "")
        if not wanted or not aid or aid == wanted:
            return direct
    for key in ("item_list", "aweme_list"):
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            aid = str(item.get("aweme_id") or item.get("id") or "")
            if wanted and aid and aid != wanted:
                continue
            if item.get("video") or aid:
                return item
    return None


def catalog_entries_from_awemes(items: Iterable[Any], **_kwargs: Any) -> List[Dict[str, str]]:
    """Normalize aweme dicts (or `{aweme_id, desc}` rows) into catalog entries."""
    entries: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        vid = str(raw.get("aweme_id") or raw.get("video_id") or raw.get("id") or "").strip()
        if not vid or not vid.isdigit() or len(vid) < 15 or vid in seen:
            continue
        seen.add(vid)
        title = str(raw.get("desc") or raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip() or f"https://www.douyin.com/video/{vid}"
        published = raw.get("create_time") or raw.get("published_at") or raw.get("timestamp")
        entries.append({
            "video_id": vid,
            "title": title,
            "url": url,
            "published_at": published,
        })
    return entries


def _merge_entries(dest: List[Dict[str, str]], incoming: Sequence[Dict[str, str]], limit: int) -> None:
    have = {row["video_id"] for row in dest}
    for row in incoming:
        vid = row.get("video_id") or ""
        if not vid or vid in have:
            continue
        dest.append(row)
        have.add(vid)
        if len(dest) >= limit:
            return


def _chrome_executables() -> List[str]:
    found: List[str] = []
    for path in _CHROME_PATHS:
        if path and os.path.isfile(path):
            found.append(path)
    return found


async def _launch_browser(playwright: Any) -> Any:
    errors: List[str] = []
    for channel in ("chrome", "msedge", "chrome-beta", "chromium"):
        try:
            return await playwright.chromium.launch(
                channel=channel,
                headless=True,
                args=_CHROME_ARGS,
            )
        except Exception as exc:
            errors.append(f"{channel}: {exc}")
    for path in _chrome_executables():
        try:
            return await playwright.chromium.launch(
                executable_path=path,
                headless=True,
                args=_CHROME_ARGS,
            )
        except Exception as exec_exc:
            errors.append(f"{path}: {exec_exc}")
    try:
        return await playwright.chromium.launch(headless=True, args=_CHROME_ARGS)
    except Exception as exc:
        errors.append(f"bundled: {exc}")
    raise RuntimeError(
        "Không mở được Chrome/Edge để lấy dữ liệu Douyin. "
        "Cài Google Chrome hoặc Microsoft Edge rồi thử lại. "
        + "; ".join(errors[:3])
    )


async def _ingest_response(response: Any, sink: List[Dict[str, str]], limit: int) -> None:
    try:
        url = str(getattr(response, "url", "") or "")
        if "aweme/post" not in url and "mix/aweme" not in url:
            return
        if int(getattr(response, "status", 0) or 0) != 200:
            return
        data = await response.json()
    except Exception:
        return
    if not isinstance(data, dict):
        return
    _merge_entries(sink, catalog_entries_from_awemes(data.get("aweme_list") or []), limit)


async def _ingest_detail_response(response: Any, sink: List[Dict[str, Any]], item_id: str) -> None:
    if sink:
        return
    try:
        url = str(getattr(response, "url", "") or "")
        if "aweme/detail" not in url and "aweme/iteminfo" not in url and "aweme/item" not in url:
            return
        if int(getattr(response, "status", 0) or 0) != 200:
            return
        data = await response.json()
    except Exception:
        return
    aweme = aweme_from_detail_payload(data, item_id)
    if aweme:
        sink.append(aweme)


async def fetch_douyin_aweme(item_id: str) -> Optional[Dict[str, Any]]:
    """Open the video page in Chrome so Douyin's signed `fetch()` returns aweme_detail.

    Unsigned httpx calls to `/aweme/v1/web/aweme/detail/` are blocked by Argus
    (`Uifid Not Found`). The page interceptor adds the same tokens the catalog
    list already relies on.
    """
    vid = str(item_id or "").strip()
    if not vid.isdigit() or len(vid) < 15:
        return None
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        logger.info("playwright unavailable for Douyin video: %s", exc)
        return None

    found: List[Dict[str, Any]] = []
    pending: Set[asyncio.Task[Any]] = set()
    try:
        async with async_playwright() as playwright:
            browser = await _launch_browser(playwright)
            try:
                context = await browser.new_context(
                    locale="zh-CN",
                    viewport={"width": 1280, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                )
                page = await context.new_page()
                loop = asyncio.get_running_loop()

                def _on_response(response: Any) -> None:
                    task = loop.create_task(_ingest_detail_response(response, found, vid))
                    pending.add(task)
                    task.add_done_callback(pending.discard)

                page.on("response", _on_response)
                await page.goto(
                    f"https://www.douyin.com/video/{vid}",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
                await page.wait_for_timeout(2200)
                if not found:
                    try:
                        payload = await page.evaluate(_FETCH_DETAIL_JS, vid)
                    except Exception as exc:
                        logger.debug("Douyin in-page detail fetch skipped: %s", exc)
                        payload = None
                    if isinstance(payload, dict):
                        aweme = aweme_from_detail_payload(payload.get("data"), vid)
                        if aweme:
                            found.append(aweme)
                for _ in range(16):
                    if found:
                        break
                    await page.wait_for_timeout(250)
                if pending:
                    await asyncio.wait(pending, timeout=5)
            finally:
                await browser.close()
    except Exception:
        logger.exception("Douyin browser video fetch failed for %s", vid)
        return found[0] if found else None
    if found:
        logger.info("Douyin browser aweme id=%s", vid)
        return found[0]
    logger.info("Douyin browser aweme empty id=%s", vid)
    return None


async def _fetch_pages(page: Any, sec_user_id: str, max_videos: int) -> tuple[List[Dict[str, str]], List[str], List[str]]:
    entries: List[Dict[str, str]] = []
    mix_ids: List[str] = []
    months: List[str] = []
    cursor = 0
    for _ in range(12):
        if len(entries) >= max_videos:
            break
        remaining = max_videos - len(entries)
        payload = await page.evaluate(
            _FETCH_POST_JS,
            {"sec": sec_user_id, "cursor": cursor, "count": min(50, max(18, remaining))},
        )
        if not isinstance(payload, dict) or not payload.get("ok"):
            break
        items = payload.get("items") or []
        _merge_entries(entries, catalog_entries_from_awemes(items), max_videos)
        for item in items:
            mix_id = str((item or {}).get("mix_id") or "").strip()
            if mix_id and mix_id not in mix_ids:
                mix_ids.append(mix_id)
        for label in payload.get("time_list") or []:
            text = str(label or "").strip()
            if text and text not in months:
                months.append(text)
        has_more = payload.get("has_more")
        next_cursor = payload.get("max_cursor")
        if not items or not has_more or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        await page.wait_for_timeout(800)
    return entries, mix_ids, months


async def _fetch_mixes(page: Any, mix_ids: Sequence[str], max_videos: int, dest: List[Dict[str, str]]) -> None:
    for mix_id in mix_ids:
        if len(dest) >= max_videos:
            return
        cursor = 0
        for _ in range(20):
            if len(dest) >= max_videos:
                return
            payload = await page.evaluate(
                _FETCH_MIX_JS,
                {"mixId": mix_id, "cursor": cursor, "count": 20},
            )
            if not isinstance(payload, dict) or not payload.get("ok"):
                break
            items = payload.get("items") or []
            before = len(dest)
            _merge_entries(dest, catalog_entries_from_awemes(items), max_videos)
            if not items or not payload.get("has_more"):
                break
            next_cursor = payload.get("cursor") or 0
            if next_cursor == cursor or len(dest) == before:
                break
            cursor = next_cursor
            await page.wait_for_timeout(400)


async def list_douyin_user_videos(sec_user_id: str, max_videos: int = 500) -> List[Dict[str, str]]:
    """Return catalog rows `{video_id, title, url}` for a Douyin `sec_user_id`."""
    sec = (sec_user_id or "").strip()
    if not sec:
        return []
    limit = max(1, min(int(max_videos or 500), 500))
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        logger.info("playwright unavailable for Douyin list: %s", exc)
        return []

    entries: List[Dict[str, str]] = []
    pending: Set[asyncio.Task[Any]] = set()
    try:
        async with async_playwright() as playwright:
            browser = await _launch_browser(playwright)
            try:
                context = await browser.new_context(
                    locale="zh-CN",
                    viewport={"width": 1440, "height": 1100},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                )
                page = await context.new_page()
                loop = asyncio.get_running_loop()

                def _on_response(response: Any) -> None:
                    task = loop.create_task(_ingest_response(response, entries, limit))
                    pending.add(task)
                    task.add_done_callback(pending.discard)

                page.on("response", _on_response)
                await page.goto(
                    f"https://www.douyin.com/user/{sec}",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
                await page.wait_for_timeout(3500)
                title = ""
                try:
                    title = await page.title()
                except Exception:
                    title = ""
                if "验证码" in (title or ""):
                    logger.warning("Douyin profile requires captcha; catalog may be incomplete")
                fetched, mix_ids, months = await _fetch_pages(page, sec, limit)
                _merge_entries(entries, fetched, limit)
                if months and len(entries) < limit:
                    try:
                        await page.evaluate(_CLICK_MONTHS_JS, months[:8])
                    except Exception as exc:
                        logger.debug("Douyin month tabs skipped: %s", exc)
                    await page.wait_for_timeout(1500)
                if mix_ids and len(entries) < limit:
                    await _fetch_mixes(page, mix_ids, limit, entries)
                if pending:
                    await asyncio.wait(pending, timeout=8)
            finally:
                await browser.close()
    except Exception:
        logger.exception("Douyin browser catalog failed for %s", sec[:24])
        return entries[:limit]
    logger.info("Douyin browser catalog sec=%s n=%s", sec[:24], len(entries))
    return entries[:limit]
