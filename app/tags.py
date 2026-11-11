"""Programming NFC tags (writing an asset or user ID onto a tag).

Flow:
1. An admin clicks "Program NFC tag" -> ``POST /admin/tags/program`` creates a
   pending TagWriteJob.
2. The next tag tapped on a PC/SC reader is pushed by reader_bridge.py to
   ``/api/reader/push``. Because a job is pending, the server answers with
   the text to write instead of treating the tap as a scan.
3. The bridge writes and verifies the tag, then reports to
   ``/api/reader/jobs/<id>/result``. On success the tag's UID is linked to the
   asset (``nfc_uid``) or user (``badge_uid``).
4. The browser polls ``/admin/tags/jobs/<id>`` to show progress.
"""
import secrets
from datetime import timedelta

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from .models import (
    JOB_CANCELLED,
    JOB_DONE,
    JOB_FAILED,
    JOB_PENDING,
    JOB_WRITING,
    STATUS_RETIRED,
    Asset,
    AuditLog,
    TagWriteJob,
    User,
    db,
    get_setting,
    utcnow,
)
from .utils import admin_required, normalize_uid

bp = Blueprint("tags", __name__)

JOB_TIMEOUT = timedelta(seconds=90)
BRIDGE_TIMEOUT = timedelta(seconds=20)

# Last contact from a reader bridge (single-process app, so memory is fine).
BRIDGE_STATUS = {"last_seen": None, "readers": [], "version": None}


def tag_text(kind, target):
    if kind == "asset":
        return f"asset:{target.asset_tag}"
    return f"user:{target.username}"


def parse_tag_text(text):
    """Split 'asset:A-00001' -> ('asset', 'A-00001'); None if not ours."""
    if not text or ":" not in text:
        return None
    kind, _, value = text.partition(":")
    kind = kind.strip().lower()
    if kind in ("asset", "user") and value.strip():
        return kind, value.strip()
    return None


def _target(kind, target_id):
    model = Asset if kind == "asset" else User if kind == "user" else None
    return db.session.get(model, target_id) if model else None


def _label(kind, target):
    if target is None:
        return "(deleted)"
    if kind == "asset":
        return f"{target.name} ({target.asset_tag})"
    return f"{target.full_name} ({target.username})"


def uid_owner(uid):
    """Return (kind, object) currently holding this tag UID, or None."""
    a = Asset.query.filter_by(nfc_uid=uid).first()
    if a:
        return "asset", a
    u = User.query.filter_by(badge_uid=uid).first()
    if u:
        return "user", u
    return None


def expire_stale_jobs():
    cutoff = utcnow() - JOB_TIMEOUT
    for job in TagWriteJob.query.filter(
        TagWriteJob.status.in_([JOB_PENDING, JOB_WRITING]),
        TagWriteJob.updated_at < cutoff,
    ):
        if job.status == JOB_PENDING:
            msg = "Timed out waiting for a tag. Is the reader bridge running?"
        else:
            msg = "The reader stopped responding while writing. Try again."
        _fail(job, msg)


def _fail(job, message):
    job.status = JOB_FAILED
    job.message = message
    job.updated_at = utcnow()


def claim_job_for_tap(uid):
    """Called for every tap pushed by the bridge.

    Returns a dict to send back to the bridge if a write job claims this tap,
    otherwise None (the tap is a normal scan).
    """
    expire_stale_jobs()
    job = (TagWriteJob.query.filter_by(status=JOB_PENDING)
           .order_by(TagWriteJob.id.desc()).first())
    if job is None:
        return None
    target = _target(job.kind, job.target_id)
    if target is None:
        _fail(job, "The asset or user was deleted.")
        return {"job_id": job.id, "error": job.message}
    owner = uid_owner(uid)
    if owner and not (owner[0] == job.kind and owner[1].id == target.id):
        _fail(job, f"This tag is already assigned to {owner[0]} {_label(*owner)}. "
                   "Remove it there first, or use a different tag.")
        return {"job_id": job.id, "error": job.message}
    job.status = JOB_WRITING
    job.uid = uid
    job.updated_at = utcnow()
    return {"job_id": job.id, "text": job.text}


def note_bridge_contact(data):
    BRIDGE_STATUS["last_seen"] = utcnow()
    if isinstance(data.get("readers"), list):
        BRIDGE_STATUS["readers"] = [str(r)[:120] for r in data["readers"]][:8]
    if data.get("version"):
        BRIDGE_STATUS["version"] = str(data["version"])[:20]


def bridge_connected():
    seen = BRIDGE_STATUS["last_seen"]
    return seen is not None and utcnow() - seen < BRIDGE_TIMEOUT


def check_api_key():
    key = request.headers.get("X-Api-Key", "")
    expected = get_setting("reader_api_key") or ""
    if not expected or not secrets.compare_digest(key, expected):
        abort(401)


# ---------------------------------------------------------------------------
# Bridge-facing endpoints (API key)
# ---------------------------------------------------------------------------


@bp.route("/api/reader/heartbeat", methods=["POST"])
def api_heartbeat():
    check_api_key()
    note_bridge_contact(request.get_json(silent=True) or {})
    return jsonify({"ok": True})


@bp.route("/api/reader/jobs/<int:job_id>/result", methods=["POST"])
def api_job_result(job_id):
    check_api_key()
    note_bridge_contact({})
    job = db.session.get(TagWriteJob, job_id) or abort(404)
    data = request.get_json(silent=True) or {}
    uid = normalize_uid(data.get("uid"))
    if job.status != JOB_WRITING or uid != job.uid:
        return jsonify({"ok": False, "error": "Job is no longer active."}), 409
    if not data.get("ok"):
        _fail(job, data.get("error") or "Writing the tag failed.")
        db.session.commit()
        return jsonify({"ok": True})

    target = _target(job.kind, job.target_id)
    owner = uid_owner(uid)
    if target is None:
        _fail(job, "The asset or user was deleted.")
    elif owner and not (owner[0] == job.kind and owner[1].id == target.id):
        _fail(job, f"Tag was written but is already assigned to {_label(*owner)}.")
    else:
        previous = target.nfc_uid if job.kind == "asset" else target.badge_uid
        if job.kind == "asset":
            target.nfc_uid = uid
        else:
            target.badge_uid = uid
        job.status = JOB_DONE
        job.updated_at = utcnow()
        job.message = f"Tag programmed with “{job.text}” and linked to {_label(job.kind, target)}."
        if previous and previous != uid:
            job.message += " The previous tag no longer works."
        db.session.add(AuditLog(
            user_id=job.created_by_id, action="program_tag",
            entity_type="Asset" if job.kind == "asset" else "User",
            entity_id=target.id, details=f"uid={uid} text={job.text}",
        ))
    db.session.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


def _job_json(job):
    return {
        "id": job.id,
        "status": job.status,
        "message": job.message,
        "uid": job.uid,
        "text": job.text,
        "bridge": bridge_connected(),
    }


@bp.route("/admin/tags/program", methods=["POST"])
@admin_required
def program():
    data = request.get_json(silent=True) or request.form
    kind = data.get("kind")
    try:
        target_id = int(data.get("id"))
    except (TypeError, ValueError):
        abort(400)
    if kind not in ("asset", "user"):
        abort(400)
    target = _target(kind, target_id) or abort(404)
    # Only one job at a time: cancel anything still open.
    for old in TagWriteJob.query.filter(TagWriteJob.status.in_([JOB_PENDING, JOB_WRITING])):
        old.status = JOB_CANCELLED
        old.updated_at = utcnow()
    job = TagWriteJob(kind=kind, target_id=target.id, text=tag_text(kind, target),
                      created_by_id=current_user.id)
    db.session.add(job)
    db.session.commit()
    return jsonify(_job_json(job))


@bp.route("/admin/tags/jobs/<int:job_id>")
@admin_required
def job_status(job_id):
    expire_stale_jobs()
    db.session.commit()
    job = db.session.get(TagWriteJob, job_id) or abort(404)
    return jsonify(_job_json(job))


@bp.route("/admin/tags/jobs/<int:job_id>/cancel", methods=["POST"])
@admin_required
def job_cancel(job_id):
    job = db.session.get(TagWriteJob, job_id) or abort(404)
    if job.status in (JOB_PENDING, JOB_WRITING):
        job.status = JOB_CANCELLED
        job.updated_at = utcnow()
        db.session.commit()
    return jsonify(_job_json(job))


@bp.route("/admin/tags/batch")
@admin_required
def batch():
    """Program tags for many assets in a row (e.g. when first setting up)."""
    which = request.args.get("which", "untagged")
    q = Asset.query.filter(Asset.status != STATUS_RETIRED)
    if which == "untagged":
        q = q.filter(Asset.nfc_uid.is_(None))
    assets = q.order_by(Asset.asset_tag).all()
    return render_template("admin/tag_batch.html", assets=assets, which=which,
                           bridge=bridge_connected())


@bp.route("/api/reader/status")
@login_required
def reader_status():
    return jsonify({"connected": bridge_connected(),
                    "readers": BRIDGE_STATUS["readers"]})
