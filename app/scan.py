import secrets
from datetime import timedelta

from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
)
from flask_login import current_user, login_required, login_user, logout_user

from .models import (
    STATUS_CHECKED_OUT,
    Asset,
    ReaderEvent,
    User,
    db,
    get_bool_setting,
    get_int_setting,
    get_setting,
    utcnow,
)
from .services import MODE_AUTO, SCAN_MODES, process_scan
from .utils import audit, normalize_uid

bp = Blueprint("scan", __name__)

READER_EVENT_TTL = timedelta(seconds=15)


@bp.route("/scan")
@login_required
def scan_page():
    mine = (
        Asset.query.filter_by(holder_id=current_user.id, status=STATUS_CHECKED_OUT)
        .order_by(Asset.due_at)
        .all()
    )
    latest = db.session.query(db.func.max(ReaderEvent.id)).scalar() or 0
    return render_template(
        "scan/scan.html",
        mine=mine,
        idle_seconds=get_int_setting("kiosk_idle_seconds", 60),
        reader_cursor=latest,
    )


@bp.route("/api/scan", methods=["POST"])
@login_required
def api_scan():
    data = request.get_json(silent=True) or request.form
    mode = data.get("mode", MODE_AUTO)
    if mode not in SCAN_MODES:
        mode = MODE_AUTO
    result = process_scan(data.get("uid", ""), current_user, mode=mode)
    if result.result == "badge":
        badge_user = result.user
        if badge_user.id == current_user.id:
            result.message = "That's your badge. Scan an asset tag to check it in or out."
        elif get_bool_setting("allow_badge_login") and badge_user.is_active:
            # Shared station: hand the session over to the badge owner.
            logout_user()
            login_user(badge_user)
            badge_user.last_login_at = utcnow()
            audit("login", badge_user, "method=badge-switch")
            result.extra["reload"] = True
            result.message = f"Welcome, {badge_user.full_name}."
        else:
            result.ok = False
            result.message = f"Badge of {badge_user.full_name}. Badge sign-in is disabled."
    db.session.commit()
    return jsonify(result.to_dict())


@bp.route("/api/reader/push", methods=["POST"])
def api_reader_push():
    """Endpoint used by reader_bridge.py. Authenticated by API key."""
    key = request.headers.get("X-Api-Key", "")
    expected = get_setting("reader_api_key") or ""
    if not expected or not secrets.compare_digest(key, expected):
        abort(401)
    data = request.get_json(silent=True) or {}
    uid = normalize_uid(data.get("uid"))
    if not uid:
        abort(400)
    db.session.add(ReaderEvent(uid=uid, station=(data.get("station") or "")[:80]))
    # Keep the table small.
    ReaderEvent.query.filter(ReaderEvent.created_at < utcnow() - timedelta(hours=1)).delete()
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/reader/poll")
@login_required
def api_reader_poll():
    """Browser polls for tag reads coming from the PC/SC bridge."""
    try:
        cursor = int(request.args.get("after", 0))
    except ValueError:
        cursor = 0
    if cursor < 0:
        latest = db.session.query(db.func.max(ReaderEvent.id)).scalar() or 0
        return jsonify({"cursor": latest, "uids": []})
    events = (
        ReaderEvent.query.filter(
            ReaderEvent.id > cursor,
            ReaderEvent.created_at >= utcnow() - READER_EVENT_TTL,
        )
        .order_by(ReaderEvent.id)
        .limit(10)
        .all()
    )
    latest = max([e.id for e in events], default=cursor)
    return jsonify({"cursor": latest, "uids": [e.uid for e in events]})


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@bp.route("/api/reader/badge-login", methods=["POST"])
def api_reader_badge_login():
    """Login page support for PC/SC readers: sign in with a badge read pushed by
    reader_bridge.py. Only honoured for browsers on the reader's own machine."""
    if request.remote_addr not in LOOPBACK or not get_bool_setting("allow_badge_login"):
        return jsonify({"ok": False, "cursor": 0})
    data = request.get_json(silent=True) or {}
    try:
        cursor = int(data.get("after", -1))
    except (TypeError, ValueError):
        cursor = -1
    latest = db.session.query(db.func.max(ReaderEvent.id)).scalar() or 0
    if cursor < 0:
        return jsonify({"ok": False, "cursor": latest})
    events = ReaderEvent.query.filter(
        ReaderEvent.id > cursor,
        ReaderEvent.created_at >= utcnow() - READER_EVENT_TTL,
    ).order_by(ReaderEvent.id).all()
    for ev in events:
        user = User.query.filter_by(badge_uid=ev.uid).first()
        if user and user.is_active:
            login_user(user)
            user.last_login_at = utcnow()
            audit("login", user, "method=badge-reader")
            db.session.commit()
            return jsonify({"ok": True, "cursor": latest})
    return jsonify({"ok": False, "cursor": latest,
                    "unknown": bool(events)})
