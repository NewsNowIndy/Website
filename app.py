import os, json, re, stripe, bleach, requests
from datetime import datetime, date
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, flash, abort, jsonify, session
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
from dotenv import load_dotenv
from wtforms.validators import ValidationError
from werkzeug.utils import secure_filename
import subprocess

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

from pathlib import Path as _P
__version__ = (_P(__file__).resolve().parent / "VERSION").read_text().strip() \
    if (_P(__file__).resolve().parent / "VERSION").exists() else "0.0.0"

app = Flask(__name__)

@app.context_processor
def inject_version():
    return {"APP_VERSION": __version__}

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

VER_FILE = Path(__file__).resolve().parent / "VERSION"
__version__ = VER_FILE.read_text().strip() if VER_FILE.exists() else (_git_ver() or "0.0.0")

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
            db.session.add(Subscriber(email=email, first_name=form.first_name.data or None, last_name=form.last_name.data or None))
            db.session.commit()
        flash("You're subscribed! We'll email you about new posts and important updates.", "success")
        send_signal_group(f"New subscriber: {form.first_name.data or ''} {form.last_name.data or ''} <{email}>", app.config["SIGNAL_SENDER"], app.config["SIGNAL_GROUP"], app.config["SIGNAL_CLI_BIN"])
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
        db.session.add(ContactMessage(name=form.name.data or None, email=form.email.data, subject=form.subject.data or None, message=form.message.data, is_spam=is_spam)); db.session.commit()
        flash("Thanks for reaching out." + (" (Message flagged for manual review.)" if is_spam else ""), "success"); return redirect(url_for("contact"))
    return render_template("contact.html", form=form, turnstile_site_key=site_key)

@app.route("/news/")
def news():
    items = NewsItem.query.order_by(NewsItem.published_at.desc().nullslast(), NewsItem.id.desc()).limit(100).all()
    return render_template("news.html", items=items)

@app.route("/events/")
def events():
    week_param = request.args.get("week",""); today = date.today(); iso_year, iso_week, _ = today.isocalendar()
    if re.fullmatch(r"\d{4}-\d{2}", week_param or ""): iso_year, iso_week = map(int, week_param.split("-"))
    iframe_url = "https://calendar.indy.gov/?" + urlencode({"view":"grid","search":"y"})
    try: parsed = fetch_calendar_week(iso_year, iso_week)
    except Exception: parsed = []
    return render_template("events.html", iframe_url=iframe_url, iso_year=iso_year, iso_week=iso_week, parsed=parsed)

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
    return render_template("admin/posts.html", form=form, posts=posts, available_images=list_static_images())

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
    rows = NewsItem.query.order_by(NewsItem.published_at.desc().nullslast(), NewsItem.id.desc()).all(); return render_template("admin/news.html", rows=rows)

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

@app.cli.command("init-db")
def init_db():
    with app.app_context():
        db.create_all(); print("Database initialized.")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
