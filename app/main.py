from datetime import timedelta

from flask import Blueprint, abort, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from .models import (
    ASSET_STATUSES,
    STATUS_CHECKED_OUT,
    Asset,
    Transaction,
    User,
    db,
    utcnow,
)

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def index():
    if current_user.is_admin:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("scan.scan_page"))


@bp.route("/dashboard")
@login_required
def dashboard():
    if not current_user.is_admin:
        return redirect(url_for("main.my_items"))
    now = utcnow()
    status_counts = dict(
        db.session.query(Asset.status, func.count(Asset.id)).group_by(Asset.status).all()
    )
    counts = {s: status_counts.get(s, 0) for s in ASSET_STATUSES}
    overdue = (
        Asset.query.filter(Asset.status == STATUS_CHECKED_OUT, Asset.due_at < now)
        .order_by(Asset.due_at)
        .all()
    )
    due_soon = (
        Asset.query.filter(
            Asset.status == STATUS_CHECKED_OUT,
            Asset.due_at >= now,
            Asset.due_at < now + timedelta(days=2),
        )
        .order_by(Asset.due_at)
        .all()
    )
    recent = Transaction.query.order_by(Transaction.timestamp.desc()).limit(15).all()
    since = now - timedelta(days=1)
    today_out = Transaction.query.filter(
        Transaction.timestamp >= since, Transaction.action == "check_out"
    ).count()
    today_in = Transaction.query.filter(
        Transaction.timestamp >= since, Transaction.action == "check_in"
    ).count()
    return render_template(
        "main/dashboard.html",
        counts=counts,
        total=sum(counts.values()),
        overdue=overdue,
        due_soon=due_soon,
        recent=recent,
        today_out=today_out,
        today_in=today_in,
        user_count=User.query.filter_by(is_active_flag=True).count(),
        untagged=Asset.query.filter(Asset.nfc_uid.is_(None)).count(),
    )


@bp.route("/my-items")
@login_required
def my_items():
    items = (
        Asset.query.filter_by(holder_id=current_user.id, status=STATUS_CHECKED_OUT)
        .order_by(Asset.due_at)
        .all()
    )
    history = (
        Transaction.query.filter_by(user_id=current_user.id)
        .order_by(Transaction.timestamp.desc())
        .limit(50)
        .all()
    )
    return render_template("main/my_items.html", items=items, history=history)


@bp.route("/assets/<int:asset_id>")
@login_required
def asset_detail(asset_id):
    asset = db.session.get(Asset, asset_id) or abort(404)
    return render_template("main/asset_detail.html", asset=asset)
