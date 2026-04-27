/* Soft Rice Mail — login/register page controller.
 *
 * Drives the dedicated /login and /register pages. Self-contained so it
 * doesn't depend on the main app.js bundle.
 */

(() => {
    "use strict";

    // ---------- Page gate ----------
    // ``auth.js`` only runs on the dedicated /login and /register pages;
    // bail early if it accidentally loads anywhere else (e.g. via stale
    // cached HTML pointing here).
    const _bodyPage = (document.body && document.body.dataset && document.body.dataset.page) || "";
    if (_bodyPage && _bodyPage !== "login" && _bodyPage !== "register") {
        console.debug("[softrise] auth.js: not a login/register page — skipping");
        return;
    }

    // Route stray exceptions to console so they never reach the user.
    window.addEventListener("error", (event) => {
        console.error("[softrise] auth.js uncaught error:", event.error || event.message);
    });
    window.addEventListener("unhandledrejection", (event) => {
        console.error("[softrise] auth.js unhandled rejection:", event.reason);
    });

    const $ = (sel, root = document) => (root || document).querySelector(sel);

    // ---------- Theme toggle (matches app.js behaviour) ----------
    const themeToggle = $("#theme-toggle");
    const themeIcon = $("#theme-icon");
    function refreshThemeIcon() {
        if (!themeIcon) return;
        themeIcon.className =
            "ph " +
            (document.documentElement.classList.contains("dark") ? "ph-sun" : "ph-moon") +
            " text-lg";
    }
    refreshThemeIcon();
    themeToggle?.addEventListener("click", () => {
        document.documentElement.classList.toggle("dark");
        try {
            localStorage.setItem(
                "color-theme",
                document.documentElement.classList.contains("dark") ? "dark" : "light",
            );
        } catch (_) {}
        refreshThemeIcon();
    });

    // ---------- Toast ----------
    function toast(message, kind = "info") {
        const stack = $("#toast-stack");
        if (!stack) return;
        const el = document.createElement("div");
        const palette = {
            info: "bg-neutral-900 text-white dark:bg-neutral-800",
            success: "bg-primary text-white",
            error: "bg-error text-white",
        }[kind] || "bg-neutral-900 text-white";
        el.className =
            "pointer-events-auto rounded-full px-4 py-2 text-xs shadow-xl border border-black/5 dark:border-white/5 " +
            palette +
            " opacity-0 translate-y-2 transition-all duration-200";
        el.textContent = message;
        stack.appendChild(el);
        requestAnimationFrame(() => el.classList.remove("opacity-0", "translate-y-2"));
        setTimeout(() => {
            el.classList.add("opacity-0", "translate-y-2");
            setTimeout(() => el.remove(), 250);
        }, 3000);
    }

    // ---------- Tiny API ----------
    async function apiRequest(method, url, body) {
        const opts = {
            method,
            credentials: "include",
            cache: "no-store",
            headers: { "Accept": "application/json" },
        };
        if (body !== undefined && body !== null) {
            opts.headers["Content-Type"] = "application/json";
            opts.body = JSON.stringify(body);
        }
        const res = await fetch(url, opts);
        let data = null;
        try {
            data = await res.json();
        } catch (_) {}
        if (!res.ok) {
            const err = new Error(formatError(data, res.status));
            err.status = res.status;
            err.payload = data;
            throw err;
        }
        return data;
    }

    function formatError(payload, status) {
        if (!payload) return `HTTP ${status}`;
        if (typeof payload.error === "string" && payload.error) {
            // Validation errors: surface the first field-level message if we got one.
            if (Array.isArray(payload.errors) && payload.errors.length) {
                const first = payload.errors[0];
                if (first && first.message) {
                    const field = first.field ? `${first.field}: ` : "";
                    return `${field}${first.message}`;
                }
            }
            return payload.error;
        }
        if (typeof payload.detail === "string") return payload.detail;
        return `HTTP ${status}`;
    }

    function clearLegacyDemoStorage() {
        try {
            ["softrise_user", "demo_user", "user", "current_user", "softrise_demo"].forEach((k) =>
                localStorage.removeItem(k),
            );
        } catch (_) {}
    }
    clearLegacyDemoStorage();

    // ---------- Already-logged-in shortcut ----------
    // If the user lands on /login or /register but already has a valid
    // session cookie, send them straight to the app.
    (async function bounceIfAuthed() {
        try {
            const r = await fetch("/api/auth/me", {
                method: "GET",
                credentials: "include",
                cache: "no-store",
                headers: { "Accept": "application/json" },
            });
            if (r.ok) {
                window.location.replace("/");
            }
        } catch (_) {
            // network problem — fall through and show the form
        }
    })();

    // ---------- Form helpers ----------
    function setError(el, message) {
        if (!el) return;
        if (message) {
            el.textContent = message;
            el.classList.remove("hidden");
        } else {
            el.textContent = "";
            el.classList.add("hidden");
        }
    }

    function pickPayload(form, fields) {
        const fd = new FormData(form);
        const payload = {};
        for (const f of fields) {
            const v = fd.get(f);
            if (typeof v === "string" && v.trim() !== "") {
                payload[f] = v.trim();
            }
        }
        return payload;
    }

    // ---------- Login ----------
    const loginForm = $("#login-form");
    if (loginForm) {
        const submitBtn = $("#auth-submit", loginForm);
        const errorEl = $("#auth-error");
        loginForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            setError(errorEl, "");
            const payload = pickPayload(loginForm, ["identifier", "password"]);
            if (!payload.identifier || !payload.password) {
                setError(errorEl, "Please enter your username and password.");
                return;
            }
            submitBtn.disabled = true;
            const original = submitBtn.textContent;
            submitBtn.textContent = "Signing in…";
            try {
                await apiRequest("POST", "/api/auth/login", payload);
                toast("Welcome back!", "success");
                // Use href= so back button doesn't return us to /login.
                window.location.replace("/");
            } catch (err) {
                if (err.status === 401) {
                    setError(errorEl, "Invalid username or password.");
                } else {
                    setError(errorEl, err.message || "Sign-in failed. Please try again.");
                }
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = original;
            }
        });
    }

    // ---------- Register ----------
    const registerForm = $("#register-form");
    if (registerForm) {
        const submitBtn = $("#auth-submit", registerForm);
        const errorEl = $("#auth-error");
        registerForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            setError(errorEl, "");
            const payload = pickPayload(registerForm, [
                "name",
                "username",
                "email",
                "password",
            ]);
            if (!payload.username || !payload.password) {
                setError(errorEl, "Username and password are required.");
                return;
            }
            // Backend treats email as optional — only send it if provided.
            submitBtn.disabled = true;
            const original = submitBtn.textContent;
            submitBtn.textContent = "Creating account…";
            try {
                await apiRequest("POST", "/api/auth/register", payload);
                // /api/auth/register sets the session cookie + returns MeOut, so
                // we are auto-logged-in.  Hop straight to the inbox.
                toast("Welcome aboard!", "success");
                window.location.replace("/");
            } catch (err) {
                let msg = err.message || "Could not create account.";
                if (err.status === 409) {
                    if (/email/i.test(msg)) msg = "That email is already registered.";
                    else if (/user/i.test(msg)) msg = "That username is already taken.";
                }
                setError(errorEl, msg);
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = original;
            }
        });
    }
})();
