import os, json, re, stripe, bleach, requests
from datetime import datetime, date
from urllib.parse import urlencode
from flask import Flask, render_template, request, redirect, url_for, flash, abort, jsonify, session
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, BooleanField, FloatField
from wtforms.validators import DataRequired, Email, Optional, URL as URLVal, NumberRange
from models import db, Post, Subscriber, ContactMessage, Donation, NewsItem
from config import Config
from utils.signal import send_signal_group
from utils.email import send_email_smtp
from utils.scraper import fetch_calendar_week

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
stripe.api_key = app.config["STRIPE_SECRET_KEY"]

ALLOWED_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + ["p","img","video","audio","source","figure","figcaption","h1","h2","h3","h4","h5","h6","blockquote","pre","code","hr","br","strong","em","ul","ol","li","a","table","thead","tbody","tr","th","td","span"]
ALLOWED_ATTRS = {**bleach.sanitizer.ALLOWED_ATTRIBUTES, "img":["src","alt","title","loading"], "a":["href","title","target","rel"], "video":["src","controls","poster"], "audio":["src","controls"], "source":["src","type"], "span":["class"]}
ALLOWED_PROTOCOLS = ["http","https","mailto","tel"]

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
    hero_image_url = StringField("Hero image URL", validators=[Optional(), URLVal()])
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
        db.session.add(Post(title=form.title.data, slug=form.slug.data, summary=form.summary.data or None, content=sanitize_html(form.content.data), hero_image_url=form.hero_image_url.data or None, published=form.published.data))
        db.session.commit(); flash("Post saved.", "success"); return redirect(url_for("admin_posts"))
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return render_template("admin/posts.html", form=form, posts=posts)

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
