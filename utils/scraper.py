# utils/scraper.py
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlencode, urljoin
import requests
from bs4 import BeautifulSoup

BASE = "https://calendar.indy.gov/"

def _week_start(iso_year: int, iso_week: int) -> date:
    return date.fromisocalendar(iso_year, iso_week, 1)  # Monday

def fetch_calendar_week(iso_year: int, iso_week: int):
    """
    Returns a list of events for the given ISO week by scraping the grid view.
    Each item: {title, location, start, end, url}
    NOTE: This relies on site markup; if they change HTML, adjust selectors.
    """
    week_monday = _week_start(iso_year, iso_week)

    # Many calendar systems accept a start parameter; if not, we’ll just parse current grid and filter by week.
    # Try a 'start' param in YYYY-MM-DD (harmless if ignored).
    params = {"view": "grid", "search": "y", "start": week_monday.isoformat()}
    url = BASE + "?" + urlencode(params)

    headers = {
        "User-Agent": "NewsNowIndyBot/1.0 (+https://newsnowindy.com)"
    }
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    html = r.text
    soup = BeautifulSoup(html, "lxml")

    # --- Heuristics: find event “cards/rows” in the grid ---
    # Try common structures: elements with a class containing 'event' or links to event detail pages.
    # Adjust these selectors to match the actual markup if needed.
    candidates = soup.select('.event, .Event, .calendar-event, .fc-event, .list-event, [class*="event"]')

    events = []
    week_end = week_monday + timedelta(days=7)

    def parse_dt(text):
        # Try to extract date/time from text like "Mon, Sep 23, 6:30 PM"
        text = re.sub(r'\s+', ' ', text or '').strip()
        for fmt in (
            "%a, %b %d, %I:%M %p",
            "%A, %B %d, %I:%M %p",
            "%b %d, %Y %I:%M %p",
            "%m/%d/%Y %I:%M %p",
            "%Y-%m-%d %H:%M",
        ):
            try:
                # Assume local Eastern time; store naive, you can localize if desired
                return datetime.strptime(text, fmt)
            except ValueError:
                pass
        return None

    for c in candidates:
        # Title
        title_el = c.select_one('.title, .event-title, a, h3, h4')
        title = (title_el.get_text(strip=True) if title_el else "").strip()

        # Link (to event details)
        link_el = title_el if title_el and title_el.name == "a" else c.select_one('a[href*="event"], a[href*="Event"]')
        href = link_el.get("href") if link_el else None
        url_abs = urljoin(BASE, href) if href else None

        # Time/date text (site-specific; try a few labels/containers)
        when_el = c.select_one('.time, .event-time, .date, .datetime')
        when_text = (when_el.get_text(" ", strip=True) if when_el else "").strip()

        # Split start/end if like "6:30 PM – 8:30 PM" or similar patterns
        start_dt = end_dt = None
        if "–" in when_text or "-" in when_text:
            sep = "–" if "–" in when_text else "-"
            parts = [p.strip() for p in when_text.split(sep, 1)]
            if len(parts) == 2:
                start_dt = parse_dt(parts[0])
                end_dt = parse_dt(parts[1])
        if not start_dt:
            start_dt = parse_dt(when_text)

        # Location
        loc_el = c.select_one('.location, .event-location, .venue')
        location = (loc_el.get_text(" ", strip=True) if loc_el else "").strip()

        # If date is not embedded, attempt to infer from the day column around the event node (optional enhancement)

        # Filter to week window if we got a start
        if start_dt and (week_monday <= start_dt.date() < week_end):
            events.append({
                "title": title or "Untitled",
                "location": location or "",
                "start": start_dt,
                "end": end_dt,
                "url": url_abs
            })

    # As a fallback, if nothing matched, you can relax selectors or parse table rows by weekday headers.

    # Sort by start time
    events.sort(key=lambda e: e["start"] or datetime.min)
    return events
