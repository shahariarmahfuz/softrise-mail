"""In-process FastAPI integration tests using TestClient + a real Neon DB.

Run with:
    pytest -q tests/test_api.py

The tests reuse the live ``app.main:app`` (so they hit the real Neon database
configured in ``.env``).  Every test creates its own user with a random
suffix, so there is no shared state to clean up.
"""

from __future__ import annotations

import secrets

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="function")
def client() -> TestClient:
    # base_url=https so Secure cookies set by the server are echoed back
    # by httpx (TestClient defaults to http://testserver which would drop
    # Secure cookies in production-like configs).
    c = TestClient(app, base_url="https://testserver")
    yield c
    c.cookies.clear()


def _random_user(client: TestClient, prefix: str = "pytest") -> tuple[str, str, dict]:
    suffix = secrets.token_hex(3)
    username = f"{prefix}_{suffix}"
    pw = "test-password-1!"
    r = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": pw,
            "name": "Pytest User",
        },
    )
    r.raise_for_status()
    return username, pw, r.json()


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_register_creates_default_mailbox(client: TestClient) -> None:
    username, _, me = _random_user(client)
    assert me["username"] == username
    assert me["default_mailbox"]["email_address"].endswith("@softrise.app")
    assert me["default_mailbox"]["is_default"] is True


def test_login_then_me(client: TestClient) -> None:
    username, password, _ = _random_user(client)
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"identifier": username, "password": password})
    assert r.status_code == 200
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == username


def test_temp_mailbox_limit(client: TestClient) -> None:
    _, _, _ = _random_user(client, "limit")
    for i in range(10):
        r = client.post("/api/mailboxes/temp", json={"local_part": f"ten{i}_{secrets.token_hex(2)}"})
        assert r.status_code == 201, r.text
    r = client.post("/api/mailboxes/temp", json={"local_part": "eleventh"})
    assert r.status_code == 400
    assert "Maximum 10" in r.json()["error"]


def test_temp_delete_and_restore(client: TestClient) -> None:
    _, _, _ = _random_user(client, "rsr")
    r = client.post("/api/mailboxes/temp", json={"local_part": f"re_{secrets.token_hex(2)}"})
    mb = r.json()
    assert r.status_code == 201

    # Delete
    r = client.delete(f"/api/mailboxes/{mb['id']}")
    assert r.status_code == 200
    assert r.json()["deleted_at"] is not None

    # Restore (should succeed because nobody else took it)
    r = client.post(f"/api/mailboxes/{mb['id']}/restore")
    assert r.status_code == 200
    assert r.json()["deleted_at"] is None


def test_webhook_is_public_no_secret_required(client: TestClient) -> None:
    """The Cloudflare Worker posts with no ``X-Webhook-Secret`` header.

    The endpoint must accept the request and never return 401 for a missing
    secret. Unknown recipient → 202 ``{"ok": true, "stored": false}``.
    """
    r = client.post(
        "/webhook/email",
        json={
            "from": "x@x.com",
            "to": "nobody@softrise.app",
            "size": 0,
            "headers": {},
            "raw_email": "",
        },
    )
    assert r.status_code != 401, r.text
    assert r.status_code in (200, 202), r.text
    body = r.json()
    assert body["ok"] is True
    assert body["stored"] is False


def test_webhook_delivers_to_user(client: TestClient) -> None:
    _, _, me = _random_user(client, "wh")
    addr = me["default_mailbox"]["email_address"]
    raw = f"From: x@x.com\nTo: {addr}\nSubject: test {secrets.token_hex(2)}\n\nbody\n"
    # Cloudflare Worker only sends Content-Type — no auth header.
    r = client.post(
        "/webhook/email",
        json={"from": "x@x.com", "to": addr, "size": len(raw), "headers": {}, "raw_email": raw},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["stored"] is True
    assert body.get("message_id")

    inbox = client.get("/api/messages?folder=inbox")
    assert inbox.status_code == 200
    assert inbox.json()["total"] >= 1


def test_webhook_normalizes_recipient_case(client: TestClient) -> None:
    _, _, me = _random_user(client, "case")
    addr = me["default_mailbox"]["email_address"]
    upper = addr.upper()
    raw = f"From: x@x.com\nTo: {upper}\nSubject: case {secrets.token_hex(2)}\n\nbody\n"
    r = client.post(
        "/webhook/email",
        json={"from": "x@x.com", "to": upper, "size": len(raw), "headers": {}, "raw_email": raw},
    )
    assert r.status_code == 200, r.text
    assert r.json()["stored"] is True


def test_message_actions(client: TestClient) -> None:
    _, _, me = _random_user(client, "msg")
    addr = me["default_mailbox"]["email_address"]
    for i in range(2):
        raw = f"From: bot{i}@x.com\nTo: {addr}\nSubject: act {i}\n\nbody marker_{secrets.token_hex(2)}\n"
        client.post(
            "/webhook/email",
            json={"from": f"bot{i}@x.com", "to": addr, "size": len(raw), "headers": {}, "raw_email": raw},
        )
    items = client.get("/api/messages?folder=inbox").json()["items"]
    assert len(items) >= 2
    mid = items[0]["id"]

    r = client.post(f"/api/messages/{mid}/read", json={"is_read": True})
    assert r.status_code == 200
    r = client.post(f"/api/messages/{mid}/star", json={"is_starred": True})
    assert r.status_code == 200
    r = client.post(f"/api/messages/{mid}/archive")
    assert r.status_code == 200
    archive = client.get("/api/messages?folder=archive").json()
    assert any(it["id"] == mid for it in archive["items"])

    r = client.post(f"/api/messages/{mid}/trash")
    assert r.status_code == 200
    r = client.delete(f"/api/messages/{mid}?force=true")
    assert r.status_code == 200


def test_user_cannot_access_others(client: TestClient) -> None:
    _, _, me_a = _random_user(client, "ua")
    addr_a = me_a["default_mailbox"]["email_address"]
    raw = f"From: x@x.com\nTo: {addr_a}\nSubject: secret {secrets.token_hex(2)}\n\nx\n"
    client.post(
        "/webhook/email",
        json={"from": "x@x.com", "to": addr_a, "size": len(raw), "headers": {}, "raw_email": raw},
    )
    items = client.get("/api/messages?folder=inbox").json()["items"]
    assert items
    target_id = items[0]["id"]

    # Now switch user
    _, _, _ = _random_user(client, "ub")
    r = client.get(f"/api/messages/{target_id}")
    assert r.status_code == 404
    r = client.post(f"/api/messages/{target_id}/trash")
    assert r.status_code == 404


def test_admin_endpoints_blocked_for_users(client: TestClient) -> None:
    _random_user(client, "admnchk")
    r = client.get("/api/admin/stats")
    assert r.status_code == 403


def test_html_sanitization() -> None:
    from app.utils import sanitize_email_html

    danger = '<p>hi</p><script>alert("xss")</script><iframe src="evil"></iframe>'
    out = sanitize_email_html(danger)
    assert "<script>" not in out
    assert "alert" not in out
    assert "<iframe" not in out


def test_localpart_validation() -> None:
    from app.utils import is_valid_localpart, slugify_localpart

    assert is_valid_localpart("alice")
    assert is_valid_localpart("alice.smith_42-1")
    assert not is_valid_localpart("")
    assert not is_valid_localpart("ALICE")  # uppercase rejected
    assert not is_valid_localpart(".alice")  # leading punctuation rejected
    assert slugify_localpart("Alice Smith") == "alice.smith"
    assert slugify_localpart("FRANK!@") == "frank"
