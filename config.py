import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-prod")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///newsnowindy.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAIL_SERVER = os.getenv("MAIL_SERVER", "")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_FROM = os.getenv("MAIL_FROM", "no-reply@newsnowindy.com")

    SIGNAL_CLI_BIN = os.getenv("SIGNAL_CLI_BIN", "signal-cli")
    SIGNAL_SENDER = os.getenv("SIGNAL_SENDER", "")
    SIGNAL_GROUP  = (
        os.getenv("SIGNAL_GROUP")
        or os.getenv("GROUP_ID")
        or os.getenv("SIGNAL_GROUP_ID")
    )

    RSS_JSON = os.getenv("RSS_JSON", "https://rss.app/feeds/v1.1/_d1kx5CfdZnJEqXr0.json")
    RSS_XML = os.getenv("RSS_XML", "https://rss.app/feeds/_d1kx5CfdZnJEqXr0.xml")
    RSS_CSV = os.getenv("RSS_CSV", "https://rss.app/feeds/_d1kx5CfdZnJEqXr0.csv")

    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

    STRIPE_PUBLIC_KEY = os.getenv("STRIPE_PUBLIC_KEY", "")
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    DONATION_SUCCESS_URL = os.getenv("DONATION_SUCCESS_URL", "http://localhost:5000/donate/success")
    DONATION_CANCEL_URL = os.getenv("DONATION_CANCEL_URL", "http://localhost:5000/donate/cancel")

    TURNSTILE_SITE_KEY = os.getenv("TURNSTILE_SITE_KEY", "")
    TURNSTILE_SECRET = os.getenv("TURNSTILE_SECRET", "")

    TINYMCE_API_KEY = os.getenv("TINYMCE_API_KEY", "")

    INDY_CAL_RSS_URL = os.getenv("INDY_CAL_RSS_URL")

    FEED_URL = os.getenv("FEED_URL") or (
        "https://rss-bridge.org/bridge01/?action=display&bridge=FeedMergeBridge"
        "&feed_name=Crime+News"
        "&feed_1=https%3A%2F%2Fwww.wthr.com%2Ffeeds%2Fsyndication%2Frss%2Fnews%2Fcrime"
        "&feed_2=https%3A%2F%2Ffox59.com%2Fnews%2Findycrime%2Ffeed%2F"
        "&feed_3=https%3A%2F%2Fwww.wrtv.com%2Fnews%2Flocal-news%2Fcrime.rss"
        "&feed_4=https%3A%2Findypolitics.org%2Ffeed%2F"
        "&format=Atom"
    )
