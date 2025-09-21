from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False)
    summary = db.Column(db.Text, nullable=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    published = db.Column(db.Boolean, default=True, index=True)
    hero_image_url = db.Column(db.String(500), nullable=True)
    media_json = db.Column(db.Text, nullable=True)

class Subscriber(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    first_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ContactMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=True)
    email = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(255), nullable=True)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_spam = db.Column(db.Boolean, default=False)

class Donation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Integer, nullable=False)  # cents
    donor_name = db.Column(db.String(200), nullable=True)
    donor_email = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    provider = db.Column(db.String(50), nullable=True)
    provider_ref = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(50), default="succeeded")

class NewsItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)
    link = db.Column(db.String(500), nullable=False)
    source = db.Column(db.String(200), nullable=True)
    published_at = db.Column(db.DateTime, index=True, nullable=True)
    summary = db.Column(db.Text, nullable=True)
    seen = db.Column(db.Boolean, default=False, index=True)

class CalendarEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(400), nullable=False)
    start = db.Column(db.DateTime, nullable=True, index=True)
    end = db.Column(db.DateTime, nullable=True)
    location = db.Column(db.String(400), nullable=True)
    link = db.Column(db.String(600), nullable=True)
    week_key = db.Column(db.String(16), index=True)
    raw_source = db.Column(db.String(50), default="calendar.indy.gov")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
