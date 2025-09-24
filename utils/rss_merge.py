# utils/rss_merge.py
import time, re, requests, feedparser
from html import unescape
from datetime import datetime, timezone
from dateutil import parser as dtparse

UA = "NewsNowIndy/merge/1.0 (+https://newsnowindy.com)"
DEFAULT_TTL = 600  # seconds

_cache = {"at": 0.0, "ttl": DEFAULT_TTL, "items": []}

def _http_get(url, timeout=12):
    return requests.get(url, headers={"User-Agent": UA}, timeout=timeout)

def _to_dt(v):
    if v is None: return None
    if hasattr(v, "tm_year"):  # time.struct_time
        return datetime(*v[:6], tzinfo=timezone.utc)
    try:
        return dtparse.parse(v)
    except Exception:
        return None

def _first(*vals):
    for v in vals:
        if v: return v
    return None

def _extract_image(entry):
    # media:content
    m = entry.get("media_content") or []
    if isinstance(m, list) and m and m[0].get("url"): return m[0]["url"]
    # media:thumbnail
    t = entry.get("media_thumbnail") or []
    if isinstance(t, list) and t and t[0].get("url"): return t[0]["url"]
    # enclosure
    for e in entry.get("enclosures") or []:
        if e.get("href") and str(e.get("type","")).startswith("image/"):
            return e["href"]
    # first <img> in summary/content
    html = _first(
        (entry.get("content") or [{}])[0].get("value") if entry.get("content") else None,
        (entry.get("summary_detail") or {}).get("value"),
        entry.get("summary"),
    )
    if html:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I)
        if m: return m.group(1)
    return None

def _source_title(fp_feed, entry):
    st = (fp_feed.get("title") or "").strip()
    if st: return st
    try:
        import urllib.parse as up
        return (up.urlparse(entry.get("link") or "").hostname) or "Feed"
    except Exception:
        return "Feed"

def fetch_combined(urls, limit=100, per_feed_limit=100, ttl=DEFAULT_TTL):
    now = time.time()
    if _cache["items"] and now - _cache["at"] < _cache["ttl"]:
        return _cache["items"][:limit]

    items = []
    for url in urls:
        try:
            r = _http_get(url); r.raise_for_status()
            fp = feedparser.parse(r.content)
            src = fp.feed if hasattr(fp, "feed") else {}
            for e in (fp.entries or [])[:per_feed_limit]:
                title = unescape(_first(e.get("title"), "")).strip()
                link  = (e.get("link") or "").strip()
                if not title or not link: continue
                when = _to_dt(_first(e.get("published_parsed"), e.get("updated_parsed"),
                                     e.get("published"), e.get("updated")))
                items.append({
                    # match your template keys:
                    "title": title,
                    "link": link,
                    "img": _extract_image(e),
                    "when": when,  # datetime | None
                    "source": _source_title(src, e),
                    "summary": _first(e.get("summary"), ""),
                })
        except Exception:
            # ignore one feed failing
            continue

    # de-dupe by link; keep newest
    by_link = {}
    for it in items:
        k = it["link"]
        if k not in by_link:
            by_link[k] = it
        else:
            a, b = by_link[k], it
            a_when = a["when"] or datetime(1970,1,1,tzinfo=timezone.utc)
            b_when = b["when"] or datetime(1970,1,1,tzinfo=timezone.utc)
            if b_when > a_when: by_link[k] = b

    merged = sorted(
        by_link.values(),
        key=lambda x: x["when"] or datetime(1970,1,1,tzinfo=timezone.utc),
        reverse=True
    )

    _cache.update({"items": merged, "at": now, "ttl": ttl})
    return merged[:limit]
