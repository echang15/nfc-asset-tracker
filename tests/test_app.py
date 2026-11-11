from datetime import timedelta

from app.models import (
    STATUS_AVAILABLE,
    STATUS_CHECKED_OUT,
    STATUS_MAINTENANCE,
    Asset,
    AuditLog,
    ReaderEvent,
    Transaction,
    User,
    db,
    get_setting,
    utcnow,
)
from app.utils import normalize_uid


def asset(tag):
    return Asset.query.filter_by(asset_tag=tag).one()


def test_normalize_uid():
    assert normalize_uid(" 04:a1:b2:c3 ") == "04A1B2C3"
    assert normalize_uid("04 a1 b2-c3") == "04A1B2C3"
    assert normalize_uid(None) == ""


def test_first_run_redirects_to_setup(tmp_path):
    from app import create_app
    app = create_app({"SECRET_KEY": "x", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path/'e.db'}"})
    c = app.test_client()
    r = c.get("/login")
    assert r.status_code == 302 and "/setup" in r.location


def test_csrf_required(client):
    r = client.c.post("/login", data={"username": "admin", "password": "adminpass"})
    assert r.status_code == 400


def test_password_and_badge_login(client, app):
    r = client.login("alice", "wrong")
    assert b"Invalid username or password" in r.data
    r = client.post("/login", {"badge_uid": "aa:bb:cc:dd"})
    assert r.status_code == 302 and r.location.endswith("/scan")


def test_scan_toggles_checkout(alice_client, app):
    r = alice_client.post("/api/scan", json={"uid": "04:a1:b2:c3"})
    data = r.get_json()
    assert data["ok"] and data["result"] == "checked_out"
    with app.app_context():
        a = asset("A-1")
        assert a.status == STATUS_CHECKED_OUT and a.holder.username == "alice"
        # Category loan period (3 days) is applied.
        assert timedelta(days=2, hours=23) < a.due_at - utcnow() <= timedelta(days=3)
    r = alice_client.post("/api/scan", json={"uid": "04A1B2C3"})
    assert r.get_json()["result"] == "checked_in"
    with app.app_context():
        a = asset("A-1")
        assert a.status == STATUS_AVAILABLE and a.holder is None
        assert Transaction.query.count() == 2


def test_scan_modes(alice_client, app):
    r = alice_client.post("/api/scan", json={"uid": "04A1B2C3", "mode": "in"})
    assert r.get_json()["result"] == "error"
    alice_client.post("/api/scan", json={"uid": "04A1B2C3", "mode": "out"})
    r = alice_client.post("/api/scan", json={"uid": "04A1B2C3", "mode": "out"})
    assert r.get_json()["result"] == "error"
    with app.app_context():
        assert asset("A-1").status == STATUS_CHECKED_OUT


def test_return_on_behalf_respects_setting(client, app):
    client.login("alice", "alicepass")
    client.post("/api/scan", json={"uid": "04A1B2C3"})
    client.post("/logout")
    with app.app_context():
        from app.models import set_setting
        set_setting("users_can_return_others", "0")
        db.session.commit()
    client.login("bob", "bobpass12")
    r = client.post("/api/scan", json={"uid": "04A1B2C3"})
    assert r.get_json()["result"] == "error"
    with app.app_context():
        from app.models import set_setting
        set_setting("users_can_return_others", "1")
        db.session.commit()
    r = client.post("/api/scan", json={"uid": "04A1B2C3"})
    data = r.get_json()
    assert data["result"] == "checked_in" and "on behalf of Alice" in data["message"]


def test_unavailable_asset_cannot_be_checked_out(alice_client, app):
    with app.app_context():
        asset("A-2").status = STATUS_MAINTENANCE
        db.session.commit()
    r = alice_client.post("/api/scan", json={"uid": "04FFEE00"})
    assert r.get_json()["result"] == "error"


def test_unknown_tag(alice_client, admin_client):
    r = alice_client.post("/api/scan", json={"uid": "DEADBEEF"})
    assert r.get_json()["result"] == "unknown"
    assert r.get_json()["can_register"] is False


def test_badge_scan_switches_user(alice_client, app):
    r = alice_client.post("/api/scan", json={"uid": "11223344"})
    data = r.get_json()
    assert data["result"] == "badge" and data["reload"]
    r = alice_client.post("/api/scan", json={"uid": "04A1B2C3"})
    with app.app_context():
        assert asset("A-1").holder.username == "bob"


def test_non_admin_blocked_from_admin(alice_client):
    assert alice_client.get("/admin/users").status_code == 403
    assert alice_client.get("/reports/").status_code == 403


def test_admin_pages_render(admin_client, app):
    admin_client.post("/api/scan", json={"uid": "04A1B2C3"})
    urls = ["/dashboard", "/admin/users", "/admin/users/new", "/admin/users/2",
            "/admin/assets", "/admin/assets/new", "/admin/assets/1/edit",
            "/admin/assets/2/checkout", "/admin/assets/import", "/admin/categories",
            "/admin/locations", "/admin/settings", "/admin/audit", "/assets/1",
            "/my-items", "/scan", "/reports/", "/account/password"]
    for name in ["checked-out", "overdue", "history", "utilization", "user-activity",
                 "inventory", "idle", "warranty"]:
        urls += [f"/reports/{name}", f"/reports/{name}?format=csv"]
    for url in urls:
        r = admin_client.get(url)
        assert r.status_code == 200, url
    r = admin_client.get("/reports/checked-out?format=csv")
    assert r.mimetype == "text/csv" and b"Laptop 1" in r.data


def test_admin_create_user_and_asset(admin_client, app):
    r = admin_client.post("/admin/users/new", {"username": "carol", "full_name": "Carol",
                                               "role": "user", "badge_uid": "ca:fe:01",
                                               "is_active": "1"})
    assert r.status_code == 302
    r = admin_client.post("/admin/assets/new", {"asset_tag": "A-9", "name": "Drill",
                                                "nfc_uid": "cafe01", "status": "available"})
    assert b"registered as a user badge" in r.data
    r = admin_client.post("/admin/assets/new", {"asset_tag": "A-9", "name": "Drill",
                                                "nfc_uid": "0499", "status": "available",
                                                "purchase_cost": "99.50"})
    assert r.status_code == 302
    with app.app_context():
        assert User.query.filter_by(username="carol").one().badge_uid == "CAFE01"
        assert asset("A-9").nfc_uid == "0499"
        assert AuditLog.query.filter_by(action="create_asset").count() == 1


def test_admin_manual_checkout_checkin_and_delete_rules(admin_client, app):
    r = admin_client.post("/admin/assets/2/checkout", {"user_id": "2", "due_date": "2030-01-01"})
    assert r.status_code == 302
    with app.app_context():
        a = asset("A-2")
        assert a.holder.username == "alice" and a.due_at.year in (2029, 2030)
    # Status can't be changed while checked out.
    r = admin_client.post("/admin/assets/2/edit", {"asset_tag": "A-2", "name": "Camera 1", "status": "lost"})
    assert b"Check the asset in" in r.data
    admin_client.post("/admin/assets/2/checkin", {"condition": "fair"})
    with app.app_context():
        a = asset("A-2")
        assert a.status == STATUS_AVAILABLE and a.condition == "fair"
    # Asset with history can't be deleted; user with history can't be deleted.
    admin_client.post("/admin/assets/2/delete")
    admin_client.post("/admin/users/2/delete")
    with app.app_context():
        assert db.session.get(Asset, 2) is not None
        assert db.session.get(User, 2) is not None


def test_admin_cannot_demote_self(admin_client, app):
    admin_client.post("/admin/users/1", {"username": "admin", "full_name": "Ada", "role": "user", "is_active": "1"})
    with app.app_context():
        assert db.session.get(User, 1).is_admin


def test_csv_import(admin_client, app):
    import io
    csv_data = ("asset_tag,name,nfc_uid,category,location,purchase_cost\n"
                "A-1,Laptop 1 renamed,,Laptop,Storeroom,\n"
                "B-1,Tripod,04:99:88,Camera gear,Storeroom,\"$1,200.00\"\n"
                ",missing tag,,,,\n")
    r = admin_client.post("/admin/assets/import",
                          {"file": (io.BytesIO(csv_data.encode()), "assets.csv")},
                          content_type="multipart/form-data", follow_redirects=True)
    assert b"1 created, 1 updated" in r.data
    with app.app_context():
        assert asset("A-1").name == "Laptop 1 renamed"
        b = asset("B-1")
        assert b.nfc_uid == "049988" and b.category.name == "Camera gear"
        assert float(b.purchase_cost) == 1200.0


def test_reader_bridge_flow(app, alice_client):
    with app.app_context():
        key = get_setting("reader_api_key")
    raw = app.test_client()
    assert raw.post("/api/reader/push", json={"uid": "04A1B2C3"}).status_code == 401
    assert raw.post("/api/reader/push", json={"uid": "04:A1:B2:C3"},
                    headers={"X-Api-Key": key}).status_code == 200
    r = alice_client.get("/api/reader/poll?after=0")
    assert r.get_json()["uids"] == ["04A1B2C3"]
    cursor = r.get_json()["cursor"]
    assert alice_client.get(f"/api/reader/poll?after={cursor}").get_json()["uids"] == []


def test_badge_login_via_reader_bridge(app, client):
    with app.app_context():
        key = get_setting("reader_api_key")
    r = client.post("/api/reader/badge-login", json={"after": -1})
    cursor = r.get_json()["cursor"]
    app.test_client().post("/api/reader/push", json={"uid": "AABBCCDD"}, headers={"X-Api-Key": key})
    r = client.post("/api/reader/badge-login", json={"after": cursor})
    assert r.get_json()["ok"]
    assert client.get("/my-items").status_code == 200


def test_reports_math(admin_client, app):
    with app.app_context():
        a = asset("A-1")
        alice = User.query.filter_by(username="alice").one()
        now = utcnow()
        db.session.add_all([
            Transaction(asset=a, user=alice, action="check_out", timestamp=now - timedelta(days=5),
                        due_at=now - timedelta(days=4)),
            Transaction(asset=a, user=alice, action="check_in", timestamp=now - timedelta(days=3)),
        ])
        db.session.commit()
    r = admin_client.get("/reports/user-activity?format=csv")
    line = [l for l in r.data.decode().splitlines() if l.startswith("Alice")][0]
    # checkouts, returns, late returns
    assert line.split(",")[3:6] == ["1", "1", "1"]
    r = admin_client.get("/reports/utilization?format=csv")
    line = [l for l in r.data.decode().splitlines() if l.startswith("A-1")][0]
    assert line.split(",")[3] == "1" and line.split(",")[6] == "48.0"
