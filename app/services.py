"""Core check-in / check-out business logic, shared by the scan UI, the
manual admin forms and the JSON API."""

from dataclasses import dataclass, field
from datetime import timedelta

from .models import (
    ACTION_CHECK_IN,
    ACTION_CHECK_OUT,
    STATUS_AVAILABLE,
    STATUS_CHECKED_OUT,
    Asset,
    Transaction,
    User,
    db,
    get_bool_setting,
    get_int_setting,
    utcnow,
)
from .utils import format_dt, humanize, normalize_uid

MODE_AUTO = "auto"
MODE_OUT = "out"
MODE_IN = "in"
SCAN_MODES = [MODE_AUTO, MODE_OUT, MODE_IN]


class CheckoutError(Exception):
    pass


@dataclass
class ScanResult:
    ok: bool
    result: str  # checked_out | checked_in | badge | unknown | error
    message: str
    uid: str = ""
    asset: Asset | None = None
    user: User | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        data = {
            "ok": self.ok,
            "result": self.result,
            "message": self.message,
            "uid": self.uid,
        }
        if self.asset is not None:
            a = self.asset
            data["asset"] = {
                "id": a.id,
                "asset_tag": a.asset_tag,
                "name": a.name,
                "status": a.status,
                "holder": a.holder.full_name if a.holder else None,
                "due_at": format_dt(a.due_at) if a.due_at else None,
            }
        if self.user is not None:
            data["user"] = {"id": self.user.id, "full_name": self.user.full_name}
        data.update(self.extra)
        return data


def loan_days_for(asset):
    if asset.category and asset.category.default_loan_days is not None:
        return asset.category.default_loan_days
    return get_int_setting("default_loan_days", 7)


def default_due_for(asset):
    days = loan_days_for(asset)
    return utcnow() + timedelta(days=days) if days and days > 0 else None


def check_out(asset, user, performed_by=None, due_at=None, method="scan",
              notes=None, use_default_due=True):
    if asset.status != STATUS_AVAILABLE:
        raise CheckoutError(
            f"{asset.name} ({asset.asset_tag}) is {humanize(asset.status).lower()} "
            "and cannot be checked out."
        )
    if not user.is_active:
        raise CheckoutError(f"User {user.full_name} is deactivated.")
    if due_at is None and use_default_due:
        due_at = default_due_for(asset)
    now = utcnow()
    asset.status = STATUS_CHECKED_OUT
    asset.holder = user
    asset.checked_out_at = now
    asset.due_at = due_at
    tx = Transaction(
        asset=asset,
        user=user,
        performed_by=performed_by or user,
        action=ACTION_CHECK_OUT,
        timestamp=now,
        due_at=due_at,
        condition=asset.condition,
        method=method,
        notes=notes,
    )
    db.session.add(tx)
    return tx


def check_in(asset, performed_by, condition=None, method="scan", notes=None):
    if asset.status != STATUS_CHECKED_OUT or asset.holder is None:
        raise CheckoutError(f"{asset.name} ({asset.asset_tag}) is not checked out.")
    borrower = asset.holder
    if condition:
        asset.condition = condition
    tx = Transaction(
        asset=asset,
        user=borrower,
        performed_by=performed_by,
        action=ACTION_CHECK_IN,
        timestamp=utcnow(),
        condition=asset.condition,
        method=method,
        notes=notes,
    )
    asset.status = STATUS_AVAILABLE
    asset.holder = None
    asset.checked_out_at = None
    asset.due_at = None
    db.session.add(tx)
    return tx


def process_scan(raw_uid, actor, mode=MODE_AUTO):
    """Handle a tag scanned by ``actor`` and return a ScanResult.

    The caller is responsible for committing the session.
    """
    uid = normalize_uid(raw_uid)
    if not uid:
        return ScanResult(False, "error", "Empty scan.", uid)

    asset = Asset.query.filter_by(nfc_uid=uid).first()
    if asset is None:
        badge_user = User.query.filter_by(badge_uid=uid).first()
        if badge_user is not None:
            return ScanResult(
                True, "badge", f"Badge of {badge_user.full_name}.", uid,
                user=badge_user,
            )
        msg = "Unknown tag."
        if actor.is_admin:
            msg += " You can register it as a new asset."
        else:
            msg += " Ask an administrator to register it."
        return ScanResult(
            False, "unknown", msg, uid,
            extra={"can_register": bool(actor.is_admin)},
        )

    try:
        if asset.status == STATUS_CHECKED_OUT:
            if mode == MODE_OUT:
                holder = asset.holder.full_name if asset.holder else "someone"
                raise CheckoutError(
                    f"{asset.name} is already checked out to {holder}."
                )
            if (asset.holder_id != actor.id and not actor.is_admin
                    and not get_bool_setting("users_can_return_others")):
                raise CheckoutError(
                    f"{asset.name} is checked out to {asset.holder.full_name}. "
                    "Only they or an administrator can return it."
                )
            borrower = asset.holder
            check_in(asset, actor)
            msg = f"Checked in {asset.name} ({asset.asset_tag})."
            if borrower.id != actor.id:
                msg += f" Returned on behalf of {borrower.full_name}."
            return ScanResult(True, "checked_in", msg, uid, asset=asset, user=borrower)

        if asset.status == STATUS_AVAILABLE:
            if mode == MODE_IN:
                raise CheckoutError(f"{asset.name} is not checked out.")
            check_out(asset, actor)
            msg = f"Checked out {asset.name} ({asset.asset_tag}) to {actor.full_name}."
            if asset.due_at:
                msg += f" Due {format_dt(asset.due_at, '%a %b %d, %H:%M')}."
            return ScanResult(True, "checked_out", msg, uid, asset=asset, user=actor)

        raise CheckoutError(
            f"{asset.name} is marked {humanize(asset.status).lower()}. "
            "Contact an administrator."
        )
    except CheckoutError as exc:
        return ScanResult(False, "error", str(exc), uid, asset=asset)
