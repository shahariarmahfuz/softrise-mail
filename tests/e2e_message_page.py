"""Verify the dedicated /message/{id} page replaces the old modal and works
on both desktop and mobile viewports.

Run with the FastAPI server already up on :5000.
"""

from __future__ import annotations

import os
import secrets
import sys

import httpx
from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:5000")

failures: list[str] = []


def step(label, fn, page=None):
    print(f"-- {label}")
    try:
        fn()
        print("   OK")
    except Exception as exc:
        msg = str(exc) or exc.__class__.__name__
        print(f"   FAIL: {msg}")
        if page is not None:
            try:
                snap = f"/tmp/e2e-msg-fail-{label.replace(' ', '_').replace('/', '_')}.png"
                page.screenshot(path=snap, full_page=True)
                print(f"   SAVED {snap}")
            except Exception:
                pass
        failures.append(f"{label}: {msg}")


def main() -> int:
    suffix = secrets.token_hex(3)
    username = f"msgpg{suffix}"
    password = "test-password-1!"

    # 1. Create a user via the API.
    api = httpx.Client(base_url=BASE_URL, follow_redirects=True, timeout=15)
    r = api.post(
        "/api/auth/register",
        json={
            "name": "Message Page Tester",
            "username": username,
            "email": f"{username}@example.com",
            "password": password,
        },
    )
    r.raise_for_status()

    # 2. Send 3 unread emails through the public webhook.
    addr = f"{username}@softrise.app"
    msg_subjects = []
    for i in range(3):
        subj = f"MsgPage Test {suffix}-{i}"
        msg_subjects.append(subj)
        raw = (
            f"From: bot{i}@example.com\n"
            f"To: {addr}\n"
            f"Subject: {subj}\n"
            f"\n"
            f"Body: this is body line for {i} -- " + ("X" * 200) + "\n"
        )
        r = httpx.post(
            f"{BASE_URL}/webhook/email",
            json={
                "from": f"bot{i}@example.com",
                "to": addr,
                "size": len(raw),
                "headers": {},
                "raw_email": raw,
            },
            timeout=15,
        )
        assert r.status_code == 200 and r.json()["stored"] is True, r.text

    # We'll target the first one in the inbox.
    items = api.get("/api/messages?folder=inbox").json()["items"]
    assert items, "inbox unexpectedly empty"
    target = items[0]  # newest first
    target_id = target["id"]
    target_subject = target["subject"]
    assert not target["is_read"]

    initial_unread = api.get("/api/messages?folder=inbox&read=false&limit=1").json()["total"]
    assert initial_unread >= 3

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # ---------- DESKTOP ----------
        desktop = browser.new_context(viewport={"width": 1366, "height": 800})
        page = desktop.new_page()
        # Sign in
        page.goto(BASE_URL + "/login", wait_until="networkidle")
        page.fill('input[name="identifier"]', username)
        page.fill('input[name="password"]', password)
        page.click("#auth-submit")
        page.wait_for_url(BASE_URL + "/", timeout=10000)
        page.wait_for_selector(".email-item", timeout=10000)

        def click_email_navigates():
            # Click the row matching our subject so the test stays deterministic.
            row = page.locator(f".email-item:has-text(\"{target_subject}\")").first
            assert row.is_visible(), "target email not visible in inbox"
            row.click()
            page.wait_for_url(f"**/message/{target_id}*", timeout=10000)
            assert page.locator("#message-content").is_visible()
            assert page.locator("#message-modal").count() == 0, "old modal still present"

        def page_shows_required_fields():
            page.wait_for_function(
                f"() => document.getElementById('message-subject')?.textContent.includes('{target_subject}')",
                timeout=10000,
            )
            assert target_subject in page.locator("#message-subject").inner_text()
            assert page.locator("#message-from").inner_text() != ""
            assert page.locator("#message-to").inner_text() == addr
            assert page.locator("#message-date").inner_text() != ""
            # Body must be rendered (text or html).
            assert page.locator("#message-body").inner_text().strip() != ""
            # Toolbar buttons exist.
            for sel in ["#back-btn", "#message-star", "#message-archive", "#message-trash", "#message-toggle-read"]:
                assert page.locator(sel).is_visible(), f"{sel} should be visible"

        def auto_marked_as_read():
            # Server-side state should now be is_read=true even though we
            # opened only via the dedicated page.
            r = api.get(f"/api/messages/{target_id}")
            assert r.status_code == 200
            assert r.json()["is_read"] is True, r.json()

        def back_button_returns_to_inbox():
            page.click("#back-btn")
            page.wait_for_url(f"{BASE_URL}/**", timeout=10000)
            # We expect to be on /  or /?folder=inbox.
            assert page.url.rstrip("/") in (BASE_URL.rstrip("/"), BASE_URL.rstrip("/") + "?folder=inbox"), page.url
            page.wait_for_selector(".email-item", timeout=10000)

        def opened_email_is_read_in_inbox():
            row = page.locator(f".email-item:has-text(\"{target_subject}\")").first
            assert row.is_visible()
            status = row.get_attribute("data-status")
            assert status == "read", f"row data-status should be 'read', got {status!r}"

        def unread_badge_decreased():
            page.wait_for_function(
                f"() => parseInt(document.querySelector('[data-nav-count=\"inbox\"]').textContent || '0', 10) === {initial_unread - 1}",
                timeout=10000,
            )

        step("Click email in inbox -> navigates to /message/{id}", click_email_navigates, page=page)
        step("Dedicated page shows subject/from/to/date/body/toolbar", page_shows_required_fields, page=page)
        step("Server marks message as read on open", auto_marked_as_read, page=page)
        step("Back button returns to inbox", back_button_returns_to_inbox, page=page)
        step("Inbox row now shows as read", opened_email_is_read_in_inbox, page=page)
        step("Unread count decreased by 1", unread_badge_decreased, page=page)

        # ---------- MOBILE 390px ----------
        mobile = browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
        )
        mpage = mobile.new_page()
        mpage.goto(BASE_URL + "/login", wait_until="networkidle")
        mpage.fill('input[name="identifier"]', username)
        mpage.fill('input[name="password"]', password)
        mpage.click("#auth-submit")
        mpage.wait_for_url(BASE_URL + "/", timeout=10000)
        mpage.wait_for_selector(".email-item", timeout=10000)

        def mobile_no_horizontal_overflow_inbox():
            # No part of the page should require horizontal scrolling.
            scroll_w, client_w = mpage.evaluate(
                "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
            )
            assert scroll_w <= client_w + 1, f"inbox overflows: scroll={scroll_w} client={client_w}"

        def mobile_sidebar_open_and_close():
            # Sidebar starts collapsed at 390px.
            mpage.click("#menu-btn")
            mpage.wait_for_selector('[data-nav="settings"]', state="visible", timeout=3000)
            # close again
            mpage.click("#close-menu-btn")
            mpage.wait_for_function(
                "() => document.getElementById('sidebar-menu').classList.contains('-translate-x-full')",
                timeout=3000,
            )

        def mobile_click_email_navigates():
            # Pick a different unread row so this also exercises mark-as-read.
            other_subject = msg_subjects[1]
            row = mpage.locator(f".email-item:has-text(\"{other_subject}\")").first
            assert row.is_visible()
            row.click()
            mpage.wait_for_url("**/message/**", timeout=10000)
            assert mpage.locator("#message-content").is_visible()
            mpage.wait_for_function(
                f"() => document.getElementById('message-subject')?.textContent.includes('{other_subject}')",
                timeout=10000,
            )

        def mobile_message_page_no_overflow():
            scroll_w, client_w = mpage.evaluate(
                "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
            )
            assert scroll_w <= client_w + 1, f"message page overflows: scroll={scroll_w} client={client_w}"
            # Body should not extend wider than the viewport either.
            body_w, viewport_w = mpage.evaluate(
                "() => [document.getElementById('message-body').scrollWidth, window.innerWidth]"
            )
            assert body_w <= viewport_w + 1, f"message body overflows: body={body_w} vp={viewport_w}"

        def mobile_back_btn_is_tappable():
            box = mpage.locator("#back-btn").bounding_box()
            assert box and box["width"] >= 36 and box["height"] >= 36, f"back button too small: {box}"

        step("Mobile inbox has no horizontal overflow", mobile_no_horizontal_overflow_inbox, page=mpage)
        step("Mobile sidebar opens and closes", mobile_sidebar_open_and_close, page=mpage)
        step("Mobile click email navigates to dedicated page", mobile_click_email_navigates, page=mpage)
        step("Mobile message page has no horizontal overflow", mobile_message_page_no_overflow, page=mpage)
        step("Mobile back button is touch-friendly (>= 36x36)", mobile_back_btn_is_tappable, page=mpage)

        browser.close()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nALL MESSAGE-PAGE STEPS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
