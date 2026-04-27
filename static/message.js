/* Soft Rice Mail — dedicated message-detail page controller.
 *
 * Replaces the in-page modal: the message lives at its own URL
 * (`/message/{id}`), can be deep-linked / shared / refreshed, and is friendly
 * on mobile.
 *
 * Behaviour matches the requirements:
 *   1. GET /api/messages/{id}  to load.
 *   2. If is_read is false -> POST /api/messages/{id}/read {is_read:true}.
 *   3. 401 -> redirect to /login.
 *   4. 404 / not-owned -> show clean error panel.
 */

(() => {
    "use strict";

    // ---------- Page gate ----------
    // ``message.js`` is only meant to run on /message/{id}. If it ends up on
    // a different page, bail early instead of trying to read missing DOM
    // nodes and throwing into the toast layer.
    const _bodyPage = (document.body && document.body.dataset && document.body.dataset.page) || "";
    if (_bodyPage && _bodyPage !== "message") {
        console.debug("[softrise] message.js: not a message page — skipping");
        return;
    }

    // Route stray exceptions to console so they never reach the user.
    window.addEventListener("error", (event) => {
        console.error("[softrise] message.js uncaught error:", event.error || event.message);
    });
    window.addEventListener("unhandledrejection", (event) => {
        console.error("[softrise] message.js unhandled rejection:", event.reason);
    });

    const $ = (sel, root = document) => (root || document).querySelector(sel);

    // ---------- Tiny helpers (kept self-contained so this page does NOT need app.js) ----------
    function setHtml(el, html, name) {
        if (!el) {
            console.warn("[softrise] message.js missing element:", name || "(unnamed)");
            return false;
        }
        el.innerHTML = html;
        return true;
    }

    function setText(el, value, name) {
        if (!el) {
            console.warn("[softrise] message.js missing element:", name || "(unnamed)");
            return false;
        }
        el.textContent = value;
        return true;
    }

    function escapeHtml(text) {
        if (text === null || text === undefined) return "";
        return String(text).replace(/[&<>"']/g, (c) => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
        })[c]);
    }

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
            " opacity-0 translate-y-2 transition-all duration-200 max-w-full break-words text-center";
        el.textContent = message;
        stack.appendChild(el);
        requestAnimationFrame(() => el.classList.remove("opacity-0", "translate-y-2"));
        setTimeout(() => {
            el.classList.add("opacity-0", "translate-y-2");
            setTimeout(() => el.remove(), 250);
        }, 3000);
    }

    function userError(err, fallback) {
        if (err && err.status === 401) {
            redirectToLogin();
            return null;
        }
        if (err && err.status) {
            return err.message || fallback;
        }
        console.error("[softrise] message action failed:", err);
        return fallback;
    }

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
        let payload = null;
        try { payload = await res.json(); } catch (_) {}
        if (!res.ok) {
            const err = new Error((payload && (payload.error || payload.detail)) || `HTTP ${res.status}`);
            err.status = res.status;
            err.payload = payload;
            throw err;
        }
        return payload;
    }

    function redirectToLogin() {
        if (window.__softrise_redirecting) return;
        window.__softrise_redirecting = true;
        // Preserve where we wanted to go for after-login UX (best-effort).
        window.location.replace("/login");
    }

    // ---------- Theme toggle (mirrors app.js behaviour) ----------
    const themeToggle = $("#theme-toggle");
    const themeIcon = $("#theme-icon");
    function refreshThemeIcon() {
        if (!themeIcon) return;
        themeIcon.className =
            "ph " +
            (document.documentElement.classList.contains("dark") ? "ph-sun" : "ph-moon") +
            " text-xl";
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

    // ---------- URL parsing ----------
    function getMessageId() {
        const m = window.location.pathname.match(/^\/message\/([^\/?#]+)/);
        return m ? decodeURIComponent(m[1]) : null;
    }

    function getReturnFolder() {
        // Optional ?from=<folder> query lets the inbox preserve folder context
        // when navigating to a message; defaults to inbox.
        const params = new URLSearchParams(window.location.search);
        const folder = (params.get("from") || "").toLowerCase();
        const allowed = ["inbox", "starred", "sent", "drafts", "archive", "trash", "mailboxes"];
        return allowed.includes(folder) ? folder : "inbox";
    }

    // ---------- Page elements ----------
    const els = {
        loading: $("#message-loading"),
        error: $("#message-error"),
        errorTitle: $("#message-error-title"),
        errorDetail: $("#message-error-detail"),
        content: $("#message-content"),
        subject: $("#messageSubject"),
        from: $("#messageFrom"),
        to: $("#messageTo"),
        date: $("#messageDate"),
        attachments: $("#messageAttachmentsRow"),
        attachmentsList: $("#messageAttachments"),
        body: $("#messageBody"),
        backBtn: $("#messageBackBtn"),
        starBtn: $("#messageStarBtn"),
        archiveBtn: $("#messageArchiveBtn"),
        trashBtn: $("#messageTrashBtn"),
        toggleReadBtn: $("#message-toggle-read"),
    };

    let current = null; // hydrated message detail
    let scaleRaf = 0;

    const LAYOUT_HEIGHT_TAGS = new Set(["TABLE", "TBODY", "THEAD", "TFOOT", "TR", "TD", "TH", "DIV", "SECTION"]);
    const ZERO_LENGTH_RE = /^(?:0+)?(?:px|em|rem|%)?$/i;

    function shouldStripMargin(value) {
        if (!value) return false;
        const normalized = String(value).trim().toLowerCase();
        return normalized !== "auto" && !ZERO_LENGTH_RE.test(normalized);
    }

    function normalizeEmailHtml(html) {
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, "text/html");
        const root = doc.createElement("div");
        root.className = "email-body-responsive";

        const bodyNodes = Array.from(doc.body ? doc.body.childNodes : []);
        const hasBodyElements = bodyNodes.some((node) => node.nodeType === Node.ELEMENT_NODE);

        if (hasBodyElements) {
            let seenFirstElement = false;
            bodyNodes.forEach((node) => {
                if (node.nodeType === Node.ELEMENT_NODE) {
                    seenFirstElement = true;
                    root.appendChild(node.cloneNode(true));
                    return;
                }
                if (!seenFirstElement) {
                    return;
                }
                root.appendChild(node.cloneNode(true));
            });
        } else {
            root.innerHTML = html;
        }

        const nodes = [root, ...root.querySelectorAll("*")];
        nodes.forEach((node) => {
            if (!(node instanceof HTMLElement)) return;

            node.removeAttribute("width");
            if (LAYOUT_HEIGHT_TAGS.has(node.tagName)) {
                node.removeAttribute("height");
            }

            if (node.style) {
                node.style.removeProperty("width");
                node.style.removeProperty("min-width");
                node.style.removeProperty("max-width");
                if (shouldStripMargin(node.style.marginLeft)) {
                    node.style.removeProperty("margin-left");
                }
                if (shouldStripMargin(node.style.marginRight)) {
                    node.style.removeProperty("margin-right");
                }
            }

            if (node.tagName === "IMG") {
                node.style.maxWidth = "100%";
                node.style.height = "auto";
            }

            if (node.tagName === "TABLE") {
                node.style.width = "100%";
                node.style.maxWidth = "100%";
                node.style.tableLayout = "fixed";
            }

            if (node.tagName === "TD" || node.tagName === "TH") {
                node.style.wordBreak = "break-word";
                node.style.overflowWrap = "anywhere";
            }
        });

        return root.outerHTML;
    }

    function fitEmailToMobile() {
        const wrapper = document.querySelector(".email-scale-wrapper");
        const inner = document.querySelector(".email-scale-inner");
        if (!wrapper || !inner) return;

        inner.style.transform = "";
        inner.style.width = "";
        wrapper.style.height = "";
        wrapper.style.overflow = "hidden";

        const viewportWidth = Math.min(window.innerWidth, document.documentElement.clientWidth);
        const availableWidth = wrapper.clientWidth || viewportWidth;
        const contentWidth = inner.scrollWidth;

        if (contentWidth > availableWidth) {
            const scale = availableWidth / contentWidth;
            inner.style.width = `${contentWidth}px`;
            inner.style.transform = `scale(${scale})`;
            inner.style.transformOrigin = "top left";
            wrapper.style.height = `${Math.ceil(inner.scrollHeight * scale)}px`;
        }
    }

    function bindEmailScaleEvents() {
        if (!els.body) return;
        els.body.querySelectorAll("img").forEach((img) => {
            img.addEventListener("load", fitEmailToMobile);
        });
    }

    function scheduleEmailScale() {
        if (scaleRaf) cancelAnimationFrame(scaleRaf);
        scaleRaf = requestAnimationFrame(() => {
            scaleRaf = 0;
            if (!current || !current.body_html || !els.body) return;
            fitEmailToMobile();
        });
    }

    // ---------- Render helpers ----------
    function showError(title, detail) {
        els.loading?.classList.add("hidden");
        els.content?.classList.add("hidden");
        if (els.errorTitle) els.errorTitle.textContent = title;
        if (els.errorDetail) els.errorDetail.textContent = detail || "";
        els.error?.classList.remove("hidden");
        // Disable toolbar action buttons since there is no message.
        [els.starBtn, els.archiveBtn, els.trashBtn, els.toggleReadBtn].forEach((b) => {
            if (b) b.setAttribute("disabled", "true");
        });
    }

    function renderAttachments(list) {
        if (!els.attachments || !els.attachmentsList) {
            console.warn("[softrise] attachments container missing — skipping");
            return;
        }
        if (!list || !list.length) {
            els.attachments.classList.add("hidden");
            els.attachmentsList.textContent = "";
            return;
        }
        els.attachments.classList.remove("hidden");
        const html = list.map((a) => {
            const sizeKb = Math.max(1, Math.round((a.size || 0) / 1024));
            return `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-stone-100 dark:bg-neutral-800 text-[11px] text-neutral-700 dark:text-neutral-300 break-all"><i class="ph ph-paperclip"></i>${escapeHtml(a.filename)} (${sizeKb} KB)</span>`;
        }).join("");
        setHtml(els.attachmentsList, html, "#messageAttachments");
    }

    function refreshToolbar() {
        if (!current) return;
        // Star icon
        const starI = els.starBtn?.querySelector("i");
        if (starI) starI.className = `ph ${current.is_starred ? "ph-fill " : ""}ph-star text-xl`;
        if (els.starBtn) {
            els.starBtn.classList.toggle("text-tertiary", !!current.is_starred);
            els.starBtn.classList.toggle("dark:text-yellow-400", !!current.is_starred);
            els.starBtn.classList.toggle("text-neutral-600", !current.is_starred);
            els.starBtn.classList.toggle("dark:text-neutral-300", !current.is_starred);
        }
        // Read toggle icon
        const readI = els.toggleReadBtn?.querySelector("i");
        if (readI) readI.className = `ph ${current.is_read ? "ph-envelope-simple" : "ph-envelope-open"} text-xl`;
        if (els.toggleReadBtn) {
            els.toggleReadBtn.title = current.is_read ? "Mark as unread" : "Mark as read";
        }
    }

    function renderMessage(m) {
        current = m;
        if (!els.content || !els.body) {
            console.warn("[softrise] message page DOM missing — cannot render");
            return;
        }
        els.loading?.classList.add("hidden");
        els.error?.classList.add("hidden");
        els.content.classList.remove("hidden", "absolute", "-z-10", "opacity-0", "pointer-events-none");
        els.content.removeAttribute("aria-hidden");

        document.title = (m.subject ? `${m.subject}` : "(no subject)") + " · Soft Rice Mail";

        const subject = m.subject || "(no subject)";
        setText(els.subject, subject, "#messageSubject");
        const fromDisplay = m.from_name ? `${m.from_name} <${m.from_email || ""}>` : (m.from_email || "");
        const dateText = m.received_at ? new Date(m.received_at).toLocaleString() : "";
        setText(els.from, fromDisplay, "#messageFrom");
        setText(els.to, m.to_email || "", "#messageTo");
        setText(els.date, dateText, "#messageDate");

        renderAttachments(m.attachments || []);

        // The backend sanitizes ``body_html`` already. We still normalize the
        // rendered DOM so fixed-width email markup cannot push mobile layouts
        // sideways.
        if (m.body_html) {
            els.body.classList.remove("whitespace-pre-wrap");
            const html = normalizeEmailHtml(m.body_html);
            setHtml(
                els.body,
                `<div class="email-scale-wrapper"><div class="email-scale-inner">${html}</div></div>`,
                "#messageBody",
            );
            bindEmailScaleEvents();
            scheduleEmailScale();
        } else {
            els.body.classList.add("whitespace-pre-wrap");
            const bodyText = m.body_text || "(no content)";
            setText(els.body, bodyText, "#messageBody");
        }

        refreshToolbar();
    }

    // ---------- Main load ----------
    async function loadMessage() {
        const id = getMessageId();
        if (!id) {
            showError("Message not found.", "The URL is missing a message id.");
            return;
        }
        try {
            // Step 1: fetch message detail. We DO NOT pass mark_read=true here
            // because the spec asks us to call the explicit /read endpoint
            // when needed.
            const m = await apiRequest("GET", `/api/messages/${encodeURIComponent(id)}`);
            renderMessage(m);

            // Step 2: auto-mark-as-read on open if it was unread.
            if (m && m.is_read === false) {
                try {
                    await apiRequest("POST", `/api/messages/${encodeURIComponent(id)}/read`, { is_read: true });
                    if (current) {
                        current.is_read = true;
                        refreshToolbar();
                    }
                } catch (err) {
                    // Non-fatal — surface only if it's not an auth issue.
                    if (err.status === 401) return redirectToLogin();
                    console.warn("auto mark-as-read failed:", err);
                }
            }
        } catch (err) {
            if (err.status === 401) return redirectToLogin();
            if (err.status === 404) {
                showError(
                    "Message not found.",
                    "It may have been permanently deleted, or you don't have access to it.",
                );
                return;
            }
            showError("Could not open message.", err.message || "Please try again.");
        }
    }

    // ---------- Toolbar actions ----------
    function goBack() {
        // Prefer browser history when we have a referrer from the same origin.
        const sameOrigin = document.referrer && document.referrer.startsWith(window.location.origin);
        if (sameOrigin && window.history.length > 1) {
            window.history.back();
            return;
        }
        // Fall back to the folder we came from (?from=<folder>) or inbox.
        const folder = getReturnFolder();
        const url = folder && folder !== "inbox" ? `/?folder=${encodeURIComponent(folder)}` : "/";
        window.location.href = url;
    }

    els.backBtn?.addEventListener("click", (e) => {
        e.preventDefault();
        goBack();
    });

    els.starBtn?.addEventListener("click", async () => {
        if (!current) return;
        try {
            const r = await apiRequest("POST", `/api/messages/${encodeURIComponent(current.id)}/star`, {
                is_starred: !current.is_starred,
            });
            current.is_starred = !!r.is_starred;
            refreshToolbar();
            toast(current.is_starred ? "Starred" : "Unstarred", "info");
        } catch (err) {
            const message = userError(err, "Could not update star.");
            if (message) toast(message, "error");
        }
    });

    els.archiveBtn?.addEventListener("click", async () => {
        if (!current) return;
        try {
            await apiRequest("POST", `/api/messages/${encodeURIComponent(current.id)}/archive`);
            toast("Archived", "success");
            // Bounce back to where we came from after a brief beat so the
            // toast is visible.
            setTimeout(goBack, 350);
        } catch (err) {
            const message = userError(err, "Could not archive.");
            if (message) toast(message, "error");
        }
    });

    els.trashBtn?.addEventListener("click", async () => {
        if (!current) return;
        try {
            if (current.folder === "trash" || current.is_deleted) {
                if (!window.confirm("Permanently delete this email?")) return;
                await apiRequest("DELETE", `/api/messages/${encodeURIComponent(current.id)}?force=true`);
                toast("Permanently deleted", "success");
            } else {
                await apiRequest("POST", `/api/messages/${encodeURIComponent(current.id)}/trash`);
                toast("Moved to trash", "success");
            }
            setTimeout(goBack, 350);
        } catch (err) {
            const message = userError(err, "Could not delete.");
            if (message) toast(message, "error");
        }
    });

    els.toggleReadBtn?.addEventListener("click", async () => {
        if (!current) return;
        const next = !current.is_read;
        try {
            const r = await apiRequest("POST", `/api/messages/${encodeURIComponent(current.id)}/read`, {
                is_read: next,
            });
            current.is_read = !!r.is_read;
            refreshToolbar();
            toast(current.is_read ? "Marked as read" : "Marked as unread", "info");
        } catch (err) {
            const message = userError(err, "Could not update read state.");
            if (message) toast(message, "error");
        }
    });

    // Hardware/browser back button shortcut: Esc.
    window.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !e.target.matches("input,textarea,select")) {
            goBack();
        }
    });
    window.addEventListener("resize", scheduleEmailScale);
    window.addEventListener("orientationchange", () => window.setTimeout(fitEmailToMobile, 300));

    // ---------- Boot ----------
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", loadMessage);
    } else {
        loadMessage();
    }
})();
