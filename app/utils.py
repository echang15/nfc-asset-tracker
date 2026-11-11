import csv
import io
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from functools import wraps

from flask import Response, abort, request, session
from flask_login import current_user

from .models import AuditLog, db


# --------------------------------------------------------------------------
# NFC UID handling
# --------------------------------------------------------------------------

_HEX_SEPARATORS = re.compile(r"[\s:\-]")


def normalize_uid(raw):
    """Normalise a tag UID so that the same tag always maps to the same string.

    Keyboard-wedge readers emit UIDs in different shapes (``04:A2:3B:..``,
    ``04 a2 3b``, ``04A23B``) - we strip separators and upper-case.  Purely
    decimal output (some readers are configured that way) is kept as-is.
    """
    if raw is None:
        return ""
    uid = _HEX_SEPARATORS.sub("", str(raw).strip())
    return uid.upper()


# --------------------------------------------------------------------------
# Security helpers
# --------------------------------------------------------------------------


def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf():
    """Validate CSRF for state-changing browser requests."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    sent = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
    expected = session.get("_csrf_token")
    if not expected or not sent or not secrets.compare_digest(sent, expected):
        abort(400, description="Invalid or missing CSRF token. Reload the page.")


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            from flask import current_app

            return current_app.login_manager.unauthorized()
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def audit(action, entity=None, details=None):
    entry = AuditLog(
        user_id=current_user.id if current_user.is_authenticated else None,
        action=action,
        entity_type=type(entity).__name__ if entity is not None else None,
        entity_id=getattr(entity, "id", None),
        details=details,
    )
    db.session.add(entry)


# --------------------------------------------------------------------------
# Parsing / formatting
# --------------------------------------------------------------------------


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def local_date_to_utc(d, end_of_day=False):
    """Convert a local calendar date to a naive-UTC datetime boundary."""
    if d is None:
        return None
    t = datetime.combine(d, datetime.max.time() if end_of_day else datetime.min.time())
    return t.astimezone().astimezone(timezone.utc).replace(tzinfo=None)


def to_local(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone()


def format_dt(dt, fmt="%Y-%m-%d %H:%M"):
    local = to_local(dt)
    return local.strftime(fmt) if local else ""


def format_duration(td):
    if td is None:
        return ""
    if isinstance(td, (int, float)):
        td = timedelta(seconds=td)
    total = int(td.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def csv_response(filename, header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow(
            [
                format_dt(v) if isinstance(v, datetime)
                else v.isoformat() if isinstance(v, date)
                else "" if v is None
                else v
                for v in row
            ]
        )
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def humanize(value):
    return (value or "").replace("_", " ").title()
