from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .models import ROLE_ADMIN, User, db, get_bool_setting, utcnow
from .utils import audit, normalize_uid

bp = Blueprint("auth", __name__)


def _safe_next(default):
    target = request.args.get("next") or request.form.get("next")
    if target and urlparse(target).netloc == "" and target.startswith("/"):
        return target
    return default


def _finish_login(user, method):
    login_user(user)
    user.last_login_at = utcnow()
    audit("login", user, f"method={method}")
    db.session.commit()
    # Users go straight to the scan station, admins to the dashboard.
    default = url_for("main.dashboard") if user.is_admin else url_for("scan.scan_page")
    return redirect(_safe_next(default))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    badge_login = get_bool_setting("allow_badge_login")

    if request.method == "POST":
        if request.form.get("badge_uid"):
            if not badge_login:
                flash("Badge login is disabled.", "danger")
                return render_template("auth/login.html", badge_login=badge_login)
            uid = normalize_uid(request.form["badge_uid"])
            user = User.query.filter_by(badge_uid=uid).first() if uid else None
            if user and user.is_active:
                return _finish_login(user, "badge")
            flash("Badge not recognised or account disabled.", "danger")
        else:
            username = request.form.get("username", "").strip()
            user = User.query.filter_by(username=username).first()
            if user and user.is_active and user.check_password(
                request.form.get("password", "")
            ):
                return _finish_login(user, "password")
            flash("Invalid username or password.", "danger")

    return render_template("auth/login.html", badge_login=badge_login)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """First-run wizard: create the initial administrator."""
    if db.session.query(User.id).first() is not None:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        if not username or not full_name or len(password) < 8:
            flash("All fields are required; password must be at least 8 characters.",
                  "danger")
        elif password != request.form.get("confirm", ""):
            flash("Passwords do not match.", "danger")
        else:
            user = User(username=username, full_name=full_name, role=ROLE_ADMIN,
                        email=request.form.get("email", "").strip() or None)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Administrator account created. Welcome!", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/setup.html")


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        if current_user.password_hash and not current_user.check_password(current):
            flash("Current password is incorrect.", "danger")
        elif len(new) < 8:
            flash("New password must be at least 8 characters.", "danger")
        elif new != request.form.get("confirm", ""):
            flash("Passwords do not match.", "danger")
        else:
            current_user.set_password(new)
            audit("change_password", current_user)
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for("main.index"))
    return render_template("auth/change_password.html")
