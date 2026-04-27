"""End-to-end test that mirrors the bug-report scenario:

- Visit http://SERVER:5000/ unauthenticated -> bounced to /login (no demo data).
- Login as username 'mahfuz' from /login.
- Refresh /  five times -> stay logged in every time.
- Sidebar shows real user info (name + @softrise.app mailbox), not demo data.
- Settings opens on desktop and mobile viewports.
- Logout sends us back to /login. Login again works.

Run with the FastAPI server already up on :5000:
    python tests/e2e_session_persistence.py
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:5000")
USERNAME = os.getenv("E2E_USERNAME", "mahfuz")
PASSWORD = os.getenv("MAHFUZ_PWD", "mahfuz-pwd-1!")

failures: list[str] = []


def step(label, fn):
    print(f"-- {label}")
    try:
        fn()
        print("   OK")
    except AssertionError as exc:
        msg = str(exc) or "AssertionError"
        print(f"   FAIL: {msg}")
        failures.append(f"{label}: {msg}")
    except Exception as exc:
        msg = str(exc) or exc.__class__.__name__
        print(f"   FAIL: {msg}")
        failures.append(f"{label}: {msg}")


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        def visit_root_redirects_to_login():
            page.goto(BASE_URL + "/", wait_until="networkidle")
            page.wait_for_url("**/login", timeout=10000)
            assert page.locator("#login-form").is_visible()

        def assert_no_demo_visible_on_login():
            html = page.content()
            assert "Md Bayezid Hossain" not in html, "Demo name leaked into HTML"
            assert "bayezid@softrice.com" not in html, "Demo email leaked into HTML"
            assert "softrice.com" not in html, "softrice.com leaked"

        def login_as_mahfuz():
            page.goto(BASE_URL + "/login", wait_until="networkidle")
            page.fill('input[name="identifier"]', USERNAME)
            page.fill('input[name="password"]', PASSWORD)
            page.click("#auth-submit")
            page.wait_for_url(BASE_URL + "/", timeout=10000)
            page.wait_for_function(
                "() => document.getElementById('user-name').textContent.trim() !== ''",
                timeout=10000,
            )

        def assert_real_user_visible():
            name = page.locator("#user-name").inner_text().strip()
            email = page.locator("#user-email").inner_text().strip()
            assert name != "", "Sidebar user-name is empty after login"
            assert "Md Bayezid Hossain" not in name
            assert email == "mahfuz@softrise.app", f"Expected mahfuz@softrise.app, got: {email!r}"

        def refresh_and_stay_logged_in(n):
            for i in range(n):
                page.reload(wait_until="networkidle")
                # Must NOT be redirected to /login.
                assert "/login" not in page.url, f"refresh {i+1}: bounced to /login"
                page.wait_for_function(
                    "() => document.getElementById('user-email').textContent.trim() === 'mahfuz@softrise.app'",
                    timeout=10000,
                )
                name = page.locator("#user-name").inner_text().strip()
                assert name != "" and "Md Bayezid Hossain" not in name, f"refresh {i+1}: bad user-name {name!r}"

        def open_settings_desktop():
            page.click('[data-nav="settings"]')
            page.wait_for_selector("#settings-modal:not(.hidden)", timeout=5000)
            page.click('[data-modal-close="settings-modal"]')
            page.wait_for_function(
                "() => document.getElementById('settings-modal').classList.contains('hidden')",
                timeout=5000,
            )

        def open_settings_mobile():
            page.set_viewport_size({"width": 414, "height": 800})
            page.click("#menu-btn")
            page.wait_for_selector('[data-nav="settings"]', state="visible", timeout=3000)
            page.click('[data-nav="settings"]')
            page.wait_for_selector("#settings-modal:not(.hidden)", timeout=5000)
            page.click('[data-modal-close="settings-modal"]')
            page.wait_for_function(
                "() => document.getElementById('settings-modal').classList.contains('hidden')",
                timeout=5000,
            )
            page.set_viewport_size({"width": 1366, "height": 800})

        def sign_out_and_back_in():
            page.set_viewport_size({"width": 1366, "height": 800})
            page.click("#sign-out-btn")
            page.wait_for_url("**/login", timeout=5000)
            page.fill('input[name="identifier"]', USERNAME)
            page.fill('input[name="password"]', PASSWORD)
            page.click("#auth-submit")
            page.wait_for_url(BASE_URL + "/", timeout=10000)
            page.wait_for_function(
                "() => document.getElementById('user-email').textContent.trim() === 'mahfuz@softrise.app'",
                timeout=10000,
            )

        step("Visit / redirects to /login when unauthenticated", visit_root_redirects_to_login)
        step("No demo placeholder data is rendered on /login", assert_no_demo_visible_on_login)
        step("Login as mahfuz from /login", login_as_mahfuz)
        step("Sidebar shows real user info", assert_real_user_visible)
        step("5 page refreshes keep the session", lambda: refresh_and_stay_logged_in(5))
        step("Settings opens on desktop", open_settings_desktop)
        step("Settings opens on mobile (sidebar collapses)", open_settings_mobile)
        step("Logout then login again", sign_out_and_back_in)

        browser.close()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nALL SESSION-PERSISTENCE STEPS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
