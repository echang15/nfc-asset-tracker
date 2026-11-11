from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def utcnow():
    """Naive UTC timestamp (SQLite has no timezone support)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = [ROLE_ADMIN, ROLE_USER]

STATUS_AVAILABLE = "available"
STATUS_CHECKED_OUT = "checked_out"
STATUS_MAINTENANCE = "maintenance"
STATUS_LOST = "lost"
STATUS_RETIRED = "retired"
ASSET_STATUSES = [
    STATUS_AVAILABLE,
    STATUS_CHECKED_OUT,
    STATUS_MAINTENANCE,
    STATUS_LOST,
    STATUS_RETIRED,
]
CONDITIONS = ["new", "good", "fair", "poor", "damaged"]

ACTION_CHECK_OUT = "check_out"
ACTION_CHECK_IN = "check_in"


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120))
    department = db.Column(db.String(120))
    password_hash = db.Column(db.String(255))
    role = db.Column(db.String(20), nullable=False, default=ROLE_USER)
    badge_uid = db.Column(db.String(64), unique=True, index=True)
    is_active_flag = db.Column("is_active", db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime)

    held_assets = db.relationship(
        "Asset", back_populates="holder", foreign_keys="Asset.holder_id"
    )

    @property
    def is_active(self):  # used by Flask-Login
        return self.is_active_flag

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return bool(self.password_hash) and check_password_hash(
            self.password_hash, password
        )

    def __repr__(self):
        return f"<User {self.username}>"


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    default_loan_days = db.Column(db.Integer)  # overrides global default if set
    assets = db.relationship("Asset", back_populates="category")


class Location(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    assets = db.relationship("Asset", back_populates="location")


class Asset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    asset_tag = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    nfc_uid = db.Column(db.String(64), unique=True, index=True)
    serial_number = db.Column(db.String(120))
    model = db.Column(db.String(120))
    manufacturer = db.Column(db.String(120))
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"))
    location_id = db.Column(db.Integer, db.ForeignKey("location.id"))
    status = db.Column(db.String(20), nullable=False, default=STATUS_AVAILABLE)
    condition = db.Column(db.String(20), default="good")
    purchase_date = db.Column(db.Date)
    purchase_cost = db.Column(db.Numeric(12, 2))
    warranty_expires = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    holder_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    checked_out_at = db.Column(db.DateTime)
    due_at = db.Column(db.DateTime)

    category = db.relationship("Category", back_populates="assets")
    location = db.relationship("Location", back_populates="assets")
    holder = db.relationship(
        "User", back_populates="held_assets", foreign_keys=[holder_id]
    )
    transactions = db.relationship(
        "Transaction",
        back_populates="asset",
        order_by="Transaction.timestamp.desc()",
        cascade="all, delete-orphan",
    )

    @property
    def is_overdue(self):
        return (
            self.status == STATUS_CHECKED_OUT
            and self.due_at is not None
            and self.due_at < utcnow()
        )

    def __repr__(self):
        return f"<Asset {self.asset_tag}>"


class Transaction(db.Model):
    """A single check-out or check-in event."""

    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("asset.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    performed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(20), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    due_at = db.Column(db.DateTime)
    condition = db.Column(db.String(20))
    method = db.Column(db.String(20), default="scan")  # scan | manual
    notes = db.Column(db.Text)

    asset = db.relationship("Asset", back_populates="transactions")
    user = db.relationship("User", foreign_keys=[user_id])
    performed_by = db.relationship("User", foreign_keys=[performed_by_id])


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(60), nullable=False)
    entity_type = db.Column(db.String(40))
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)

    user = db.relationship("User")


class ReaderEvent(db.Model):
    """Tag reads pushed by reader_bridge.py (PC/SC readers)."""

    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(64), nullable=False)
    station = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


class Setting(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text)


DEFAULT_SETTINGS = {
    "org_name": "Asset Tracker",
    "default_loan_days": "7",
    "allow_badge_login": "1",
    "kiosk_idle_seconds": "60",
    "users_can_return_others": "1",
    "reader_api_key": "",  # generated on first run
}


def get_setting(key):
    row = db.session.get(Setting, key)
    if row is None:
        return DEFAULT_SETTINGS.get(key)
    return row.value


def set_setting(key, value):
    row = db.session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value)
        db.session.add(row)
    else:
        row.value = value


def get_int_setting(key, default=0):
    try:
        return int(get_setting(key))
    except (TypeError, ValueError):
        return default


def get_bool_setting(key):
    return str(get_setting(key)) in ("1", "true", "True", "on")
