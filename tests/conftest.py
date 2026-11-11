import re

import pytest

from app import create_app
from app.models import ROLE_ADMIN, Asset, Category, User, db


@pytest.fixture
def app(tmp_path):
    from app.tags import BRIDGE_STATUS

    BRIDGE_STATUS.update(last_seen=None, readers=[], version=None)
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
    })
    with app.app_context():
        admin = User(username="admin", full_name="Ada Admin", role=ROLE_ADMIN, badge_uid="ADMINBADGE")
        admin.set_password("adminpass")
        alice = User(username="alice", full_name="Alice User", badge_uid="AABBCCDD")
        alice.set_password("alicepass")
        bob = User(username="bob", full_name="Bob User", badge_uid="11223344")
        bob.set_password("bobpass12")
        cat = Category(name="Laptop", default_loan_days=3)
        db.session.add_all([admin, alice, bob, cat])
        db.session.add(Asset(asset_tag="A-1", name="Laptop 1", nfc_uid="04A1B2C3", category=cat))
        db.session.add(Asset(asset_tag="A-2", name="Camera 1", nfc_uid="04FFEE00"))
        db.session.commit()
    yield app


class Client:
    """Test client wrapper that handles CSRF tokens automatically."""

    def __init__(self, client):
        self.c = client

    def token(self):
        with self.c.session_transaction() as s:
            if "_csrf_token" not in s:
                s["_csrf_token"] = "tok"
            return s["_csrf_token"]

    def get(self, *a, **kw):
        return self.c.get(*a, **kw)

    def post(self, url, data=None, json=None, **kw):
        tok = self.token()
        if json is not None:
            return self.c.post(url, json=json, headers={"X-CSRF-Token": tok, **kw.pop("headers", {})}, **kw)
        data = dict(data or {})
        data["_csrf_token"] = tok
        return self.c.post(url, data=data, **kw)

    def login(self, username, password):
        return self.post("/login", {"username": username, "password": password})


@pytest.fixture
def client(app):
    return Client(app.test_client())


@pytest.fixture
def admin_client(client):
    client.login("admin", "adminpass")
    return client


@pytest.fixture
def alice_client(client):
    client.login("alice", "alicepass")
    return client
