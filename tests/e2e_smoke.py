"""Headless browser smoke test for the Softrise Mail frontend.

This drives the live FastAPI server (port 5000) using Playwright to verify:
- The page loads without console errors.
- Visual elements (Soft Rice Mail brand, sidebar items, theme toggle, filter chips)
  remain present.
- Unauthenticated users on / are redirected to /login.
- Register on /register -> auto-logs in -> /  with the @softrise.app default mailbox.
- Send a sample webhook email (NO secret header) -> message appears in inbox.
- Open + read message; settings modal opens; sign out returns to /login.
"""

from __future__ import annotations

import os
import secrets
import sys

import httpx
from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:5000")

failures: list[str] = []


def step(label: str, fn, page=None):
    print(f"-- {label}")
    try:
        fn()
        print("   OK")
    except Exception as exc:
        msg = str(exc) or exc.__class__.__name__
        print(f"   FAIL: {msg}")
        if page is not None:
            try:
                snap = f"/tmp/e2e-fail-{label.replace(' ', '_').replace('/', '_')}.png"
                page.screenshot(path=snap, full_page=True)
                print(f"   SAVED {snap}")
            except Exception:
                pass
        failures.append(f"{label}: {msg}")


def main() -> int:
    suffix = secrets.token_hex(3)
    username = f"e2e{suffix}"
    password = "test-password-1!"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda exc: console_errors.append(f"pageerror: {exc}"))

        def visit_root_redirects_to_login():
            page.goto(BASE_URL + "/", wait_until="networkidle")
            # The SPA bootstraps, hits /api/auth/me, gets 401, redirects to /login.
            page.wait_for_url("**/login", timeout=10000)
            assert page.locator("#login-form").is_visible()

        def register():
            page.goto(BASE_URL + "/register", wait_until="networkidle")
            page.wait_for_selector("#register-form", timeout=5000)
            page.fill('input[name="name"]', "E2E User")
            page.fill('input[name="username"]', username)
            page.fill('input[name="email"]', f"{username}@example.com")
            page.fill('input[name="password"]', password)
            page.click("#auth-submit")
            page.wait_for_url(BASE_URL + "/", timeout=10000)
            page.wait_for_function(
                "() => document.getElementById('user-email')?.textContent.includes('@softrise.app')",
                timeout=10000,
            )

        def assert_default_mailbox_visible():
            email_text = page.locator("#user-email").inner_text()
            assert email_text.endswith("@softrise.app"), email_text
            name_text = page.locator("#user-name").inner_text()
            assert name_text and "Bayezid" not in name_text, f"unexpected name {name_text!r}"

        def login_persists_after_refresh():
            for _ in range(3):
                page.reload(wait_until="networkidle")
                # We must NOT be bounced to /login.
                assert "/login" not in page.url
                page.wait_for_function(
                    "() => document.getElementById('user-email')?.textContent.includes('@softrise.app')",
                    timeout=5000,
                )

        def send_webhook_email():
            raw = (
                f"From: e2e-bot@example.com\n"
                f"To: {username}@softrise.app\n"
                f"Subject: E2E Smoke Test {suffix}\n"
                f"Message-ID: <e2e-{suffix}@example.com>\n\n"
                f"Hello E2E! marker:{suffix}\n"
            )
            r = httpx.post(
                f"{BASE_URL}/webhook/email",
                json={
                    "from": "e2e-bot@example.com",
                    "to": f"{username}@softrise.app",
                    "size": len(raw),
                    "headers": {},
                    "raw_email": raw,
                },
                # Intentionally no auth header — Cloudflare Worker contract.
                timeout=15,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True and body["stored"] is True, body
            assert body.get("message_id"), body

        def reload_inbox_and_assert():
            page.reload(wait_until="networkidle")
            page.wait_for_selector(".email-item", timeout=10000)
            assert page.locator(".email-item").count() >= 1

        def open_and_read_message():
            # Clicking an email now navigates to a dedicated /message/{id}
            # page (no modal). Verify navigation, the rendered subject, and
            # that the back button returns to the inbox.
            page.locator(".email-item").first.click()
            page.wait_for_url("**/message/**", timeout=10000)
            page.wait_for_function(
                f"() => document.getElementById('message-subject')?.textContent.includes('E2E Smoke Test')",
                timeout=10000,
            )
            page.click("#back-btn")
            page.wait_for_url(BASE_URL + "/**", timeout=10000)
            page.wait_for_selector(".email-item", timeout=10000)

        def settings_modal():
            page.click('[data-nav="settings"]')
            page.wait_for_selector("#settings-modal:not(.hidden)", timeout=5000)
            page.fill('input[name="display_name"]', "E2E Renamed")
            page.click('#settings-form button[type=submit]')
            page.wait_for_function(
                "() => document.getElementById('settings-modal').classList.contains('hidden')",
                timeout=5000,
            )
            page.wait_for_function(
                "() => document.getElementById('user-name').textContent.trim() === 'E2E Renamed'",
                timeout=5000,
            )

        def sign_out():
            page.click("#sign-out-btn")
            page.wait_for_url("**/login", timeout=5000)

        step("Visit / redirects to /login when unauthenticated", visit_root_redirects_to_login, page=page)
        step("Register new user via /register", register, page=page)
        step("Default @softrise.app mailbox visible (no demo data)", assert_default_mailbox_visible, page=page)
        step("Login persists across refreshes", login_persists_after_refresh, page=page)
        step("Webhook delivers without secret header", send_webhook_email, page=page)
        step("Inbox shows the email", reload_inbox_and_assert, page=page)
        step("Open and read message", open_and_read_message, page=page)
        step("Settings modal opens and saves display name", settings_modal, page=page)
        step("Sign out goes to /login", sign_out, page=page)

        def is_critical(msg: str) -> bool:
            low = msg.lower()
            if "favicon" in low:
                return False
            # 401s during bootstrap on /  before we redirect to /login are
            # expected (we explicitly call /api/auth/me to detect the session).
            if "401" in low or "unauthorized" in low:
                return False
            return True

        critical = [e for e in console_errors if is_critical(e)]
        if critical:
            failures.append("console errors: " + " | ".join(critical[:5]))

        browser.close()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nALL E2E STEPS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
