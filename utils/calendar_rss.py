import feedparser
from datetime import date, datetime, timedelta
from time import mktime
from flask import current_app

def _week_bounds(iso_year: int, iso_week: int):
    start = date.fromisocalendar(iso_year, iso_week, 1)   # Monday
    end = start + timedelta(days=6)                        # Sunday (inclusive for header)
    return start, end

def _entry_dt(entry):
    # Prefer published_parsed -> updated_parsed -> now
    if getattr(entry, "published_parsed", None):
        return datetime.fromtimestamp(mktime(entry.published_parsed))
    if getattr(entry, "updated_parsed", None):
        return datetime.fromtimestamp(mktime(entry.updated_parsed))
    return datetime.utcnow()

def week_events_rss(iso_year: int, iso_week: int):
    """
    Pull the City's aggregate RSS feed and return events for the ISO week.
    Each item: {title, start (datetime), end (None), location, url}
    """
    url = current_app.config.get("INDY_CAL_RSS_URL")
    if not url:
        return [], None, None

    feed = feedparser.parse(url)
    start_d, end_d = _week_bounds(iso_year, iso_week)

    items = []
    for e in getattr(feed, "entries", []):
        dt = _entry_dt(e)
        if start_d <= dt.date() <= end_d:
            title = getattr(e, "title", "Untitled")
            link = getattr(e, "link", None)
            # Many civic RSS feeds put location in author or a custom tag; grab both if present
            loc = getattr(e, "location", "") or getattr(e, "author", "") or ""
            items.append({
                "title": title,
                "start": dt,
                "end": None,
                "location": loc,
                "url": link,
            })
    items.sort(key=lambda x: x["start"])
    return items, start_d, end_d
