import os, json, re, stripe, bleach, requests
from datetime import datetime, date, timedelta
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, flash, abort, jsonify, session
from markupsafe import Markup
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, TextAreaField, BooleanField, FloatField
from wtforms.validators import DataRequired, Email, Optional, URL as URLVal, NumberRange
from models import db, Post, Subscriber, ContactMessage, Donation, NewsItem
from config import Config
from utils.signal import send_signal_group
from utils.email import send_email_smtp
from utils.scraper import fetch_calendar_week
from pathlib import Path
from pathlib import Path as _P
from dotenv import load_dotenv
from wtforms.validators import ValidationError
from werkzeug.utils import secure_filename
from itertools import islice
from utils.scraper import fetch_calendar_week
from utils.calendar_rss import week_events_rss
from zoneinfo import ZoneInfo
from dateutil import parser as dtparse
import feedparser
import subprocess
import logging, sys
import time, urllib.parse

TZ = ZoneInfo("America/Indiana/Indianapolis")

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

ROOT = _P(__file__).resolve().parent
_VER_FILE = ROOT / "VERSION"
_ver_cache = {"t": 0.0, "v": None}
_ver_state = {
    "v": None,                # cached version string WITHOUT leading 'v'
    "src": None,              # "VERSION" or "git" (for your own debugging)
    "verfile_mtime": 0.0,     # last seen mtime of VERSION file
    "git_head_rev": "",       # last seen git HEAD commit hash
}

app = Flask(__name__)

@app.context_processor
def inject_keys():
    return {"TINYMCE_API_KEY": app.config.get("TINYMCE_API_KEY", "")}

@app.context_processor
def inject_cfg():
    return {"CFG": app.config}

app.config.from_object(Config)
app.config.setdefault("HERO_IMAGE_DIR", str(Path(app.static_folder) / "img"))
Path(app.config["HERO_IMAGE_DIR"]).mkdir(parents=True, exist_ok=True)
db.init_app(app)
stripe.api_key = app.config["STRIPE_SECRET_KEY"]

FEED_URL = app.config["FEED_URL"]

_cache = {"t": 0, "items": []}

ALLOWED_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + ["p","img","video","audio","source","figure","figcaption","h1","h2","h3","h4","h5","h6","blockquote","pre","code","hr","br","strong","em","ul","ol","li","a","table","thead","tbody","tr","th","td","span"]
ALLOWED_ATTRS = {**bleach.sanitizer.ALLOWED_ATTRIBUTES, "img":["src","alt","title","loading"], "a":["href","title","target","rel"], "video":["src","controls","poster"], "audio":["src","controls"], "source":["src","type"], "span":["class"]}
ALLOWED_PROTOCOLS = ["http","https","mailto","tel"]
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

def list_static_images():
    base = Path(app.config["HERO_IMAGE_DIR"])
    imgs = []
    for p in base.iterdir():
        if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTS:
            imgs.append("/static/img/" + p.name)
    return sorted(imgs)

def url_or_static(form, field):
    v = (field.data or "").strip()
    if not v:
        return  # Optional is handled separately
    if v.startswith(("/static/", "static/")):
        return
    if v.startswith(("http://", "https://")):
        return
    raise ValidationError("Enter a full URL (https://...) or a /static/... path")

def _git_ver():
    try:
        v = subprocess.check_output(["git","describe","--tags","--abbrev=0"], stderr=subprocess.DEVNULL).decode().strip()
        return v[1:] if v.startswith("v") else v
    except Exception:
        return None
    
def _chunks(iterable, size):
    it = iter(iterable)
    while True:
        batch = list(islice(it, size))
        if not batch: break
        yield batch
    
def _mask(v: str | None, keep: int = 4) -> str:
    if not v: return "(empty)"
    v = str(v)
    if len(v) <= keep: return v
    return v[:keep] + "…" + v[-keep:]

def _first_image(entry):
    # 1) media:content / media:thumbnail
    media = getattr(entry, "media_content", None) or []
    if media and isinstance(media, list) and media[0].get("url"):
        return media[0]["url"]
    thumbs = getattr(entry, "media_thumbnail", None) or []
    if thumbs and isinstance(thumbs, list) and thumbs[0].get("url"):
        return thumbs[0]["url"]
    # 2) enclosure
    enc = getattr(entry, "enclosures", None) or []
    for e in enc:
        if e.get("type", "").startswith("image/") and e.get("href"):
            return e["href"]
    # 3) try to sniff from summary/content (very light)
    for key in ("summary", "summary_detail", "content"):
        val = getattr(entry, key, None)
        html = ""
        if isinstance(val, list) and val:
            html = val[0].get("value", "")
        elif isinstance(val, dict):
            html = val.get("value", "")
        elif isinstance(val, str):
            html = val
        if html:
            import re
            m = re.search(r'<img[^>]+src="([^"]+)"', html, re.I)
            if m:
                return m.group(1)
    return None

def _source(entry):
    # Try to show a human-readable source (falls back to link domain).
    if getattr(entry, "source", None) and entry.source.get("title"):
        return entry.source["title"]
    try:
        return urllib.parse.urlparse(entry.link).hostname
    except Exception:
        return "Source"

def _fmt_time(entry):
    # Prefer published, then updated
    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if not raw:
        return None
    try:
        dt = dtparse.parse(raw)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return None

def get_news_items(ttl=300, limit=40):
    now = time.time()
    if now - _cache["t"] > ttl:
        d = feedparser.parse(FEED_URL)
        items = []
        for e in d.entries[:limit]:
            items.append({
                "title": e.title,
                "link": e.link,
                "img": _first_image(e),
                "when": _fmt_time(e),            # datetime or None
                "source": _source(e),
                "summary": getattr(e, "summary", None),
            })
        _cache["items"] = items
        _cache["t"] = now
    return _cache["items"]

def _normalize(v: str) -> str:
    v = (v or "").strip()
    return v[1:] if v.startswith("v") else v

def _read_version_file():
    try:
        mtime = _VER_FILE.stat().st_mtime
        if mtime != _ver_state["verfile_mtime"]:
            s = _VER_FILE.read_text().strip()
            _ver_state["v"] = _normalize(s)
            _ver_state["src"] = "VERSION"
            _ver_state["verfile_mtime"] = mtime
        return True
    except Exception:
        return False

def _git_head_hash():
    try:
        head_path = ROOT / ".git" / "HEAD"
        head = head_path.read_text().strip()
        if head.startswith("ref: "):
            ref = head.split(" ", 1)[1]  # e.g. refs/heads/main
            ref_path = ROOT / ".git" / ref
            return ref_path.read_text().strip()
        return head  # detached HEAD: contains the hash
    except Exception:
        return ""

def _read_git_tag_exact():
    try:
        tag = subprocess.check_output(
            ["git", "describe", "--tags", "--exact-match"],
            stderr=subprocess.DEVNULL, cwd=str(ROOT), timeout=1.5
        ).decode().strip()
        _ver_state["v"] = _normalize(tag)
        _ver_state["src"] = "git"
        return True
    except Exception:
        return False

def get_app_version(ttl=0):
    """
    Returns the version WITHOUT a leading 'v'.
    Auto-updates when:
      - VERSION file content/mtime changes, or
      - .git HEAD commit changes.
    ttl is kept for signature compatibility; we invalidate by file/HEAD change.
    """
    # 1) Prefer VERSION file if present; refresh on mtime change
    if _VER_FILE.exists():
        if _read_version_file():
            return _ver_state["v"]

    # 2) Otherwise, detect git HEAD change and (re)read exact tag if any
    head = _git_head_hash()
    if head and head != _ver_state["git_head_rev"]:
        _ver_state["git_head_rev"] = head
        if _read_git_tag_exact():
            return _ver_state["v"]

    # 3) If cached, keep using it
    if _ver_state["v"]:
        return _ver_state["v"]

    # 4) Final fallback: short SHA or 0.0.0
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, cwd=str(ROOT), timeout=1.5
        ).decode().strip()
        _ver_state["v"] = sha
        _ver_state["src"] = "git"
        return _ver_state["v"]
    except Exception:
        _ver_state["v"] = "0.0.0"
        _ver_state["src"] = "fallback"
        return _ver_state["v"]

@app.context_processor
def inject_version():
    # template expects the 'v' prefix, so we add it there
    return {"APP_VERSION": get_app_version()}

class SubscribeForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    first_name = StringField("First name", validators=[Optional()])
    last_name = StringField("Last name", validators=[Optional()])

class ContactForm(FlaskForm):
    name = StringField("Name", validators=[Optional()])
    email = StringField("Email", validators=[DataRequired(), Email()])
    subject = StringField("Subject", validators=[Optional()])
    message = TextAreaField("Message", validators=[DataRequired()])

class PostForm(FlaskForm):
    title = StringField("Title", validators=[DataRequired()])
    slug = StringField("Slug", validators=[DataRequired()])
    summary = TextAreaField("Summary", validators=[Optional()])
    content = TextAreaField("Content (HTML)", validators=[DataRequired()])
    hero_image_url = StringField("Hero image URL", validators=[Optional()])
    hero_file = FileField("Upload hero image",
                          validators=[Optional(), FileAllowed(["jpg","jpeg","png","gif","webp","svg"], "Images only")])
    published = BooleanField("Published")

class DonationForm(FlaskForm):
    amount = FloatField("Amount (USD)", validators=[DataRequired(), NumberRange(min=1.0)])
    donor_name = StringField("Name", validators=[Optional()])
    donor_email = StringField("Email", validators=[Optional(), Email()])

class EmptyForm(FlaskForm):
    pass

def sanitize_html(html): return bleach.clean(html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, protocols=ALLOWED_PROTOCOLS, strip=False)
def require_admin(): 
    if not session.get("is_admin"): abort(403)

@app.route("/")
def index():
    intro = ("NewsNowIndy is a local, independent investigative journalism outlet in Indianapolis. "
             "We focus on accountability reporting across criminal justice, crime, and local government—"
             "digging into court records, budgets, policing, prosecutions, and public policy so residents "
             "can make informed decisions. Our mission is simple: verify the facts, follow the paper trail, "
             "and tell the story plainly.")
    posts = Post.query.filter_by(published=True).order_by(Post.created_at.desc()).limit(3).all()
    return render_template("index.html", intro=intro, posts=posts)

@app.route("/articles/")
def articles():
    posts = Post.query.filter_by(published=True).order_by(Post.created_at.desc()).all()
    return render_template("articles.html", posts=posts)

@app.route("/article/<slug>/")
def article_detail(slug):
    post = Post.query.filter_by(slug=slug, published=True).first_or_404()
    return render_template("article_detail.html", post=post)

@app.route("/subscribe/", methods=["GET","POST"])
def subscribe():
    form = SubscribeForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if not Subscriber.query.filter_by(email=email).first():
            db.session.add(Subscriber(
                email=email,
                first_name=form.first_name.data or None,
                last_name=form.last_name.data or None
            ))
            db.session.commit()
        flash("You're subscribed! We'll email you about new posts and important updates.", "success")

        # Signal notify (with logging)
        try:
            rc, out, err = send_signal_group(
                f"New subscriber: {form.first_name.data or ''} {form.last_name.data or ''} <{email}>",
                app.config.get("SIGNAL_SENDER"), app.config.get("SIGNAL_GROUP"), app.config.get("SIGNAL_CLI_BIN")
            )
            if rc != 0:
                app.logger.error("Signal subscriber notify failed rc=%s err=%s", rc, err)
        except Exception:
            app.logger.exception("Signal notify crashed in /subscribe")

        return redirect(url_for("index"))
    return render_template("subscribe.html", form=form)

@app.route("/donate/", methods=["GET","POST"])
def donate():
    form = DonationForm()
    return render_template("donate.html", form=form)

@app.route("/donate/checkout", methods=["POST"])
def donate_checkout():
    form = DonationForm()
    if not form.validate_on_submit():
        flash("Enter a valid amount.", "danger"); return redirect(url_for("donate"))
    amount_cents = int(round(form.amount.data * 100))
    metadata = {"donor_name": form.donor_name.data or "", "donor_email": form.donor_email.data or ""}
    session_stripe = stripe.checkout.Session.create(
        payment_method_types=["card"], mode="payment",
        line_items=[{"price_data":{"currency":"usd","product_data":{"name":"NewsNowIndy Donation"},"unit_amount":amount_cents},"quantity":1}],
        success_url=app.config["DONATION_SUCCESS_URL"], cancel_url=app.config["DONATION_CANCEL_URL"], metadata=metadata,
    )
    return redirect(session_stripe.url, code=303)

@app.route("/donate/success")
def donate_success(): return render_template("donate_success.html")
@app.route("/donate/cancel")
def donate_cancel(): return render_template("donate_cancel.html")

@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data(as_text=True); sig = request.headers.get("Stripe-Signature","")
    try: event = stripe.Webhook.construct_event(payload, sig, app.config["STRIPE_WEBHOOK_SECRET"])
    except Exception: return ("", 400)
    if event["type"] == "checkout.session.completed":
        data = event["data"]["object"]; amount_total = data.get("amount_total", 0)
        donor_name = data.get("metadata",{}).get("donor_name") or None
        donor_email = data.get("metadata",{}).get("donor_email") or None
        db.session.add(Donation(amount=amount_total, donor_name=donor_name, donor_email=donor_email, provider="stripe", provider_ref=data.get("id"), status="succeeded"))
        db.session.commit()
    return ("", 200)

@app.route("/contact/", methods=["GET","POST"])
def contact():
    form = ContactForm(); site_key = app.config.get("TURNSTILE_SITE_KEY")
    if form.validate_on_submit():
        secret = app.config.get("TURNSTILE_SECRET"); token = request.form.get("cf-turnstile-response",""); is_spam = False
        if secret and token:
            try:
                ok = requests.post("https://challenges.cloudflare.com/turnstile/v0/siteverify", data={"secret":secret,"response":token}, timeout=10).json().get("success", False)
                if not ok: is_spam = True
            except Exception: is_spam = True

        msg = ContactMessage(
            name=form.name.data or None,
            email=form.email.data,
            subject=form.subject.data or None,
            message=form.message.data,
            is_spam=is_spam
        )
        db.session.add(ContactMessage(name=form.name.data or None, email=form.email.data, subject=form.subject.data or None, message=form.message.data, is_spam=is_spam)); db.session.commit()

        try:
            if app.config.get("SIGNAL_SENDER") and app.config.get("SIGNAL_GROUP") and app.config.get("SIGNAL_CLI_BIN"):
                preview = (msg.message or "").strip().replace("\n", " ")
                if len(preview) > 140: preview = preview[:137] + "..."
                rc, out, err = send_signal_group(
                    f"New contact ({'SPAM?' if is_spam else 'OK'})\nFrom: {msg.name or 'Anonymous'} <{msg.email}>\nSubject: {msg.subject or '(no subject)'}\nPreview: {preview}",
                    app.config["SIGNAL_SENDER"], app.config["SIGNAL_GROUP"], app.config["SIGNAL_CLI_BIN"]
                )
                if rc != 0:
                    app.logger.error("Signal contact notify failed rc=%s err=%s", rc, err)
        except Exception:
            app.logger.exception("Signal notify crashed in /contact")

        flash("Thanks for reaching out." + (" (Message flagged for manual review.)" if is_spam else ""), "success"); return redirect(url_for("contact"))
    return render_template("contact.html", form=form, turnstile_site_key=site_key)


@app.route("/news/")
def news():
    return render_template("news.html", items=get_news_items())

@app.route("/events/")
def events():
    week_param = request.args.get("week","")
    today = date.today()
    iso_year, iso_week, _ = today.isocalendar()

    if re.fullmatch(r"\d{4}-\d{2}", (week_param or "")):
        iso_year, iso_week = map(int, week_param.split("-"))

    items, start_d, end_d = week_events_rss(iso_year, iso_week)

    # Prev/next week keys
    start = date.fromisocalendar(iso_year, iso_week, 1)
    prev_w = (start - timedelta(days=7)).isocalendar()
    next_w = (start + timedelta(days=7)).isocalendar()
    prev_week = f"{prev_w[0]}-{prev_w[1]:02d}"
    next_week = f"{next_w[0]}-{next_w[1]:02d}"

    # Header like "September 21, 2025 – September 27, 2025"
    if start_d and end_d:
        pretty_range = f"{start_d.strftime('%B %d, %Y')} – {end_d.strftime('%B %d, %Y')}"
    else:
        pretty_range = f"Week {iso_year}-{iso_week:02d}"

    return render_template("events.html",
        iso_year=iso_year, iso_week=iso_week,
        items=items, prev_week=prev_week, next_week=next_week,
        pretty_range=pretty_range
    )

@app.route("/admin/debug-events")
def admin_debug_events():
    if not session.get("is_admin"): return abort(403)
    url = app.config.get("INDY_CAL_RSS_URL")
    from utils.calendar_rss import _week_bounds
    iso_year, iso_week, _ = date.today().isocalendar()
    items, start_d, end_d = week_events_rss(iso_year, iso_week)
    return (
        "<pre>"
        f"RSS URL: {url or '(empty)'}\n"
        f"Week: {iso_year}-{iso_week:02d} ({start_d} .. {end_d})\n"
        f"Items this week: {len(items)}\n"
        + "\n".join(f"- {i['start']}  {i['title']}" for i in items[:10])
        + "</pre>"
    )

@app.route("/foia-laws/")
def foia_laws(): return render_template("foia.html")

@app.route("/admin/login/", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        pw = request.form.get("password","")
        if pw and app.config["ADMIN_PASSWORD"] and pw == app.config["ADMIN_PASSWORD"]:
            session["is_admin"]=True; return redirect(url_for("admin_dashboard"))
        flash("Invalid password.", "danger")
    return render_template("admin/login.html")

@app.route("/admin/logout/")
def admin_logout(): session.clear(); flash("Logged out.", "success"); return redirect(url_for("index"))

@app.route("/admin/")
def admin_dashboard():
    if not session.get("is_admin"): return abort(403)
    stats = {"posts": Post.query.count(),"subscribers": Subscriber.query.count(),"donations": Donation.query.count(),"messages": ContactMessage.query.count(),"news_items": NewsItem.query.count()}
    return render_template("admin/dashboard.html", stats=stats)

@app.route("/admin/subscribers/")
def admin_subscribers():
    if not session.get("is_admin"): return abort(403)
    subs = Subscriber.query.order_by(Subscriber.created_at.desc()).all(); return render_template("admin/subscribers.html", subs=subs)

@app.route("/admin/broadcast/", methods=["GET","POST"])
def admin_broadcast():
    if not session.get("is_admin"): return abort(403)
    if request.method == "POST":
        subject = (request.form.get("subject","") or "").strip(); html = (request.form.get("html","") or "").strip()
        if not subject or not html: flash("Subject and HTML body are required.", "danger"); return redirect(url_for("admin_broadcast"))
        to_list = [s.email for s in Subscriber.query.all()]
        if not to_list: flash("No subscribers to send to.", "warning"); return redirect(url_for("admin_broadcast"))
        send_email_smtp(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], app.config["MAIL_USE_TLS"], app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"], app.config["MAIL_FROM"], to_list, subject, html)
        flash(f"Sent to {len(to_list)} subscribers.", "success"); return redirect(url_for("admin_broadcast"))
    return render_template("admin/broadcast.html")

@app.route("/admin/posts/", methods=["GET","POST"])
def admin_posts():
    if not session.get("is_admin"): return abort(403)
    form = PostForm()
    if form.validate_on_submit():
        # 1) If a file was uploaded, save it
        hero = None
        if form.hero_file.data:
            fn = secure_filename(form.hero_file.data.filename or "")
            ext = os.path.splitext(fn)[1].lower()
            if not ext or ext not in ALLOWED_IMAGE_EXTS:
                flash("Invalid image type.", "danger")
                return redirect(url_for("admin_posts"))
            dest = Path(app.config["HERO_IMAGE_DIR"]) / f"{int(datetime.utcnow().timestamp())}_{fn}"
            form.hero_file.data.save(dest)
            hero = "/static/img/" + dest.name

        # 2) If no file, use selection or typed URL
        if not hero:
            # From the <select> (see template) or the typed URL
            choice = (request.form.get("hero_image_choice") or "").strip()
            if choice:
                hero = choice  # already like /static/img/filename.ext
            else:
                typed = (form.hero_image_url.data or "").strip()
                if typed.startswith("static/"):
                    typed = "/" + typed
                hero = typed or None

        db.session.add(Post(
            title=form.title.data,
            slug=form.slug.data,
            summary=form.summary.data or None,
            content=sanitize_html(form.content.data),
            hero_image_url=hero,
            published=form.published.data
        ))
        db.session.commit()
        flash("Post saved.", "success")
        return redirect(url_for("admin_posts"))

    posts = Post.query.order_by(Post.created_at.desc()).all()
    broadcast_form = EmptyForm()
    return render_template("admin/posts.html", form=form, posts=posts, broadcast_form=broadcast_form, available_images=list_static_images())

@app.route("/admin/posts/<int:pid>/edit/", methods=["GET", "POST"])
def admin_post_edit(pid):
    if not session.get("is_admin"):
        return abort(403)

    p = Post.query.get_or_404(pid)
    form = PostForm(obj=p)

    if form.validate_on_submit():
        # Save new upload if provided
        hero = p.hero_image_url
        if form.hero_file.data:
            fn = secure_filename(form.hero_file.data.filename or "")
            ext = os.path.splitext(fn)[1].lower()
            if not ext or ext not in ALLOWED_IMAGE_EXTS:
                flash("Invalid image type.", "danger")
                return redirect(url_for("admin_post_edit", pid=p.id))
            dest = Path(app.config["HERO_IMAGE_DIR"]) / f"{int(datetime.utcnow().timestamp())}_{fn}"
            form.hero_file.data.save(dest)
            hero = "/static/img/" + dest.name
        else:
            choice = (request.form.get("hero_image_choice") or "").strip()
            if choice:
                hero = choice
            else:
                typed = (form.hero_image_url.data or "").strip()
                if typed.startswith("static/"):
                    typed = "/" + typed
                hero = typed or None

        p.title = form.title.data
        p.slug = form.slug.data
        p.summary = form.summary.data or None
        p.hero_image_url = hero
        p.content = sanitize_html(form.content.data)
        p.published = form.published.data

        db.session.commit()
        flash("Post updated.", "success")
        return redirect(url_for("admin_posts"))

    return render_template("admin/edit_post.html", form=form, post=p, available_images=list_static_images())

@app.route("/admin/posts/<int:pid>/delete/", methods=["POST"])
def admin_post_delete(pid):
    if not session.get("is_admin"): return abort(403)
    p = Post.query.get_or_404(pid); db.session.delete(p); db.session.commit(); flash("Post deleted.", "success"); return redirect(url_for("admin_posts"))

@app.route("/admin/posts/<int:pid>/broadcast/", methods=["POST"])
def admin_post_broadcast(pid):
    if not session.get("is_admin"):
        return abort(403)

    p = Post.query.get_or_404(pid)
    subs = [s.email for s in Subscriber.query.all()]
    if not subs:
        flash("No subscribers to send to.", "warning")
        return redirect(url_for("admin_posts"))

    # Build absolute URL to the article
    base = request.url_root.rstrip("/")
    article_url = base + url_for("article_detail", slug=p.slug)

    hero_html = f'<p><img src="{p.hero_image_url}" alt="" style="max-width:100%;border-radius:10px;border:1px solid #333"></p>' if p.hero_image_url else ""
    html = f"""
    <div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#111">
      <h2 style="margin:0 0 8px 0">{p.title}</h2>
      <p style="margin:0 0 16px 0;opacity:.8">NewsNowIndy</p>
      {hero_html}
      {"<p>"+(p.summary or "")+"</p>" if p.summary else ""}
      <div>{p.content}</div>
      <p style="margin-top:16px">
        <a href="{article_url}" style="display:inline-block;padding:10px 14px;background:#0b5; color:#fff; text-decoration:none; border-radius:8px">Read on the site</a>
      </p>
      <hr style="margin:24px 0;border:0;border-top:1px solid #ddd">
      <p style="font-size:12px;opacity:.7">You’re receiving this because you subscribed to NewsNowIndy alerts.</p>
    </div>
    """

    subject = f"New: {p.title} — NewsNowIndy"

    total = 0
    for batch in _chunks(subs, 100):
        send_email_smtp(
            app.config["MAIL_SERVER"], app.config["MAIL_PORT"], app.config["MAIL_USE_TLS"],
            app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"], app.config["MAIL_FROM"],
            batch, subject, html
        )
        total += len(batch)

    # (Optional) Signal ping
    try:
        if app.config.get("SIGNAL_SENDER") and app.config.get("SIGNAL_GROUP") and app.config.get("SIGNAL_CLI_BIN"):
            send_signal_group(
                f'Broadcasted Post "{p.title}" to {total} subscriber(s).',
                app.config["SIGNAL_SENDER"], app.config["SIGNAL_GROUP"], app.config["SIGNAL_CLI_BIN"]
            )
    except Exception:
        pass

    flash(f'Sent "{p.title}" to {total} subscriber(s).', "success")
    return redirect(url_for("admin_posts"))

@app.route("/admin/news/<int:nid>/broadcast/", methods=["POST"])
def admin_news_broadcast(nid):
    if not session.get("is_admin"):
        return abort(403)

    item = NewsItem.query.get_or_404(nid)
    subs = [s.email for s in Subscriber.query.all()]
    if not subs:
        flash("No subscribers to send to.", "warning")
        return redirect(url_for("admin_news"))

    # Build simple, clean HTML email
    pub = item.published_at.strftime("%b %d, %Y") if item.published_at else ""
    source = f" — {item.source}" if item.source else ""
    article_url = item.link  # external source link

    html = f"""
    <div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#111;line-height:1.5">
      <h2 style="margin:0 0 6px 0">{item.title}</h2>
      <p style="margin:0 0 14px 0;opacity:.8">NewsNowIndy News Feed{source}{(' • ' + pub) if pub else ''}</p>
      {"<p>"+(item.summary or "").strip()+"</p>" if (item.summary or "").strip() else ""}
      <p style="margin-top:16px">
        <a href="{article_url}" target="_blank" rel="noopener"
           style="display:inline-block;padding:10px 14px;background:#0b5;color:#fff;text-decoration:none;border-radius:8px">
           Read the article
        </a>
      </p>
      <hr style="margin:24px 0;border:0;border-top:1px solid #ddd">
      <p style="font-size:12px;opacity:.7;margin:0">You’re receiving this because you subscribed to NewsNowIndy alerts.</p>
    </div>
    """

    subject = f"News Feed: {item.title}"

    sent = 0
    for batch in _chunks(subs, 100):  # send in batches
        send_email_smtp(
            app.config["MAIL_SERVER"], app.config["MAIL_PORT"], app.config["MAIL_USE_TLS"],
            app.config["MAIL_USERNAME"], app.config["MAIL_PASSWORD"], app.config["MAIL_FROM"],
            batch, subject, html
        )
        sent += len(batch)

    # (Optional) Signal notification to your group so you know it went out
    try:
        if app.config.get("SIGNAL_SENDER") and app.config.get("SIGNAL_GROUP") and app.config.get("SIGNAL_CLI_BIN"):
            preview = (item.title or "")[:120]
            send_signal_group(
                f"Broadcasted NewsItem to {sent} subscriber(s): {preview}",
                app.config["SIGNAL_SENDER"], app.config["SIGNAL_GROUP"], app.config["SIGNAL_CLI_BIN"]
            )
    except Exception:
        pass

    flash(f"Sent news item to {sent} subscriber(s).", "success")
    return redirect(url_for("admin_news"))

@app.route("/admin/donations/")
def admin_donations():
    if not session.get("is_admin"): return abort(403)
    rows = Donation.query.order_by(Donation.created_at.desc()).all(); return render_template("admin/donations.html", rows=rows)

@app.route("/admin/messages/")
def admin_messages():
    if not session.get("is_admin"): return abort(403)
    rows = ContactMessage.query.order_by(ContactMessage.created_at.desc()).all(); return render_template("admin/messages.html", rows=rows)

@app.route("/admin/news/")
def admin_news():
    if not session.get("is_admin"): return abort(403)
    rows = NewsItem.query.order_by(NewsItem.published_at.desc().nullslast(), NewsItem.id.desc()).all()
    broadcast_form = EmptyForm()
    return render_template("admin/news.html", rows=rows, broadcast_form=broadcast_form)

@app.route("/admin/import_rss/", methods=["POST","GET"])
def admin_import_rss():
    if not session.get("is_admin"): return abort(403)
    try:
        resp = requests.get(app.config["RSS_JSON"], timeout=10); data = resp.json(); imported = 0
        for item in data.get("items", []):
            title = (item.get("title") or "").strip(); link = item.get("url") or item.get("link")
            if not title or not link: continue
            if NewsItem.query.filter_by(link=link).first(): continue
            published_at = None; dt = item.get("publishedDate") or item.get("pubDate")
            if dt:
                try: published_at = datetime.fromisoformat(dt.replace("Z","+00:00"))
                except Exception: published_at = None
            db.session.add(NewsItem(title=title[:300], link=link[:500], source=item.get("source","")[:200] if item.get("source") else None, summary=item.get("description") or None, published_at=published_at)); imported += 1
        db.session.commit(); flash(f"RSS import complete. {imported} new items.", "success")
    except Exception as e:
        flash(f"RSS import failed: {e}", "danger")
    return redirect(url_for("admin_news"))

@app.route("/admin/test-signal/", methods=["POST"])
def admin_test_signal():
    if not session.get("is_admin"): return abort(403)
    text = f"Signal test {datetime.utcnow().isoformat()}Z from NewsNowIndy"
    rc, out, err = send_signal_group(
        text,
        app.config.get("SIGNAL_SENDER"),
        app.config.get("SIGNAL_GROUP"),
        app.config.get("SIGNAL_CLI_BIN"),
        config_dir=app.config.get("SIGNAL_CONFIG_DIR")
    )
    msg = f"rc={rc}"
    if err: msg += f" | err: {err[:200]}"
    if out: msg += f" | out: {out[:200]}"
    if rc == 0:
        flash("Signal test message sent. " + msg, "success")
    else:
        flash("Signal test failed. " + msg, "danger")
        app.logger.error("Signal test failed %s", msg)
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/debug-signal/")
def admin_debug_signal():
    if not session.get("is_admin"): return abort(403)
    sender = app.config.get("SIGNAL_SENDER")
    group  = app.config.get("SIGNAL_GROUP")
    cli    = app.config.get("SIGNAL_CLI_BIN")
    cfg    = app.config.get("SIGNAL_CONFIG_DIR")

    missing = [k for k, val in {
        "SIGNAL_SENDER": sender,
        "SIGNAL_GROUP": group,
        "SIGNAL_CLI_BIN": cli
    }.items() if not val]

    lines = [
        f"SIGNAL_SENDER:     {_mask(sender)}",
        f"SIGNAL_GROUP:      {_mask(group, keep=6)}",
        f"SIGNAL_CLI_BIN:    {cli or '(empty)'}",
        f"SIGNAL_CONFIG_DIR: {cfg or '(default)'}",
        f"MISSING:           {', '.join(missing) if missing else '(none)'}"
    ]
    return "<pre>" + "\n".join(lines) + "</pre>"

@app.cli.command("init-db")
def init_db():
    with app.app_context():
        db.create_all(); print("Database initialized.")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
