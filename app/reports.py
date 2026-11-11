from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, render_template, request
from sqlalchemy import func

from .models import (
    ACTION_CHECK_IN,
    ACTION_CHECK_OUT,
    ASSET_STATUSES,
    STATUS_CHECKED_OUT,
    STATUS_RETIRED,
    Asset,
    Category,
    Location,
    Transaction,
    User,
    db,
    utcnow,
)
from .utils import (
    admin_required,
    csv_response,
    format_duration,
    humanize,
    local_date_to_utc,
    parse_date,
)

bp = Blueprint("reports", __name__)

REPORTS = [
    ("reports.checked_out", "Currently checked out", "Everything that is out right now, who has it and when it is due."),
    ("reports.overdue", "Overdue items", "Checked-out assets past their due date."),
    ("reports.history", "Transaction history", "Every check-in / check-out, filterable by date, user, asset and action."),
    ("reports.utilization", "Asset utilization", "How often and how long each asset was in use during a period."),
    ("reports.user_activity", "User activity", "Check-outs, returns, late returns and current holdings per user."),
    ("reports.inventory", "Inventory summary", "Asset counts and value by category, location and status."),
    ("reports.idle", "Idle assets", "Assets that have not been checked out for a given number of days."),
    ("reports.warranty", "Warranty expiring", "Assets whose warranty expires soon or has expired."),
]


@bp.before_request
@admin_required
def _require_admin():
    return None


def _want_csv():
    return request.args.get("format") == "csv"


def _date_range(default_days=30):
    today = date.today()
    start = parse_date(request.args.get("start")) or today - timedelta(days=default_days)
    end = parse_date(request.args.get("end")) or today
    if start > end:
        start, end = end, start
    return start, end


@bp.route("/")
def index():
    return render_template("reports/index.html", reports=REPORTS)


# --------------------------------------------------------------------------


def _checked_out_rows(overdue_only):
    now = utcnow()
    q = Asset.query.filter(Asset.status == STATUS_CHECKED_OUT)
    if overdue_only:
        q = q.filter(Asset.due_at < now)
    return q.order_by(Asset.due_at.is_(None), Asset.due_at).all()


def _checked_out_view(overdue_only, title, filename):
    rows = _checked_out_rows(overdue_only)
    now = utcnow()
    if _want_csv():
        return csv_response(
            filename,
            ["Asset tag", "Name", "Category", "Holder", "Username", "Email",
             "Checked out", "Due", "Days overdue"],
            [
                [a.asset_tag, a.name, a.category.name if a.category else "",
                 a.holder.full_name if a.holder else "",
                 a.holder.username if a.holder else "",
                 a.holder.email if a.holder else "",
                 a.checked_out_at, a.due_at,
                 (now - a.due_at).days if a.is_overdue else ""]
                for a in rows
            ],
        )
    return render_template("reports/checked_out.html", rows=rows, title=title, now=now)


@bp.route("/checked-out")
def checked_out():
    return _checked_out_view(False, "Currently checked out", "checked_out.csv")


@bp.route("/overdue")
def overdue():
    return _checked_out_view(True, "Overdue items", "overdue.csv")


# --------------------------------------------------------------------------


@bp.route("/history")
def history():
    start, end = _date_range()
    user_id = request.args.get("user", type=int)
    asset_q = request.args.get("asset", "").strip()
    action = request.args.get("action", "")
    q = Transaction.query.filter(
        Transaction.timestamp >= local_date_to_utc(start),
        Transaction.timestamp <= local_date_to_utc(end, end_of_day=True),
    )
    if user_id:
        q = q.filter(Transaction.user_id == user_id)
    if action in (ACTION_CHECK_IN, ACTION_CHECK_OUT):
        q = q.filter(Transaction.action == action)
    if asset_q:
        like = f"%{asset_q}%"
        q = q.join(Asset).filter(db.or_(Asset.asset_tag.ilike(like), Asset.name.ilike(like)))
    q = q.order_by(Transaction.timestamp.desc())
    if _want_csv():
        return csv_response(
            "transactions.csv",
            ["Timestamp", "Action", "Asset tag", "Asset", "User", "Performed by",
             "Method", "Due", "Condition", "Notes"],
            [
                [t.timestamp, humanize(t.action), t.asset.asset_tag, t.asset.name,
                 t.user.full_name, t.performed_by.full_name if t.performed_by else "",
                 t.method, t.due_at, t.condition, t.notes]
                for t in q.all()
            ],
        )
    pagination = q.paginate(page=request.args.get("page", 1, type=int),
                            per_page=100, error_out=False)
    return render_template(
        "reports/history.html", pagination=pagination, start=start, end=end,
        user_id=user_id, asset_q=asset_q, action=action,
        users=User.query.order_by(User.full_name).all(),
    )


# --------------------------------------------------------------------------


def _loan_periods(range_start, range_end):
    """Pair check-out/check-in transactions into loan periods.

    Returns a list of dicts with asset_id, user_id, out_at, in_at (None if still
    out) and due_at, for loans overlapping [range_start, range_end].
    """
    txs = (
        Transaction.query.filter(Transaction.timestamp <= range_end)
        .order_by(Transaction.asset_id, Transaction.timestamp, Transaction.id)
        .all()
    )
    periods, open_loans = [], {}
    for t in txs:
        if t.action == ACTION_CHECK_OUT:
            open_loans[t.asset_id] = t
        elif t.action == ACTION_CHECK_IN and t.asset_id in open_loans:
            out = open_loans.pop(t.asset_id)
            if t.timestamp >= range_start:
                periods.append(dict(asset_id=t.asset_id, user_id=out.user_id,
                                    out_at=out.timestamp, in_at=t.timestamp,
                                    due_at=out.due_at))
    for out in open_loans.values():
        periods.append(dict(asset_id=out.asset_id, user_id=out.user_id,
                            out_at=out.timestamp, in_at=None, due_at=out.due_at))
    return periods


@bp.route("/utilization")
def utilization():
    start, end = _date_range()
    rs, re_ = local_date_to_utc(start), local_date_to_utc(end, end_of_day=True)
    now = utcnow()
    window_end = min(re_, now)
    window = max((window_end - rs).total_seconds(), 1)
    stats = defaultdict(lambda: {"checkouts": 0, "seconds": 0.0, "users": set()})
    for p in _loan_periods(rs, re_):
        s = max(p["out_at"], rs)
        e = min(p["in_at"] or now, window_end)
        if e <= s and not (rs <= p["out_at"] <= re_):
            continue
        st = stats[p["asset_id"]]
        if rs <= p["out_at"] <= re_:
            st["checkouts"] += 1
        st["seconds"] += max((e - s).total_seconds(), 0)
        st["users"].add(p["user_id"])
    category_id = request.args.get("category", type=int)
    aq = Asset.query.filter(Asset.status != STATUS_RETIRED)
    if category_id:
        aq = aq.filter(Asset.category_id == category_id)
    rows = []
    for a in aq.order_by(Asset.asset_tag).all():
        st = stats.get(a.id, {"checkouts": 0, "seconds": 0.0, "users": set()})
        rows.append(dict(asset=a, checkouts=st["checkouts"], seconds=st["seconds"],
                         users=len(st["users"]),
                         pct=round(100 * st["seconds"] / window, 1)))
    sort = request.args.get("sort", "pct")
    if sort in ("pct", "checkouts", "users"):
        rows.sort(key=lambda r: r[sort], reverse=True)
    if _want_csv():
        return csv_response(
            f"utilization_{start}_{end}.csv",
            ["Asset tag", "Name", "Category", "Checkouts", "Distinct users",
             "Time out", "Hours out", "Utilization %"],
            [[r["asset"].asset_tag, r["asset"].name,
              r["asset"].category.name if r["asset"].category else "",
              r["checkouts"], r["users"], format_duration(r["seconds"]),
              round(r["seconds"] / 3600, 1), r["pct"]] for r in rows],
        )
    return render_template("reports/utilization.html", rows=rows, start=start,
                           end=end, sort=sort, category_id=category_id,
                           categories=Category.query.order_by(Category.name).all())


@bp.route("/user-activity")
def user_activity():
    start, end = _date_range()
    rs, re_ = local_date_to_utc(start), local_date_to_utc(end, end_of_day=True)
    now = utcnow()
    stats = defaultdict(lambda: {"checkouts": 0, "returns": 0, "late": 0, "seconds": 0.0})
    for p in _loan_periods(rs, re_):
        st = stats[p["user_id"]]
        if rs <= p["out_at"] <= re_:
            st["checkouts"] += 1
        if p["in_at"] and rs <= p["in_at"] <= re_:
            st["returns"] += 1
            if p["due_at"] and p["in_at"] > p["due_at"]:
                st["late"] += 1
        s, e = max(p["out_at"], rs), min(p["in_at"] or now, re_, now)
        st["seconds"] += max((e - s).total_seconds(), 0)
    holding = dict(
        db.session.query(Asset.holder_id, func.count(Asset.id))
        .filter(Asset.status == STATUS_CHECKED_OUT).group_by(Asset.holder_id).all()
    )
    overdue = dict(
        db.session.query(Asset.holder_id, func.count(Asset.id))
        .filter(Asset.status == STATUS_CHECKED_OUT, Asset.due_at < now)
        .group_by(Asset.holder_id).all()
    )
    rows = []
    for u in User.query.order_by(User.full_name).all():
        st = stats.get(u.id, {"checkouts": 0, "returns": 0, "late": 0, "seconds": 0.0})
        if not (st["checkouts"] or st["returns"] or holding.get(u.id)) and \
                request.args.get("all") != "1":
            continue
        rows.append(dict(user=u, holding=holding.get(u.id, 0),
                         overdue=overdue.get(u.id, 0), **st))
    rows.sort(key=lambda r: (r["checkouts"], r["holding"]), reverse=True)
    if _want_csv():
        return csv_response(
            f"user_activity_{start}_{end}.csv",
            ["User", "Username", "Department", "Checkouts", "Returns",
             "Late returns", "Currently holding", "Overdue now", "Hours out"],
            [[r["user"].full_name, r["user"].username, r["user"].department,
              r["checkouts"], r["returns"], r["late"], r["holding"], r["overdue"],
              round(r["seconds"] / 3600, 1)] for r in rows],
        )
    return render_template("reports/user_activity.html", rows=rows, start=start,
                           end=end, show_all=request.args.get("all") == "1")


@bp.route("/inventory")
def inventory():
    def matrix(group_col, model):
        data = (
            db.session.query(group_col, Asset.status, func.count(Asset.id),
                             func.coalesce(func.sum(Asset.purchase_cost), 0))
            .group_by(group_col, Asset.status).all()
        )
        names = {m.id: m.name for m in model.query.all()}
        out = {}
        for gid, status, count, value in data:
            name = names.get(gid, "(none)")
            row = out.setdefault(name, {"total": 0, "value": 0.0,
                                        **{s: 0 for s in ASSET_STATUSES}})
            row[status] = count
            row["total"] += count
            row["value"] += float(value or 0)
        return dict(sorted(out.items()))

    by_category = matrix(Asset.category_id, Category)
    by_location = matrix(Asset.location_id, Location)
    if _want_csv():
        rows = []
        for group, data in (("Category", by_category), ("Location", by_location)):
            for name, r in data.items():
                rows.append([group, name, *[r[s] for s in ASSET_STATUSES],
                             r["total"], round(r["value"], 2)])
        return csv_response("inventory.csv",
                            ["Group by", "Name", *[humanize(s) for s in ASSET_STATUSES],
                             "Total", "Value"], rows)
    return render_template("reports/inventory.html", by_category=by_category,
                           by_location=by_location, statuses=ASSET_STATUSES)


@bp.route("/idle")
def idle():
    days = request.args.get("days", 30, type=int) or 30
    cutoff = utcnow() - timedelta(days=days)
    last_out = dict(
        db.session.query(Transaction.asset_id, func.max(Transaction.timestamp))
        .filter(Transaction.action == ACTION_CHECK_OUT)
        .group_by(Transaction.asset_id).all()
    )
    rows = []
    for a in Asset.query.filter(Asset.status.notin_([STATUS_RETIRED, STATUS_CHECKED_OUT]))\
            .order_by(Asset.asset_tag).all():
        last = last_out.get(a.id)
        if last is None or last < cutoff:
            rows.append(dict(asset=a, last=last))
    rows.sort(key=lambda r: (r["last"] is not None, r["last"] or 0))
    if _want_csv():
        return csv_response(
            f"idle_{days}d.csv",
            ["Asset tag", "Name", "Category", "Location", "Status", "Last checked out"],
            [[r["asset"].asset_tag, r["asset"].name,
              r["asset"].category.name if r["asset"].category else "",
              r["asset"].location.name if r["asset"].location else "",
              humanize(r["asset"].status), r["last"] or "never"] for r in rows],
        )
    return render_template("reports/idle.html", rows=rows, days=days)


@bp.route("/warranty")
def warranty():
    days = request.args.get("days", 90, type=int) or 90
    horizon = date.today() + timedelta(days=days)
    rows = (
        Asset.query.filter(Asset.warranty_expires.isnot(None),
                           Asset.warranty_expires <= horizon,
                           Asset.status != STATUS_RETIRED)
        .order_by(Asset.warranty_expires).all()
    )
    if _want_csv():
        return csv_response(
            f"warranty_{days}d.csv",
            ["Asset tag", "Name", "Serial", "Manufacturer", "Warranty expires"],
            [[a.asset_tag, a.name, a.serial_number, a.manufacturer,
              a.warranty_expires] for a in rows],
        )
    return render_template("reports/warranty.html", rows=rows, days=days,
                           today=date.today())
