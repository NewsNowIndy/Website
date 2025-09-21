from datetime import date
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlencode

def iso_week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-{w:02d}"

def fetch_calendar_week(iso_year: int, iso_week: int):
    base = "https://calendar.indy.gov/"
    qs = urlencode({"view":"grid","search":"y"})
    url = f"{base}?{qs}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    events = []
    for a in soup.select("a"):
        title = a.get_text(strip=True)
        href = a.get("href","")
        if not title or "http" not in href:
            continue
        parent = a.find_parent()
        time_txt = parent.get_text(" ", strip=True) if parent else ""
        events.append({"title": title[:400], "link": href, "when_text": time_txt[:200], "location": None})
    return events
