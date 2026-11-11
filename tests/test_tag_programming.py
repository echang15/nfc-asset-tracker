"""End-to-end tag programming: admin request -> bridge tap -> write -> link."""
from datetime import timedelta

import reader_bridge
from app.models import (
    JOB_DONE,
    JOB_FAILED,
    Asset,
    ReaderEvent,
    TagWriteJob,
    User,
    db,
    get_setting,
    utcnow,
)
from nfc_tags import read_text
from tests.test_nfc_tags import NTAG_ATR, FakeTag


class FakeForwarder(reader_bridge.Forwarder):
    """Bridge forwarder that talks to the Flask test client instead of HTTP."""

    def __init__(self, app):
        with app.app_context():
            self.key = get_setting("reader_api_key")
        self.client = app.test_client()
        self.station = "test"

    def _post(self, path, payload, attempts=3):
        r = self.client.post(path, json=payload, headers={"X-Api-Key": self.key})
        return r.get_json() if r.status_code < 400 else None


def start_job(admin_client, kind, target_id):
    r = admin_client.post("/admin/tags/program", json={"kind": kind, "id": target_id})
    assert r.status_code == 200, r.data
    return r.get_json()


def test_program_asset_tag(app, admin_client):
    job = start_job(admin_client, "asset", 2)
    assert job["status"] == "pending" and job["text"] == "asset:A-2"
    tag = FakeTag(uid=(0x04, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x01))
    reader_bridge.handle_tap(tag.transmit, NTAG_ATR, FakeForwarder(app))
    status = admin_client.get(f"/admin/tags/jobs/{job['id']}").get_json()
    assert status["status"] == "done", status
    assert "linked to Camera 1 (A-2)" in status["message"]
    assert read_text(tag.transmit, NTAG_ATR) == "asset:A-2"
    with app.app_context():
        assert Asset.query.filter_by(asset_tag="A-2").one().nfc_uid == "04AABBCCDDEE01"
        assert ReaderEvent.query.count() == 0  # the tap was not treated as a scan
    # The freshly programmed tag now works at the scan station.
    r = admin_client.post("/api/scan", json={"uid": "04AABBCCDDEE01"})
    assert r.get_json()["result"] == "checked_out"


def test_program_user_badge(app, admin_client):
    job = start_job(admin_client, "user", 3)  # bob
    tag = FakeTag(uid=(0x04, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06))
    reader_bridge.handle_tap(tag.transmit, NTAG_ATR, FakeForwarder(app))
    assert admin_client.get(f"/admin/tags/jobs/{job['id']}").get_json()["status"] == "done"
    assert read_text(tag.transmit, NTAG_ATR) == "user:bob"
    with app.app_context():
        assert User.query.filter_by(username="bob").one().badge_uid == "04010203040506"


def test_tag_already_used_elsewhere_is_not_written(app, admin_client):
    with app.app_context():
        db.session.get(Asset, 1).nfc_uid = "04777777777777"
        db.session.commit()
    job = start_job(admin_client, "user", 3)
    tag = FakeTag(uid=(0x04, 0x77, 0x77, 0x77, 0x77, 0x77, 0x77))
    reader_bridge.handle_tap(tag.transmit, NTAG_ATR, FakeForwarder(app))
    status = admin_client.get(f"/admin/tags/jobs/{job['id']}").get_json()
    assert status["status"] == "failed" and "already assigned to asset" in status["message"]
    assert tag.writes == 0
    with app.app_context():
        assert User.query.filter_by(username="bob").one().badge_uid == "11223344"


def test_write_failure_is_reported(app, admin_client):
    job = start_job(admin_client, "asset", 1)
    tag = FakeTag(cc=(0xE1, 0x10, 0x3E, 0x0F))  # read-only tag
    reader_bridge.handle_tap(tag.transmit, NTAG_ATR, FakeForwarder(app))
    status = admin_client.get(f"/admin/tags/jobs/{job['id']}").get_json()
    assert status["status"] == "failed" and "read-only" in status["message"]
    with app.app_context():
        assert db.session.get(Asset, 1).nfc_uid == "04A1B2C3"


def test_cancel_and_timeout(app, admin_client):
    job = start_job(admin_client, "asset", 1)
    admin_client.post(f"/admin/tags/jobs/{job['id']}/cancel", json={})
    # With no pending job, a tap is a normal scan event carrying the tag text.
    tag = FakeTag()
    from nfc_tags import write_text
    write_text(tag.transmit, "asset:A-1", NTAG_ATR)
    reader_bridge.handle_tap(tag.transmit, NTAG_ATR, FakeForwarder(app))
    with app.app_context():
        ev = ReaderEvent.query.one()
        assert ev.text == "asset:A-1"
    job = start_job(admin_client, "asset", 1)
    with app.app_context():
        j = db.session.get(TagWriteJob, job["id"])
        j.updated_at = utcnow() - timedelta(minutes=5)
        db.session.commit()
    assert admin_client.get(f"/admin/tags/jobs/{job['id']}").get_json()["status"] == JOB_FAILED


def test_starting_a_new_job_cancels_the_old_one(app, admin_client):
    first = start_job(admin_client, "asset", 1)
    start_job(admin_client, "asset", 2)
    assert admin_client.get(f"/admin/tags/jobs/{first['id']}").get_json()["status"] == "cancelled"


def test_programmed_but_unlinked_tag_message(alice_client, admin_client):
    r = admin_client.post("/api/scan", json={"uid": "0499999999", "text": "asset:A-1"})
    data = r.get_json()
    assert data["result"] == "unknown" and data["asset_id"] == 1
    assert "asset A-1" in data["message"]


def test_bridge_status_and_auth(app, admin_client):
    assert admin_client.get("/api/reader/status").get_json()["connected"] is False
    FakeForwarder(app).heartbeat(["Fake Contactless Reader 0"])
    st = admin_client.get("/api/reader/status").get_json()
    assert st["connected"] and st["readers"] == ["Fake Contactless Reader 0"]
    raw = app.test_client()
    assert raw.post("/api/reader/heartbeat", json={}).status_code == 401
    assert raw.post("/api/reader/jobs/1/result", json={}).status_code == 401


def test_non_admin_cannot_program(alice_client):
    r = alice_client.post("/admin/tags/program", json={"kind": "asset", "id": 1})
    assert r.status_code == 403


def test_batch_page(admin_client):
    assert admin_client.get("/admin/tags/batch").status_code == 200
    assert admin_client.get("/admin/tags/batch?which=all").status_code == 200
