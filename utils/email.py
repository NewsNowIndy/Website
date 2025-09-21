import smtplib
from email.message import EmailMessage
def send_email_smtp(host, port, use_tls, username, password, sender, to_list, subject, html):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.set_content("This email requires an HTML-capable client.")
    msg.add_alternative(html, subtype="html")
    if use_tls:
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            if username: s.login(username, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as s:
            if username: s.login(username, password)
            s.send_message(msg)
