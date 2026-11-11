import csv
import io
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_

from .models import (
    ASSET_STATUSES,
    CONDITIONS,
    ROLE_ADMIN,
    ROLE_USER,
    ROLES,
    STATUS_AVAILABLE,
    STATUS_CHECKED_OUT,
    Asset,
    AuditLog,
    Category,
    Location,
    Transaction,
    User,
    db,
    get_setting,
    set_setting,
    utcnow,
)
from .services import CheckoutError, check_in, check_out, default_due_for
from .utils import admin_required, audit, local_date_to_utc, normalize_uid, parse_date

bp = Blueprint("admin", __name__)


@bp.before_request
@admin_required
def _require_admin():
    return None


def _paginate(query, per_page=50):
    page = max(request.args.get("page", 1, type=int), 1)
    return query.paginate(page=page, per_page=per_page, error_out=False)


# ==========================================================================
# Users
# ==========================================================================


@bp.route("/users")
def users():
    q = request.args.get("q", "").strip()
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(User.username.ilike(like), User.full_name.ilike(like),
                User.email.ilike(like), User.department.ilike(like))
        )
    show = request.args.get("show", "active")
    if show == "active":
        query = query.filter(User.is_active_flag.is_(True))
    elif show == "inactive":
        query = query.filter(User.is_active_flag.is_(False))
    pagination = _paginate(query.order_by(User.full_name))
    return render_template("admin/users.html", pagination=pagination, q=q, show=show)


def _user_from_form(user):
    errors = []
    username = request.form.get("username", "").strip()
    full_name = request.form.get("full_name", "").strip()
    if not username or not full_name:
        errors.append("Username and full name are required.")
    existing = User.query.filter_by(username=username).first()
    if existing and existing.id != user.id:
        errors.append("That username is already taken.")
    badge = normalize_uid(request.form.get("badge_uid")) or None
    if badge:
        clash = User.query.filter_by(badge_uid=badge).first()
        if clash and clash.id != user.id:
            errors.append(f"Badge is already assigned to {clash.full_name}.")
        if Asset.query.filter_by(nfc_uid=badge).first():
            errors.append("That tag is already registered to an asset.")
    role = request.form.get("role", ROLE_USER)
    if role not in ROLES:
        errors.append("Invalid role.")
    password = request.form.get("password", "")
    if password and len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if user.id == current_user.id:
        if role != ROLE_ADMIN:
            errors.append("You cannot remove your own admin role.")
        if not request.form.get("is_active"):
            errors.append("You cannot deactivate your own account.")
    if errors:
        return errors

    user.username = username
    user.full_name = full_name
    user.email = request.form.get("email", "").strip() or None
    user.department = request.form.get("department", "").strip() or None
    user.badge_uid = badge
    user.role = role
    user.is_active_flag = bool(request.form.get("is_active"))
    if password:
        user.set_password(password)
    return []


@bp.route("/users/new", methods=["GET", "POST"])
def user_new():
    user = User(role=ROLE_USER, is_active_flag=True)
    if request.method == "POST":
        errors = _user_from_form(user)
        if not errors:
            db.session.add(user)
            db.session.flush()
            audit("create_user", user, user.username)
            db.session.commit()
            flash(f"User {user.full_name} created.", "success")
            return redirect(url_for("admin.users"))
        for e in errors:
            flash(e, "danger")
    return render_template("admin/user_form.html", user=user, roles=ROLES)


@bp.route("/users/<int:user_id>", methods=["GET", "POST"])
def user_edit(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if request.method == "POST":
        errors = _user_from_form(user)
        if not errors:
            audit("update_user", user, user.username)
            db.session.commit()
            flash("User updated.", "success")
            return redirect(url_for("admin.user_edit", user_id=user.id))
        db.session.rollback()
        for e in errors:
            flash(e, "danger")
    held = Asset.query.filter_by(holder_id=user.id, status=STATUS_CHECKED_OUT).all()
    history = (
        Transaction.query.filter_by(user_id=user.id)
        .order_by(Transaction.timestamp.desc())
        .limit(25)
        .all()
    )
    return render_template("admin/user_form.html", user=user, roles=ROLES,
                           held=held, history=history)


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
def user_delete(user_id):
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        flash("You cannot delete your own account.", "danger")
    elif Transaction.query.filter(
        or_(Transaction.user_id == user.id, Transaction.performed_by_id == user.id)
    ).first():
        flash("This user has transaction history; deactivate the account instead.",
              "warning")
    else:
        audit("delete_user", user, user.username)
        db.session.delete(user)
        db.session.commit()
        flash("User deleted.", "success")
        return redirect(url_for("admin.users"))
    return redirect(url_for("admin.user_edit", user_id=user.id))


# ==========================================================================
# Assets
# ==========================================================================


@bp.route("/assets")
def assets():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    category_id = request.args.get("category", type=int)
    location_id = request.args.get("location", type=int)
    query = Asset.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Asset.asset_tag.ilike(like), Asset.name.ilike(like),
                Asset.serial_number.ilike(like), Asset.model.ilike(like),
                Asset.nfc_uid.ilike(like))
        )
    if status == "overdue":
        query = query.filter(Asset.status == STATUS_CHECKED_OUT, Asset.due_at < utcnow())
    elif status == "untagged":
        query = query.filter(Asset.nfc_uid.is_(None))
    elif status in ASSET_STATUSES:
        query = query.filter(Asset.status == status)
    if category_id:
        query = query.filter(Asset.category_id == category_id)
    if location_id:
        query = query.filter(Asset.location_id == location_id)
    pagination = _paginate(query.order_by(Asset.asset_tag))
    return render_template(
        "admin/assets.html",
        pagination=pagination,
        q=q,
        status=status,
        category_id=category_id,
        location_id=location_id,
        statuses=ASSET_STATUSES,
        categories=Category.query.order_by(Category.name).all(),
        locations=Location.query.order_by(Location.name).all(),
    )


def _asset_from_form(asset):
    errors = []
    tag = request.form.get("asset_tag", "").strip()
    name = request.form.get("name", "").strip()
    if not tag or not name:
        errors.append("Asset tag and name are required.")
    clash = Asset.query.filter_by(asset_tag=tag).first()
    if clash and clash.id != asset.id:
        errors.append("That asset tag is already in use.")
    uid = normalize_uid(request.form.get("nfc_uid")) or None
    if uid:
        clash = Asset.query.filter_by(nfc_uid=uid).first()
        if clash and clash.id != asset.id:
            errors.append(f"NFC tag already registered to {clash.asset_tag}.")
        if User.query.filter_by(badge_uid=uid).first():
            errors.append("That NFC tag is registered as a user badge.")
    status = request.form.get("status", asset.status or STATUS_AVAILABLE)
    if status not in ASSET_STATUSES:
        errors.append("Invalid status.")
    elif status == STATUS_CHECKED_OUT and asset.status != STATUS_CHECKED_OUT:
        errors.append("Use the Check out action to check an asset out.")
    elif asset.status == STATUS_CHECKED_OUT and status != STATUS_CHECKED_OUT:
        errors.append("Check the asset in before changing its status.")
    cost = request.form.get("purchase_cost", "").strip()
    cost_val = None
    if cost:
        try:
            cost_val = Decimal(cost)
        except InvalidOperation:
            errors.append("Purchase cost must be a number.")
    if errors:
        return errors

    asset.asset_tag = tag
    asset.name = name
    asset.nfc_uid = uid
    asset.serial_number = request.form.get("serial_number", "").strip() or None
    asset.model = request.form.get("model", "").strip() or None
    asset.manufacturer = request.form.get("manufacturer", "").strip() or None
    asset.category_id = request.form.get("category_id", type=int) or None
    asset.location_id = request.form.get("location_id", type=int) or None
    asset.status = status
    cond = request.form.get("condition")
    asset.condition = cond if cond in CONDITIONS else asset.condition
    asset.purchase_date = parse_date(request.form.get("purchase_date"))
    asset.warranty_expires = parse_date(request.form.get("warranty_expires"))
    asset.purchase_cost = cost_val
    asset.notes = request.form.get("notes", "").strip() or None
    return []


def _asset_form_ctx(asset):
    return dict(
        asset=asset,
        statuses=ASSET_STATUSES,
        conditions=CONDITIONS,
        categories=Category.query.order_by(Category.name).all(),
        locations=Location.query.order_by(Location.name).all(),
    )


def _next_asset_tag():
    n = Asset.query.count() + 1
    while Asset.query.filter_by(asset_tag=f"A-{n:05d}").first():
        n += 1
    return f"A-{n:05d}"


@bp.route("/assets/new", methods=["GET", "POST"])
def asset_new():
    asset = Asset(status=STATUS_AVAILABLE, condition="good",
                  nfc_uid=normalize_uid(request.args.get("uid")) or None,
                  asset_tag=_next_asset_tag())
    if request.method == "POST":
        errors = _asset_from_form(asset)
        if not errors:
            db.session.add(asset)
            db.session.flush()
            audit("create_asset", asset, asset.asset_tag)
            db.session.commit()
            flash(f"Asset {asset.asset_tag} created.", "success")
            if request.form.get("another"):
                return redirect(url_for("admin.asset_new"))
            return redirect(url_for("main.asset_detail", asset_id=asset.id))
        for e in errors:
            flash(e, "danger")
    return render_template("admin/asset_form.html", **_asset_form_ctx(asset))


@bp.route("/assets/<int:asset_id>/edit", methods=["GET", "POST"])
def asset_edit(asset_id):
    asset = db.session.get(Asset, asset_id) or abort(404)
    if request.method == "POST":
        errors = _asset_from_form(asset)
        if not errors:
            audit("update_asset", asset, asset.asset_tag)
            db.session.commit()
            flash("Asset updated.", "success")
            return redirect(url_for("main.asset_detail", asset_id=asset.id))
        db.session.rollback()
        for e in errors:
            flash(e, "danger")
    return render_template("admin/asset_form.html", **_asset_form_ctx(asset))


@bp.route("/assets/<int:asset_id>/delete", methods=["POST"])
def asset_delete(asset_id):
    asset = db.session.get(Asset, asset_id) or abort(404)
    if asset.transactions:
        flash("This asset has history; set its status to Retired instead.", "warning")
        return redirect(url_for("main.asset_detail", asset_id=asset.id))
    audit("delete_asset", asset, asset.asset_tag)
    db.session.delete(asset)
    db.session.commit()
    flash("Asset deleted.", "success")
    return redirect(url_for("admin.assets"))


@bp.route("/assets/<int:asset_id>/checkout", methods=["GET", "POST"])
def asset_checkout(asset_id):
    asset = db.session.get(Asset, asset_id) or abort(404)
    if request.method == "POST":
        user = db.session.get(User, request.form.get("user_id", type=int) or 0)
        due_date = parse_date(request.form.get("due_date"))
        if user is None:
            flash("Choose a user.", "danger")
        else:
            try:
                check_out(
                    asset, user, performed_by=current_user, method="manual",
                    due_at=local_date_to_utc(due_date, end_of_day=True),
                    use_default_due=False,
                    notes=request.form.get("notes", "").strip() or None,
                )
                db.session.commit()
                flash(f"{asset.name} checked out to {user.full_name}.", "success")
                return redirect(url_for("main.asset_detail", asset_id=asset.id))
            except CheckoutError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
    default_due = default_due_for(asset)
    return render_template(
        "admin/asset_checkout.html",
        asset=asset,
        users=User.query.filter_by(is_active_flag=True).order_by(User.full_name).all(),
        default_due=default_due,
    )


@bp.route("/assets/<int:asset_id>/checkin", methods=["POST"])
def asset_checkin(asset_id):
    asset = db.session.get(Asset, asset_id) or abort(404)
    cond = request.form.get("condition")
    try:
        check_in(asset, current_user, method="manual",
                 condition=cond if cond in CONDITIONS else None,
                 notes=request.form.get("notes", "").strip() or None)
        db.session.commit()
        flash(f"{asset.name} checked in.", "success")
    except CheckoutError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("main.asset_detail", asset_id=asset.id))


@bp.route("/assets/<int:asset_id>/extend", methods=["POST"])
def asset_extend(asset_id):
    asset = db.session.get(Asset, asset_id) or abort(404)
    due = parse_date(request.form.get("due_date"))
    if asset.status != STATUS_CHECKED_OUT or not due:
        flash("Asset must be checked out and a valid date given.", "danger")
    else:
        asset.due_at = local_date_to_utc(due, end_of_day=True)
        audit("extend_due", asset, f"new due {due.isoformat()}")
        db.session.commit()
        flash("Due date updated.", "success")
    return redirect(url_for("main.asset_detail", asset_id=asset.id))


IMPORT_COLUMNS = [
    "asset_tag", "name", "nfc_uid", "serial_number", "model", "manufacturer",
    "category", "location", "condition", "purchase_date", "purchase_cost",
    "warranty_expires", "notes",
]


@bp.route("/assets/import", methods=["GET", "POST"])
def asset_import():
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename:
            flash("Choose a CSV file.", "danger")
            return redirect(url_for("admin.asset_import"))
        text = upload.read().decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        created, updated, errors = 0, 0, []
        cat_cache = {c.name.lower(): c for c in Category.query.all()}
        loc_cache = {loc.name.lower(): loc for loc in Location.query.all()}
        for lineno, row in enumerate(reader, start=2):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items()
                   if isinstance(k, str) and isinstance(v, (str, type(None)))}
            tag, name = row.get("asset_tag"), row.get("name")
            if not tag or not name:
                errors.append(f"Line {lineno}: asset_tag and name are required.")
                continue
            asset = Asset.query.filter_by(asset_tag=tag).first()
            is_new = asset is None
            if is_new:
                asset = Asset(asset_tag=tag, status=STATUS_AVAILABLE)
            uid = normalize_uid(row.get("nfc_uid")) or None
            if uid:
                clash = Asset.query.filter_by(nfc_uid=uid).first()
                if clash and clash is not asset:
                    errors.append(f"Line {lineno}: NFC tag already used by {clash.asset_tag}.")
                    continue
                asset.nfc_uid = uid
            asset.name = name
            for col in ("serial_number", "model", "manufacturer", "notes"):
                if row.get(col):
                    setattr(asset, col, row[col])
            if row.get("condition") in CONDITIONS:
                asset.condition = row["condition"]
            for col in ("purchase_date", "warranty_expires"):
                if row.get(col):
                    asset.__setattr__(col, parse_date(row[col]))
            if row.get("purchase_cost"):
                try:
                    asset.purchase_cost = Decimal(row["purchase_cost"].replace("$", "").replace(",", ""))
                except InvalidOperation:
                    errors.append(f"Line {lineno}: bad purchase_cost (ignored).")
            if row.get("category"):
                key = row["category"].lower()
                if key not in cat_cache:
                    cat_cache[key] = Category(name=row["category"])
                    db.session.add(cat_cache[key])
                asset.category = cat_cache[key]
            if row.get("location"):
                key = row["location"].lower()
                if key not in loc_cache:
                    loc_cache[key] = Location(name=row["location"])
                    db.session.add(loc_cache[key])
                asset.location = loc_cache[key]
            if is_new:
                db.session.add(asset)
                created += 1
            else:
                updated += 1
            db.session.flush()
        audit("import_assets", None, f"created={created} updated={updated}")
        db.session.commit()
        flash(f"Import complete: {created} created, {updated} updated.", "success")
        for e in errors[:20]:
            flash(e, "warning")
        return redirect(url_for("admin.assets"))
    return render_template("admin/asset_import.html", columns=IMPORT_COLUMNS)


# ==========================================================================
# Categories & locations
# ==========================================================================


def _simple_crud(model, template, label):
    if request.method == "POST":
        action = request.form.get("action")
        item_id = request.form.get("id", type=int)
        name = request.form.get("name", "").strip()
        item = db.session.get(model, item_id) if item_id else None
        if action == "delete" and item:
            if item.assets:
                flash(f"Cannot delete a {label} that still has assets.", "danger")
            else:
                audit(f"delete_{label}", item, item.name)
                db.session.delete(item)
                db.session.commit()
                flash(f"{label.title()} deleted.", "success")
        elif action in ("create", "update"):
            clash = model.query.filter(db.func.lower(model.name) == name.lower()).first()
            if not name:
                flash("Name is required.", "danger")
            elif clash and clash is not item:
                flash(f"A {label} with that name already exists.", "danger")
            else:
                if item is None:
                    item = model(name=name)
                    db.session.add(item)
                item.name = name
                if model is Category:
                    days = request.form.get("default_loan_days", "").strip()
                    item.default_loan_days = int(days) if days.isdigit() else None
                db.session.flush()
                audit(f"{action}_{label}", item, name)
                db.session.commit()
                flash(f"{label.title()} saved.", "success")
        return redirect(request.path)
    items = model.query.order_by(model.name).all()
    return render_template(template, items=items)


@bp.route("/categories", methods=["GET", "POST"])
def categories():
    return _simple_crud(Category, "admin/categories.html", "category")


@bp.route("/locations", methods=["GET", "POST"])
def locations():
    return _simple_crud(Location, "admin/locations.html", "location")


# ==========================================================================
# Settings & audit log
# ==========================================================================


@bp.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        if request.form.get("action") == "regenerate_key":
            set_setting("reader_api_key", secrets.token_urlsafe(24))
            audit("regenerate_reader_key")
            db.session.commit()
            flash("Reader API key regenerated. Update reader_bridge.py.", "success")
            return redirect(url_for("admin.settings"))
        set_setting("org_name", request.form.get("org_name", "").strip() or "Asset Tracker")
        for key in ("default_loan_days", "kiosk_idle_seconds"):
            val = request.form.get(key, "").strip()
            if val.isdigit():
                set_setting(key, val)
        for key in ("allow_badge_login", "users_can_return_others"):
            set_setting(key, "1" if request.form.get(key) else "0")
        audit("update_settings")
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))
    keys = ["org_name", "default_loan_days", "kiosk_idle_seconds",
            "allow_badge_login", "users_can_return_others", "reader_api_key"]
    return render_template("admin/settings.html",
                           s={k: get_setting(k) for k in keys})


@bp.route("/audit")
def audit_log():
    query = AuditLog.query.order_by(AuditLog.timestamp.desc())
    action = request.args.get("action", "").strip()
    if action:
        query = query.filter(AuditLog.action == action)
    days = request.args.get("days", 30, type=int)
    if days:
        query = query.filter(AuditLog.timestamp >= utcnow() - timedelta(days=days))
    actions = [a for (a,) in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action)]
    return render_template("admin/audit.html", pagination=_paginate(query, 100),
                           actions=actions, action=action, days=days)


@bp.app_template_filter("date_input")
def _date_input(value):
    """Format a date / naive-UTC datetime for an <input type=date>."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        from .utils import to_local
        return to_local(value).strftime("%Y-%m-%d")
    return value.isoformat()
