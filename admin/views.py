# admin/views.py
import os
from flask import Blueprint, render_template, request, redirect, url_for, session, flash

admin_bp = Blueprint("admin", __name__, template_folder="templates")

PASSCODE = os.getenv("ADMIN_PASSCODE", "")  # set on Render later

# Gate every /admin/* route except the login/logout/static endpoints
@admin_bp.before_app_request
def _admin_passcode_gate():
    from flask import request
    p = request.path or "/"
    if p.startswith("/admin"):
        # allow the login/logout pages themselves
        if p == "/admin/login" or p == "/admin/login/" or p == "/admin/logout":
            return
        # already unlocked?
        if session.get("admin_ok") is True:
            return
        # otherwise, send to login
        return redirect(url_for("admin.admin_login", next=request.full_path or "/admin/"))

@admin_bp.route("/login", methods=["GET", "POST"])
def admin_login():
    from flask import request
    if request.method == "POST":
        code = (request.form.get("passcode") or "").strip()
        if code == PASSCODE:
            session["admin_ok"] = True
            nxt = request.args.get("next") or "/admin/"
            # prevent open redirects
            if not nxt.startswith("/admin"):
                nxt = "/admin/"
            return redirect(nxt)
        flash("Invalid passcode", "danger")
    return render_template("admin/login.html")

@admin_bp.route("/logout", methods=["POST", "GET"])
def admin_logout():
    session.pop("admin_ok", None)
    return redirect(url_for("admin.admin_login"))

@admin_bp.route("/")
def admin_home():
    # your existing admin dashboard
    return render_template("admin/dashboard.html")
