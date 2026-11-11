import os
import secrets

import click
from flask import Flask, redirect, request, url_for
from flask_login import LoginManager

from .models import (
    DEFAULT_SETTINGS,
    ROLE_ADMIN,
    User,
    db,
    get_setting,
    set_setting,
)
from .utils import csrf_token, format_dt, format_duration, humanize, validate_csrf

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def _load_secret_key(app):
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(app.instance_path, "secret_key")
    if os.path.exists(path):
        with open(path) as fh:
            return fh.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as fh:
        fh.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.update(
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL",
            "sqlite:///" + os.path.join(app.instance_path, "assets.db"),
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _load_secret_key(app)

    db.init_app(app)
    login_manager.init_app(app)

    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .main import bp as main_bp
    from .reports import bp as reports_bp
    from .scan import bp as scan_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(scan_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(reports_bp, url_prefix="/reports")

    # The reader bridge authenticates with an API key instead of a session.
    csrf_exempt = {"scan.api_reader_push"}

    @app.before_request
    def _security_checks():
        if request.endpoint in csrf_exempt or app.config.get("TESTING_NO_CSRF"):
            return None
        validate_csrf()
        # First run: force creation of an administrator account.
        if request.endpoint not in ("auth.setup", "static") and not _has_users():
            return redirect(url_for("auth.setup"))
        return None

    @app.context_processor
    def _inject():
        return {
            "csrf_token": csrf_token,
            "org_name": get_setting("org_name") or "Asset Tracker",
        }

    app.jinja_env.filters["dt"] = format_dt
    app.jinja_env.filters["duration"] = format_duration
    app.jinja_env.filters["humanize"] = humanize

    with app.app_context():
        db.create_all()
        _ensure_settings()

    _register_cli(app)
    return app


def _has_users():
    return db.session.query(User.id).first() is not None


def _ensure_settings():
    changed = False
    for key, value in DEFAULT_SETTINGS.items():
        from .models import Setting

        if db.session.get(Setting, key) is None:
            if key == "reader_api_key":
                value = secrets.token_urlsafe(24)
            set_setting(key, value)
            changed = True
    if changed:
        db.session.commit()


def _register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--full-name", prompt=True)
    @click.password_option()
    def create_admin(username, full_name, password):
        """Create an administrator account."""
        if User.query.filter_by(username=username).first():
            raise click.ClickException("That username already exists.")
        user = User(username=username, full_name=full_name, role=ROLE_ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created admin {username}.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Load demo categories, locations, users and assets."""
        from .demo import seed

        seed()
        click.echo("Demo data loaded. Users alice/bob/carol/dave have password "
                   "'password'; if no admin existed, admin/adminadmin was created.")
