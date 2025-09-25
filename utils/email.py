import smtplib, ssl
from email.message import EmailMessage

def send_email_smtp(
    host, port, use_tls, username, password, sender, to_list, subject, html,
    *, content_type: str = "html", text_body: str | None = None
):
    """
    Send an email via SMTP.

    Backwards-compatible:
      - Existing calls still pass `html` and get HTML emails with a plain-text fallback line.
    New usage:
      - content_type="plain": send text/plain only (good for email-to-SMS).
      - content_type="both":  send multipart/alternative with real plain text + HTML.
      - text_body: optional explicit plain text when you choose html/both.
    """
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject

    # Normalize inputs
    html_body = html or ""
    plain_fallback = text_body or "This email requires an HTML-capable client."

    if content_type == "plain":
        # SMS-friendly: send ONLY text/plain
        msg.set_content(text_body or html_body, subtype="plain")
    elif content_type == "both":
        # Proper multipart/alternative
        msg.set_content(text_body or plain_fallback, subtype="plain")
        msg.add_alternative(html_body, subtype="html")
    else:
        # Default (fully backward-compatible with your current behavior)
        msg.set_content(plain_fallback, subtype="plain")
        msg.add_alternative(html_body, subtype="html")

    if use_tls:
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            if username:
                s.login(username, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as s:
            if username:
                s.login(username, password)
            s.send_message(msg)
