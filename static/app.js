/* Softrise Mail — frontend application script.
 *
 * Connects the existing index.html design to the FastAPI backend without
 * changing the visual style. Original inline behavior (theme toggle, sidebar
 * open/close, long-press email actions, delete confirmation modal animations,
 * filter chips, load more) is preserved 1:1.  Dummy data has been replaced
 * with real API calls.
 */

(() => {
    "use strict";

    // ---------- Tiny API client ----------
    // Always send cookies with requests so the session survives a refresh,
    // even when the page is opened from an IP/HTTP origin where ``same-origin``
    // semantics could otherwise drop the cookie in some browsers.
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
        let res;
        try {
            res = await fetch(url, opts);
        } catch (err) {
            const e = new Error("Network error");
            e.netError = true;
            throw e;
        }
        let payload = null;
        try {
            payload = await res.json();
        } catch (_) {}
        if (!res.ok) {
            const e = new Error((payload && (payload.error || payload.detail)) || `HTTP ${res.status}`);
            e.status = res.status;
            e.payload = payload;
            throw e;
        }
        return payload;
    }

    // ---------- DOM helpers ----------
    const $ = (sel, root = document) => (root || document).querySelector(sel);
    const $$ = (sel, root = document) => Array.from((root || document).querySelectorAll(sel));

    /**
     * Safely set ``innerHTML`` on an element that may be missing.
     *
     * The browser will throw ``TypeError: Cannot set properties of null
     * (setting 'innerHTML')`` if you write into a null reference, and that
     * exception used to bubble all the way up to the user-facing toast. We
     * never want that — log a warning and bail.
     */
    function setHtml(el, html, name) {
        if (!el) {
            console.warn("[softrise] setHtml: missing element", name || "(unnamed)");
            return false;
        }
        el.innerHTML = html;
        return true;
    }

    function setHTML(selector, html) {
        const el = document.querySelector(selector);
        if (!el) {
            console.warn("Missing selector:", selector);
            return false;
        }
        el.innerHTML = html;
        return true;
    }

    function appendHtml(el, html, name) {
        if (!el) {
            console.warn("[softrise] appendHtml: missing element", name || "(unnamed)");
            return false;
        }
        el.insertAdjacentHTML("beforeend", html);
        return true;
    }

    function getHtml(el, name) {
        if (!el) {
            console.warn("[softrise] getHtml: missing element", name || "(unnamed)");
            return "";
        }
        return el.innerHTML;
    }

    function setText(el, value, name) {
        if (!el) {
            console.warn("[softrise] setText: missing element", name || "(unnamed)");
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

    function formatRelativeTime(iso) {
        if (!iso) return "";
        const d = new Date(iso);
        const now = new Date();
        const diff = (now - d) / 1000;
        if (Number.isNaN(diff)) return "";
        if (diff < 60) return "Just now";
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        const sameDay =
            d.getFullYear() === now.getFullYear() &&
            d.getMonth() === now.getMonth() &&
            d.getDate() === now.getDate();
        if (sameDay) {
            return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
        }
        const yesterday = new Date(now);
        yesterday.setDate(now.getDate() - 1);
        const isYesterday =
            d.getFullYear() === yesterday.getFullYear() &&
            d.getMonth() === yesterday.getMonth() &&
            d.getDate() === yesterday.getDate();
        if (isYesterday) return "Yesterday";
        if (now.getFullYear() === d.getFullYear()) {
            return d.toLocaleDateString([], { month: "short", day: "numeric" });
        }
        return d.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
    }

    function avatarFor(name, email) {
        const seed = (name || email || "?").trim();
        const letter = (seed.charAt(0) || "?").toUpperCase();
        const palette = [
            "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
            "bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300",
            "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
            "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
            "bg-slate-200 text-slate-800 dark:bg-slate-800 dark:text-slate-200",
            "bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-900/40 dark:text-fuchsia-300",
            "bg-pink-100 text-pink-700 dark:bg-pink-900/40 dark:text-pink-300",
            "bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300",
            "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300",
            "bg-zinc-200 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200",
        ];
        let h = 0;
        for (let i = 0; i < seed.length; i++) {
            h = (h * 31 + seed.charCodeAt(i)) >>> 0;
        }
        return { letter, style: palette[h % palette.length] };
    }

    // ---------- Toast notifications ----------
    function isTechnicalNullInnerHtmlError(message) {
        return typeof message === "string" && message.includes("Cannot set properties of null");
    }

    function toast(message, kind = "info") {
        if (isTechnicalNullInnerHtmlError(message)) {
            console.error("[softrise] suppressed technical toast:", message);
            return;
        }
        const stack = $("#toast-stack");
        if (!stack) return;
        const el = document.createElement("div");
        const palette = {
            info: "bg-neutral-900 text-white dark:bg-neutral-800",
            success: "bg-primary text-white",
            error: "bg-error text-white",
        }[kind] || "bg-neutral-900 text-white";
        el.className = `pointer-events-auto rounded-full px-4 py-2 text-xs shadow-xl border border-black/5 dark:border-white/5 ${palette} opacity-0 translate-y-2 transition-all duration-200 max-w-[90vw] break-words text-center`;
        el.textContent = message;
        stack.appendChild(el);
        requestAnimationFrame(() => {
            el.classList.remove("opacity-0", "translate-y-2");
        });
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
        console.error("[softrise] frontend action failed:", err);
        return fallback;
    }

    // ---------- Modal helpers (match existing animation pattern) ----------
    function openModal(id) {
        const m = document.getElementById(id);
        if (!m) return;
        m.classList.remove("hidden");
        document.body.style.overflow = "hidden";
        setTimeout(() => {
            m.classList.remove("opacity-0");
            const box = m.firstElementChild;
            if (box) {
                box.classList.remove("scale-95");
                box.classList.add("scale-100");
            }
        }, 10);
    }
    function closeModal(id) {
        const m = document.getElementById(id);
        if (!m) return;
        m.classList.add("opacity-0");
        const box = m.firstElementChild;
        if (box) {
            box.classList.remove("scale-100");
            box.classList.add("scale-95");
        }
        setTimeout(() => {
            m.classList.add("hidden");
            // restore body overflow only if no other modals open
            const anyOpen = $$(".fixed.inset-0").some((el) =>
                !el.classList.contains("hidden") &&
                el !== m &&
                el.classList.contains("z-[100]") + el.classList.contains("z-[110]") > 0,
            );
            if (!anyOpen) document.body.style.overflow = "";
        }, 200);
    }

    // ---------- Application state ----------
    const PAGE_CONFIG = {
        inbox: { kind: "messages", nav: "inbox", folder: "inbox" },
        starred: { kind: "messages", nav: "starred", starred: true },
        archive: { kind: "messages", nav: "archive", folder: "archive" },
        trash: { kind: "messages", nav: "trash", folder: "trash" },
        mailboxes: { kind: "mailboxes", nav: "mailboxes" },
        settings: { kind: "settings", nav: "settings" },
    };

    function getCurrentPage() {
        const tag = (document.body && document.body.dataset && document.body.dataset.page) || "";
        return PAGE_CONFIG[tag] ? tag : "";
    }

    function getPageConfig() {
        return PAGE_CONFIG[getCurrentPage()] || null;
    }

    const state = {
        me: null,
        mailboxes: [],
        currentFolder: null,
        currentPage: getCurrentPage(),
        currentSearch: "",
        currentReadFilter: "all", // all | unread | read
        currentMailboxId: null,
        emailsPerPage: 20,
        page: 1,
        pages: 1,
        total: 0,
        loadedItems: [],
        loading: false,
    };

    // ---------- DOM references ----------
    const dom = {};

    function cacheDom() {
        Object.assign(dom, {
            html: document.documentElement,
            body: document.body,
            menuBtn: $("#menu-btn"),
            closeMenuBtn: $("#close-menu-btn"),
            sidebar: $("#sidebar-menu"),
            menuOverlay: $("#menu-overlay"),
            themeToggle: $("#theme-toggle"),
            themeIcon: $("#theme-icon"),
            refreshBtn: $("#refresh-btn"),
            mobileSearchToggle: $("#mobile-search-toggle"),
            closeMobileSearch: $("#close-mobile-search"),
            mobileSearchContainer: $("#mobile-search-container"),
            mobileSearchInput: $("#mobile-search-input"),
            desktopSearchInput: $("#desktop-search-input"),
            emailList: $("#email-list"),
            loadMoreBtn: $("#load-more-btn"),
            loadMoreContainer: $("#load-more-container"),
            paginationText: $("#pagination-text"),
            filterButtons: $$(".filter-btn"),
            navItems: $$(".nav-item"),
            navAdmin: $("#nav-admin"),
            userName: $("#user-name"),
            userEmail: $("#user-email"),
            signOutBtn: $("#sign-out-btn"),
            deleteModal: $("#delete-modal"),
            cancelDelete: $("#cancel-delete"),
            confirmDelete: $("#confirm-delete"),
            mailboxesModal: $("#mailboxes-modal"),
            mailboxesList: $("#mailboxes-list"),
            tempCounter: $("#temp-counter"),
            createTempForm: $("#create-temp-form"),
            createTempInput: $("#create-temp-input"),
            createTempMessage: $("#create-temp-message"),
            settingsModal: $("#settings-modal"),
            settingsForm: $("#settings-form"),
            settingsMessage: $("#settings-message"),
        });
    }

    // ---------- Theme toggle (preserved) ----------
    function setupThemeToggle() {
        function updateThemeIcon() {
            if (!dom.themeIcon) return;
            if (dom.html.classList.contains("dark")) {
                dom.themeIcon.className = "ph ph-sun text-lg md:text-xl";
            } else {
                dom.themeIcon.className = "ph ph-moon text-lg md:text-xl";
            }
        }
        updateThemeIcon();
        dom.themeToggle?.addEventListener("click", () => {
            dom.html.classList.toggle("dark");
            try {
                localStorage.setItem(
                    "color-theme",
                    dom.html.classList.contains("dark") ? "dark" : "light",
                );
            } catch (_) { /* private mode */ }
            updateThemeIcon();
        });
    }

    // ---------- Sidebar open/close (preserved) ----------
    function setupSidebar() {
        if (!dom.sidebar || !dom.menuOverlay) {
            console.warn("[softrise] setupSidebar: sidebar/overlay missing — skipping");
            return;
        }
        const open = () => {
            dom.sidebar.classList.remove("-translate-x-full");
            dom.menuOverlay.classList.remove("hidden");
            setTimeout(() => dom.menuOverlay.classList.remove("opacity-0"), 10);
            document.body.style.overflow = "hidden";
        };
        const close = () => {
            dom.sidebar.classList.add("-translate-x-full");
            dom.menuOverlay.classList.add("opacity-0");
            setTimeout(() => dom.menuOverlay.classList.add("hidden"), 300);
            document.body.style.overflow = "";
        };
        dom.menuBtn?.addEventListener("click", open);
        dom.closeMenuBtn?.addEventListener("click", close);
        dom.menuOverlay.addEventListener("click", close);
    }

    // ---------- Mobile search (preserved) ----------
    function setupMobileSearch() {
        if (!dom.mobileSearchToggle || !dom.mobileSearchContainer || !dom.mobileSearchInput) return;
        dom.mobileSearchToggle.addEventListener("click", () => {
            dom.mobileSearchContainer.classList.remove("search-mobile-hidden");
            dom.mobileSearchContainer.classList.add("search-mobile-active");
            setTimeout(() => dom.mobileSearchInput.focus(), 100);
        });
        dom.closeMobileSearch?.addEventListener("click", () => {
            dom.mobileSearchContainer.classList.remove("search-mobile-active");
            dom.mobileSearchContainer.classList.add("search-mobile-hidden");
            dom.mobileSearchInput.value = "";
            state.currentSearch = "";
            if (getPageConfig()?.kind === "messages") {
                loadMessages(true);
            }
        });

        const desktopClearBtn = document.querySelector(".hidden.sm\\:block button");
        if (desktopClearBtn && dom.desktopSearchInput) {
            desktopClearBtn.addEventListener("click", () => {
                dom.desktopSearchInput.value = "";
                state.currentSearch = "";
                if (getPageConfig()?.kind === "messages") {
                    loadMessages(true);
                }
                dom.desktopSearchInput.focus();
            });
        }
    }

    // ---------- Auth flow ----------
    // The dedicated /login and /register pages handle credential entry. From
    // the SPA's perspective we only ever need to redirect to /login when a
    // protected request comes back as 401, and to wire the sign-out button.
    function redirectToLogin() {
        // Avoid bouncing if we're already mid-redirect.
        if (window.__softrise_redirecting) return;
        window.__softrise_redirecting = true;
        window.location.replace("/login");
    }

    function setupAuth() {
        dom.signOutBtn?.addEventListener("click", async () => {
            try {
                await apiRequest("POST", "/api/auth/logout");
            } catch (_) {}
            state.me = null;
            state.mailboxes = [];
            // Wipe everything that could leak a previous user's identity.
            if (dom.userName) dom.userName.textContent = "";
            if (dom.userEmail) dom.userEmail.textContent = "";
            if (dom.navAdmin) dom.navAdmin.style.display = "none";
            setHtml(dom.emailList, "", "#email-list");
            const badge = document.querySelector('[data-nav-count="inbox"]');
            if (badge) badge.textContent = "0";
            // Make sure no stale legacy demo keys live in localStorage.
            try {
                ["softrise_user", "demo_user", "user", "current_user", "softrise_demo"].forEach((k) =>
                    localStorage.removeItem(k),
                );
            } catch (_) {}
            redirectToLogin();
        });
    }

    // ---------- Sidebar nav wiring ----------
    function setupNav() {
        const activeBlock = ["bg-surface-container-low/60", "dark:bg-neutral-800/50", "text-neutral-900", "dark:text-neutral-200", "border-primary", "dark:border-neutral-400"];
        const inactiveBlock = ["text-neutral-600", "dark:text-neutral-400", "border-transparent"];
        function setActiveNav(target) {
            dom.navItems.forEach((a) => {
                if (a.dataset.nav === target) {
                    a.classList.remove(...inactiveBlock);
                    a.classList.add(...activeBlock);
                } else {
                    a.classList.remove(...activeBlock);
                    a.classList.add(...inactiveBlock);
                }
            });
        }
        function closeSidebarOnMobile() {
            if (window.innerWidth < 1024) {
                dom.closeMenuBtn?.click();
            }
        }
        setActiveNav(getPageConfig()?.nav || "inbox");
        dom.navItems.forEach((a) => {
            a.addEventListener("click", () => closeSidebarOnMobile());
        });
    }

    // ---------- Email list ----------
    function setupFilterChips() {
        const activeCls = ["bg-primary/10", "text-primary", "dark:bg-neutral-800", "dark:text-neutral-200", "border-transparent", "active", "font-medium"];
        const inactiveCls = ["bg-transparent", "text-neutral-500", "border-outline-variant/30", "font-normal"];
        dom.filterButtons.forEach((btn) => {
            btn.addEventListener("click", () => {
                dom.filterButtons.forEach((b) => {
                    b.classList.remove(...activeCls);
                    b.classList.add(...inactiveCls);
                });
                btn.classList.add(...activeCls);
                btn.classList.remove(...inactiveCls);
                state.currentReadFilter = btn.dataset.filter;
                state.page = 1;
                loadMessages(true);
            });
        });
    }

    function buildEmailItemHtml(item) {
        const isUnread = !item.is_read;
        const page = getCurrentPage();
        const bgClass = isUnread ? "bg-white dark:bg-[#121212]" : "bg-stone-50/30 dark:bg-[#0a0a0a]";
        const nameWeight = isUnread ? "font-semibold" : "font-medium";
        const subjectWeight = isUnread ? "font-medium" : "font-normal";
        const timeWeight = isUnread ? "font-semibold" : "font-normal";
        const titleColor = isUnread ? "text-neutral-900 dark:text-white" : "text-neutral-700 dark:text-neutral-300";
        const subjectColor = isUnread ? "text-neutral-800 dark:text-neutral-200" : "text-neutral-600 dark:text-neutral-400";
        const timeColor = isUnread
            ? `text-primary dark:text-neutral-300 ${timeWeight}`
            : `text-neutral-500 dark:text-neutral-500 ${timeWeight}`;
        const snippetColor = isUnread
            ? "text-neutral-600 dark:text-neutral-400 font-normal"
            : "text-neutral-500 dark:text-neutral-500 font-normal";
        const readIcon = isUnread ? "ph-envelope-open" : "ph-envelope-simple";
        const readText = isUnread ? "Read" : "Unread";
        const av = avatarFor(item.from_name, item.from_email);
        const senderDisplay = item.from_name || item.from_email || "Unknown sender";
        const subject = item.subject || "(no subject)";
        const snippet = item.snippet || "";
        const time = formatRelativeTime(item.received_at);
        const starFilled = item.is_starred ? "ph-fill" : "";
        const starColor = item.is_starred
            ? "text-tertiary dark:text-yellow-400"
            : "text-neutral-600 dark:text-neutral-300";
        const secondaryAction = page === "trash"
            ? { action: "inbox", icon: "ph-arrow-counter-clockwise", label: "Restore" }
            : page === "archive"
                ? { action: "inbox", icon: "ph-tray", label: "Inbox" }
                : { action: "archive", icon: "ph-archive", label: "Archive" };
        const deleteAction = page === "trash"
            ? { action: "delete", label: "Delete" }
            : { action: "trash", label: "Trash" };

        return `
        <div data-message-id="${escapeHtml(item.id)}" data-status="${isUnread ? "unread" : "read"}" data-starred="${item.is_starred ? "1" : "0"}" class="email-item relative px-4 py-3.5 border-b border-outline-variant/10 dark:border-neutral-800 cursor-pointer ${bgClass} select-none transition-colors overflow-hidden fade-in">
            <div class="flex items-start gap-3 sm:gap-4 w-full">
                <div class="w-10 h-10 rounded-full flex items-center justify-center font-medium text-[16px] flex-shrink-0 mt-0.5 ${av.style}">
                    ${escapeHtml(av.letter)}
                </div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center justify-between mb-0.5">
                        <h2 class="${nameWeight} text-[15px] ${titleColor} truncate pr-2">${escapeHtml(senderDisplay)}</h2>
                        <span class="text-[12px] ${timeColor} flex-shrink-0">${escapeHtml(time)}</span>
                    </div>
                    <h3 class="${subjectWeight} text-[14px] ${subjectColor} truncate">${escapeHtml(subject)}</h3>
                    <p class="text-[13px] ${snippetColor} line-clamp-1 mt-0.5">${escapeHtml(snippet)}</p>
                </div>
            </div>
            <div class="action-overlay absolute inset-0 bg-surface-container-low/95 dark:bg-neutral-800/95 backdrop-blur-md flex items-center justify-center gap-6 sm:gap-8 opacity-0 pointer-events-none transition-opacity duration-200 z-10 rounded-sm px-4">
                <button data-action="star" class="star-action-btn flex flex-col items-center gap-1 ${starColor} hover:text-tertiary dark:hover:text-yellow-400 transition-colors w-12">
                    <i class="star-icon ph ${starFilled} ph-star text-[24px] sm:text-[26px]"></i>
                    <span class="star-text text-[10px] font-medium uppercase tracking-wider">${item.is_starred ? "Starred" : "Star"}</span>
                </button>
                <button data-action="${secondaryAction.action}" class="secondary-action-btn flex flex-col items-center gap-1 text-neutral-600 dark:text-neutral-300 hover:text-primary dark:hover:text-white transition-colors w-12">
                    <i class="ph ${secondaryAction.icon} text-[24px] sm:text-[26px]"></i>
                    <span class="text-[10px] font-medium uppercase tracking-wider">${secondaryAction.label}</span>
                </button>
                <button data-action="${deleteAction.action}" class="delete-action-btn flex flex-col items-center gap-1 text-neutral-600 dark:text-neutral-300 hover:text-error dark:hover:text-red-400 transition-colors w-12">
                    <i class="ph ph-trash text-[24px] sm:text-[26px]"></i>
                    <span class="text-[10px] font-medium uppercase tracking-wider">${deleteAction.label}</span>
                </button>
                <button data-action="more" class="read-action-btn flex flex-col items-center gap-1 text-neutral-600 dark:text-neutral-300 hover:text-primary dark:hover:text-white transition-colors w-12">
                    <i class="ph ${readIcon} text-[24px] sm:text-[26px]"></i>
                    <span class="text-[10px] font-medium uppercase tracking-wider">${readText}</span>
                </button>
                <button data-action="more" class="close-action-btn absolute right-2 top-1/2 -translate-y-1/2 p-2 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 transition-colors flex items-center justify-center">
                    <i class="ph ph-x text-xl"></i>
                </button>
            </div>
        </div>`;
    }

    function emptyStateHtml() {
        const fEmoji = {
            inbox: "ph-tray",
            archive: "ph-archive",
            trash: "ph-trash",
            starred: "ph-star",
        }[state.currentFolder] || "ph-tray";
        const label = (state.currentSearch
            ? `No matches for "${escapeHtml(state.currentSearch)}".`
            : `Nothing in ${state.currentFolder}.`);
        const sub = state.currentFolder === "inbox"
            ? `Your @${(state.me && state.me.default_mailbox && state.me.default_mailbox.email_address.split("@")[1]) || "softrise.app"} inbox is empty. New mail arrives here automatically.`
            : "";
        return `<div class="px-4 py-16 text-center text-neutral-500 dark:text-neutral-400">
            <i class="ph ${fEmoji} text-4xl block mb-3 text-neutral-300 dark:text-neutral-600"></i>
            <p class="text-sm font-medium text-neutral-600 dark:text-neutral-300">${label}</p>
            <p class="text-[13px] mt-1">${escapeHtml(sub)}</p>
        </div>`;
    }

    function loadingStateHtml() {
        return `<div class="px-4 py-16 text-center text-neutral-500 dark:text-neutral-400">
            <i class="ph ph-spinner-gap animate-spin text-3xl block mb-3"></i>
            <p class="text-[13px]">Loading...</p>
        </div>`;
    }

    async function loadMessages(replace = false) {
        // Guard against being called on a page that doesn't have the inbox
        // shell at all (e.g. running on /login or a stale cached document).
        if (!dom.emailList) {
            console.warn("[softrise] loadMessages: #email-list missing — skipping");
            return;
        }
        if (state.loading) return;
        state.loading = true;
        if (replace) {
            setHtml(dom.emailList, loadingStateHtml(), "#email-list");
        }
        try {
            const params = new URLSearchParams();
            if (state.currentFolder === "starred") {
                params.set("starred", "true");
            } else {
                params.set("folder", state.currentFolder);
            }
            if (state.currentSearch) params.set("search", state.currentSearch);
            if (state.currentReadFilter === "read") params.set("read", "true");
            if (state.currentReadFilter === "unread") params.set("read", "false");
            if (state.currentMailboxId) params.set("mailbox_id", state.currentMailboxId);
            params.set("page", state.page.toString());
            params.set("limit", state.emailsPerPage.toString());

            const data = await apiRequest("GET", `/api/messages?${params.toString()}`);
            state.pages = data.pages;
            state.total = data.total;
            if (replace) {
                state.loadedItems = [];
                setHtml(dom.emailList, "", "#email-list");
            }
            data.items.forEach((it) => state.loadedItems.push(it));
            const html = data.items.map(buildEmailItemHtml).join("");
            if (replace) {
                setHtml(dom.emailList, html, "#email-list");
            } else {
                appendHtml(dom.emailList, html, "#email-list");
            }
            if (state.loadedItems.length === 0) {
                setHtml(dom.emailList, emptyStateHtml(), "#email-list");
            }
            const start = state.loadedItems.length === 0 ? 0 : (state.page - 1) * state.emailsPerPage + 1;
            const end = Math.min(state.page * state.emailsPerPage, state.total);
            const range = state.total === 0 ? "0 of 0" : `${start}-${end} of ${state.total}`;
            setText(dom.paginationText, range, "#pagination-text");
            // Update inbox count badge (cheap approximation: total of folder=inbox)
            updateInboxCountBadge();

            const more = state.page < state.pages;
            if (dom.loadMoreContainer) {
                dom.loadMoreContainer.style.display = more ? "" : "none";
            }
        } catch (err) {
            if (err && err.status === 401) {
                redirectToLogin();
                return;
            }
            console.error("[softrise] loadMessages failed:", err);
            // Only show a friendly toast for real backend errors, never raw
            // technical exceptions.
            if (err && err.status) {
                setHtml(
                    dom.emailList,
                    `<div class="px-4 py-12 text-center text-error">${escapeHtml(err.message || "Failed to load emails.")}</div>`,
                    "#email-list",
                );
            } else {
                setHtml(
                    dom.emailList,
                    `<div class="px-4 py-12 text-center text-error">Could not load emails. Please try again.</div>`,
                    "#email-list",
                );
            }
        } finally {
            state.loading = false;
        }
    }

    async function updateInboxCountBadge() {
        const badge = document.querySelector('[data-nav-count="inbox"]');
        if (!badge) return;
        try {
            const r = await apiRequest("GET", "/api/messages?folder=inbox&read=false&limit=1&page=1");
            badge.textContent = r.total;
            badge.classList.toggle("opacity-0", r.total === 0);
        } catch (_) { /* ignore */ }
    }

    // ---------- Email item interactions (long-press, actions) ----------
    function setupEmailListInteractions() {
        if (!dom.emailList) {
            console.warn("[softrise] setupEmailListInteractions: #email-list missing — skipping");
            return;
        }
        let pressTimer = null;

        const pressStart = (e) => {
            const item = e.target.closest(".email-item");
            if (!item || e.target.closest("button")) return;
            pressTimer = window.setTimeout(() => {
                $$(".email-item.show-actions").forEach((el) => el.classList.remove("show-actions"));
                item.classList.add("show-actions");
                if (navigator.vibrate) navigator.vibrate(50);
            }, 500);
        };
        const pressCancel = () => clearTimeout(pressTimer);

        dom.emailList.addEventListener("mousedown", pressStart);
        dom.emailList.addEventListener("touchstart", pressStart, { passive: true });
        window.addEventListener("mouseup", pressCancel);
        window.addEventListener("touchend", pressCancel);

        document.addEventListener("click", function (e) {
            const row = e.target.closest("[data-message-id]");
            if (!row) return;

            if (e.target.closest("[data-action]") || e.target.closest("button")) {
                return;
            }

            const id = row.dataset.messageId;
            if (!id) return;

            console.log("EMAIL_ROW_CLICK_NAVIGATE", id);
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();

            window.location.href = `/message/${encodeURIComponent(id)}`;
        }, true);

        dom.emailList.addEventListener("click", async (e) => {
            const closeBtn = e.target.closest(".close-action-btn");
            if (closeBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                closeBtn.closest(".email-item")?.classList.remove("show-actions");
                return;
            }
            const item = e.target.closest("[data-message-id]");
            if (!item) return;
            const id = item.dataset.messageId;
            if (!id) return;

            const starBtn = e.target.closest(".star-action-btn");
            if (starBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                await toggleStar(id, item);
                return;
            }
            const secondaryBtn = e.target.closest(".secondary-action-btn");
            if (secondaryBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                if (secondaryBtn.dataset.action === "inbox") {
                    await moveToInbox(id, item);
                } else {
                    await archiveItem(id, item);
                }
                return;
            }
            const deleteBtn = e.target.closest(".delete-action-btn");
            if (deleteBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                if (deleteBtn.dataset.action === "delete") {
                    showDeleteConfirm(id, item);
                } else {
                    await moveToTrash(id, item);
                }
                return;
            }
            const readBtn = e.target.closest(".read-action-btn");
            if (readBtn) {
                e.preventDefault();
                e.stopPropagation();
                e.stopImmediatePropagation();
                await toggleReadAction(id, item);
                return;
            }
            // Plain click navigates to the dedicated message page.
            if (!item.classList.contains("show-actions")) {
                e.preventDefault();
                navigateToMessage(id);
            }
        });

        document.addEventListener("click", (e) => {
            if (!e.target.closest(".email-item") && !e.target.closest("#delete-modal")) {
                $$(".email-item.show-actions").forEach((el) => el.classList.remove("show-actions"));
            }
        });
    }

    async function toggleStar(id, item) {
        const wasStarred = item.dataset.starred === "1";
        try {
            const r = await apiRequest("POST", `/api/messages/${id}/star`, { is_starred: !wasStarred });
            item.dataset.starred = r.is_starred ? "1" : "0";
            const icon = item.querySelector(".star-icon");
            const text = item.querySelector(".star-text");
            const btn = item.querySelector(".star-action-btn");
            if (icon && text && btn) {
                icon.classList.toggle("ph-fill", r.is_starred);
                btn.classList.toggle("text-tertiary", r.is_starred);
                btn.classList.toggle("dark:text-yellow-400", r.is_starred);
                btn.classList.toggle("text-neutral-600", !r.is_starred);
                btn.classList.toggle("dark:text-neutral-300", !r.is_starred);
                text.textContent = r.is_starred ? "Starred" : "Star";
            }
            if (state.currentFolder === "starred" && !r.is_starred) {
                animateRemove(item);
            }
            toast(r.is_starred ? "Starred" : "Unstarred", "info");
        } catch (err) {
            const message = userError(err, "Could not update star.");
            if (message) toast(message, "error");
        }
    }

    async function archiveItem(id, item) {
        try {
            await apiRequest("POST", `/api/messages/${id}/archive`);
            if (state.currentFolder !== "archive") animateRemove(item);
            toast("Archived", "success");
        } catch (err) {
            const message = userError(err, "Could not archive.");
            if (message) toast(message, "error");
        }
    }

    async function moveToInbox(id, item) {
        try {
            await apiRequest("POST", `/api/messages/${id}/inbox`);
            if (state.currentFolder !== "inbox") animateRemove(item);
            toast("Moved to inbox", "success");
        } catch (err) {
            const message = userError(err, "Could not move to inbox.");
            if (message) toast(message, "error");
        }
    }

    async function moveToTrash(id, item) {
        try {
            await apiRequest("POST", `/api/messages/${id}/trash`);
            if (state.currentFolder !== "trash") animateRemove(item);
            toast("Moved to trash", "success");
        } catch (err) {
            const message = userError(err, "Could not move to trash.");
            if (message) toast(message, "error");
        }
    }

    async function toggleReadAction(id, item) {
        const wasUnread = item.dataset.status === "unread";
        try {
            await apiRequest("POST", `/api/messages/${id}/read`, { is_read: wasUnread });
            // Reload current page to reflect style change cleanly
            loadMessages(true);
        } catch (err) {
            const message = userError(err, "Could not update read state.");
            if (message) toast(message, "error");
        }
    }

    function animateRemove(item) {
        item.classList.remove("show-actions");
        item.style.height = item.offsetHeight + "px";
        item.style.transition = "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)";
        item.style.overflow = "hidden";
        item.offsetHeight; // force reflow
        item.style.height = "0";
        item.style.paddingTop = "0";
        item.style.paddingBottom = "0";
        item.style.opacity = "0";
        item.style.border = "none";
        setTimeout(() => {
            item.remove();
            const remaining = $$(".email-item").length;
            state.total = Math.max(0, state.total - 1);
            if (remaining === 0) setHtml(dom.emailList, emptyStateHtml(), "#email-list");
        }, 300);
    }

    // ---------- Delete confirmation modal (preserved animation) ----------
    let pendingDeleteId = null;
    let pendingDeleteItem = null;

    function showDeleteConfirm(id, item) {
        pendingDeleteId = id;
        pendingDeleteItem = item;
        if (!dom.deleteModal) {
            console.warn("[softrise] showDeleteConfirm: #delete-modal missing — skipping");
            pendingDeleteId = null;
            pendingDeleteItem = null;
            return;
        }
        // Update modal text depending on whether this is in trash already
        const isInTrash = state.currentFolder === "trash";
        const title = dom.deleteModal.querySelector("h3");
        const desc = dom.deleteModal.querySelector("p");
        if (title) title.textContent = isInTrash ? "Delete permanently?" : "Move to trash?";
        if (desc) desc.textContent = isInTrash
            ? "This email will be permanently removed. This action cannot be undone."
            : "Are you sure you want to move this email to trash? You can recover it later from the Trash folder.";
        openModal("delete-modal");
    }

    function hideDeleteConfirm() {
        closeModal("delete-modal");
        pendingDeleteId = null;
        pendingDeleteItem = null;
    }

    function setupDeleteModal() {
        dom.cancelDelete?.addEventListener("click", hideDeleteConfirm);
        dom.deleteModal?.addEventListener("click", (e) => {
            if (e.target === dom.deleteModal) hideDeleteConfirm();
        });
        dom.confirmDelete?.addEventListener("click", async () => {
            if (!pendingDeleteId) return;
            const item = pendingDeleteItem;
            const isInTrash = state.currentFolder === "trash";
            try {
                if (isInTrash) {
                    await apiRequest("DELETE", `/api/messages/${pendingDeleteId}?force=true`);
                } else {
                    await apiRequest("POST", `/api/messages/${pendingDeleteId}/trash`);
                }
                hideDeleteConfirm();
                if (item) animateRemove(item);
                toast(isInTrash ? "Deleted" : "Moved to trash", "success");
            } catch (err) {
                hideDeleteConfirm();
                const message = userError(err, "Could not delete.");
                if (message) toast(message, "error");
            }
        });
    }

    // ---------- Load more ----------
    function setupLoadMore() {
        if (!dom.loadMoreBtn) {
            console.warn("[softrise] setupLoadMore: #load-more-btn missing — skipping");
            return;
        }
        dom.loadMoreBtn.addEventListener("click", async () => {
            if (state.page >= state.pages) return;
            const original = getHtml(dom.loadMoreBtn, "#load-more-btn");
            setHtml(
                dom.loadMoreBtn,
                '<i class="ph ph-spinner-gap animate-spin text-lg"></i> <span>Loading...</span>',
                "#load-more-btn",
            );
            dom.loadMoreBtn.disabled = true;
            state.page += 1;
            await loadMessages(false);
            setHtml(dom.loadMoreBtn, original, "#load-more-btn");
            dom.loadMoreBtn.disabled = false;
        });
    }

    // ---------- Search ----------
    function setupSearch() {
        const onSearch = (val) => {
            state.currentSearch = val.trim();
            state.page = 1;
            loadMessages(true);
        };
        let t;
        const debounce = (fn, ms) => (...args) => {
            clearTimeout(t);
            t = setTimeout(() => fn(...args), ms);
        };
        dom.desktopSearchInput?.addEventListener(
            "input",
            debounce((e) => onSearch(e.target.value), 350),
        );
        dom.mobileSearchInput?.addEventListener(
            "input",
            debounce((e) => onSearch(e.target.value), 350),
        );
        // Sync the two so the visible UI matches state when toggling
        dom.desktopSearchInput?.addEventListener("input", (e) => {
            if (dom.mobileSearchInput) dom.mobileSearchInput.value = e.target.value;
        });
        dom.mobileSearchInput?.addEventListener("input", (e) => {
            if (dom.desktopSearchInput) dom.desktopSearchInput.value = e.target.value;
        });
    }

    // ---------- Message detail navigation ----------
    // Opening an email is a *navigation*, not a modal: each message has its
    // own URL (/message/{id}) so it can be deep-linked, refreshed, and
    // rendered properly on small screens.
    function navigateToMessage(id) {
        if (!id) return;
        window.location.href = `/message/${encodeURIComponent(id)}`;
    }

    // ---------- Mailboxes page ----------
    async function refreshMailboxes() {
        if (!dom.mailboxesList) return;
        try {
            const list = await apiRequest("GET", "/api/mailboxes?include_deleted=true");
            state.mailboxes = list;
            renderMailboxes();
        } catch (err) {
            console.error("[softrise] refreshMailboxes failed:", err);
            setHtml(
                dom.mailboxesList,
                `<div class="px-5 py-8 text-center text-error">${escapeHtml(err.message || "Could not load mailboxes.")}</div>`,
                "#mailboxes-list",
            );
        }
    }

    function renderMailboxes() {
        const list = state.mailboxes || [];
        const activeTemps = list.filter((m) => m.type === "temp" && !m.deleted_at).length;
        const TEMP_LIMIT = 10;
        setText(dom.tempCounter, `(${activeTemps}/${TEMP_LIMIT} active)`, "#temp-counter");
        if (list.length === 0) {
            setHtml(
                dom.mailboxesList,
                `<div class="px-5 py-8 text-center text-neutral-500 dark:text-neutral-400 text-sm">No mailboxes yet.</div>`,
                "#mailboxes-list",
            );
            return;
        }
        const rows = list.map((m) => mailboxRowHtml(m)).join("");
        setHtml(dom.mailboxesList, rows, "#mailboxes-list");
    }

    function mailboxRowHtml(m) {
        const isDeleted = !!m.deleted_at;
        const tag = m.is_default
            ? `<span class="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-primary/10 text-primary dark:bg-neutral-800 dark:text-neutral-200">Default</span>`
            : `<span class="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-stone-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">${m.type === "temp" ? "Temporary" : m.type}</span>`;
        const deletedBadge = isDeleted
            ? `<span class="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-error-container/20 text-error dark:bg-red-900/20 dark:text-red-400">Deleted</span>`
            : "";
        const actions = [];
        actions.push(`<button data-mb-action="copy" data-id="${escapeHtml(m.id)}" data-email="${escapeHtml(m.email_address)}" class="px-2.5 py-1 rounded-md text-[11px] uppercase tracking-wider bg-stone-100 dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 hover:bg-stone-200 dark:hover:bg-neutral-700 transition-colors">Copy</button>`);
        if (!m.is_default) {
            if (isDeleted) {
                actions.push(`<button data-mb-action="restore" data-id="${escapeHtml(m.id)}" class="px-2.5 py-1 rounded-md text-[11px] uppercase tracking-wider bg-primary text-white hover:bg-[#4a4949] transition-colors">Restore</button>`);
            } else {
                actions.push(`<button data-mb-action="delete" data-id="${escapeHtml(m.id)}" class="px-2.5 py-1 rounded-md text-[11px] uppercase tracking-wider bg-error-container/20 text-error hover:bg-error-container/40 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/40 transition-colors">Delete</button>`);
            }
        }
        return `
        <div class="px-5 py-3 border-b border-outline-variant/10 dark:border-neutral-800 flex items-center gap-3">
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 text-sm font-medium text-neutral-900 dark:text-neutral-200 truncate">
                    <span>${escapeHtml(m.email_address)}</span>
                </div>
                <div class="flex items-center gap-2 mt-1">${tag}${deletedBadge}<span class="text-[11px] text-neutral-500 dark:text-neutral-400">created ${escapeHtml(formatRelativeTime(m.created_at))}</span></div>
            </div>
            <div class="flex items-center gap-2 flex-shrink-0">${actions.join("")}</div>
        </div>`;
    }

    function setupMailboxesPage() {
        if (!dom.mailboxesList) {
            console.warn("[softrise] setupMailboxesPage: #mailboxes-list missing — skipping");
            return;
        }

        dom.createTempForm?.addEventListener("submit", async (e) => {
            e.preventDefault();
            const local = (dom.createTempInput && dom.createTempInput.value || "").trim();
            try {
                const r = await apiRequest("POST", "/api/mailboxes/temp", local ? { local_part: local } : {});
                if (dom.createTempInput) dom.createTempInput.value = "";
                showCreateTempMessage(`Created ${r.email_address}`, "success");
                await refreshMailboxes();
                toast("Temporary email created", "success");
            } catch (err) {
                console.error("[softrise] create temp mailbox failed:", err);
                showCreateTempMessage(err.message || "Failed to create.", "error");
            }
        });

        dom.mailboxesList?.addEventListener("click", async (e) => {
            const btn = e.target.closest("[data-mb-action]");
            if (!btn) return;
            const id = btn.dataset.id;
            const action = btn.dataset.mbAction;
            if (action === "copy") {
                try {
                    await navigator.clipboard.writeText(btn.dataset.email);
                    toast("Email copied", "success");
                } catch (_) {
                    toast("Could not copy to clipboard", "error");
                }
            } else if (action === "delete") {
                try {
                    await apiRequest("DELETE", `/api/mailboxes/${id}`);
                    await refreshMailboxes();
                    toast("Temporary email deleted", "success");
                } catch (err) {
                    const message = userError(err, "Could not delete.");
                    if (message) toast(message, "error");
                }
            } else if (action === "restore") {
                try {
                    await apiRequest("POST", `/api/mailboxes/${id}/restore`);
                    await refreshMailboxes();
                    toast("Restored", "success");
                } catch (err) {
                    const message = userError(err, "Could not restore.");
                    if (message) toast(message, "error");
                }
            }
        });
    }

    function showCreateTempMessage(text, kind) {
        const el = dom.createTempMessage;
        if (!el) return;
        el.textContent = text;
        el.classList.remove("hidden", "text-error", "text-primary", "dark:text-red-400", "dark:text-neutral-200");
        el.classList.add(kind === "error" ? "text-error" : "text-primary");
        if (kind === "error") el.classList.add("dark:text-red-400");
        else el.classList.add("dark:text-neutral-200");
        clearTimeout(showCreateTempMessage._t);
        showCreateTempMessage._t = setTimeout(() => el.classList.add("hidden"), 4000);
    }

    // ---------- Settings page ----------
    async function loadSettingsPage() {
        if (!state.me) {
            redirectToLogin();
            return;
        }
        const form = dom.settingsForm;
        if (!form) return;
        let s;
        let mailboxes;
        try {
            [s, mailboxes] = await Promise.all([
                apiRequest("GET", "/api/settings"),
                apiRequest("GET", "/api/mailboxes?include_deleted=false"),
            ]);
        } catch (err) {
            if (err && err.status === 401) {
                state.me = null;
                redirectToLogin();
                return;
            }
            const message = userError(err, "Could not load settings.");
            if (message) toast(message, "error");
            return;
        }
        if (form.elements.display_name) form.elements.display_name.value = s.display_name || "";
        if (form.elements.emails_per_page) form.elements.emails_per_page.value = s.emails_per_page || 20;
        const select = form.elements.default_mailbox_id;
        if (select) {
            setHtml(
                select,
                mailboxes
                    .map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.email_address)}${m.is_default ? " (default)" : ""}</option>`)
                    .join(""),
                "select[name=default_mailbox_id]",
            );
            select.value = s.default_mailbox_id || (mailboxes.find((m) => m.is_default) || {}).id || "";
        }
    }

    function setupSettingsPage() {
        if (!dom.settingsForm) {
            console.warn("[softrise] setupSettingsPage: #settings-form missing — skipping");
            return;
        }
        dom.settingsForm?.addEventListener("submit", async (e) => {
            e.preventDefault();
            const fd = new FormData(dom.settingsForm);
            const payload = {
                display_name: fd.get("display_name") || "",
                emails_per_page: parseInt(fd.get("emails_per_page") || "20", 10),
                default_mailbox_id: fd.get("default_mailbox_id") || null,
            };
            try {
                const updated = await apiRequest("POST", "/api/settings", payload);
                state.emailsPerPage = updated.emails_per_page || 20;
                // Always sync the user name in the sidebar to whatever the backend
                // just stored for display_name. We deliberately do NOT key this on
                // state.me being truthy, because state.me may have been replaced
                // by a recent /api/auth/me refresh that has the OLD name baked in.
                const newName = (updated.display_name && updated.display_name.trim())
                    || (state.me && (state.me.name || state.me.username))
                    || "";
                if (state.me) state.me.name = newName;
                if (dom.userName) dom.userName.textContent = newName;
                toast("Settings saved", "success");
                if (getPageConfig()?.kind === "messages") {
                    loadMessages(true);
                }
            } catch (err) {
                if (dom.settingsMessage) {
                    dom.settingsMessage.textContent = err.message || "Could not save settings.";
                    dom.settingsMessage.classList.remove("hidden");
                    dom.settingsMessage.classList.add("text-error");
                }
            }
        });
    }

    // ---------- Read-all (filter chip integration) ----------
    function setupReadAllAffordance() {
        // Long-press the "All" filter chip triggers read-all.  Single-click is filter.
        const allBtn = document.querySelector('.filter-btn[data-filter="all"]');
        if (!allBtn) return;
        let longTimer;
        const start = () => {
            longTimer = setTimeout(async () => {
                if (confirm("Mark all messages in this folder as read?")) {
                    try {
                        const r = await apiRequest("POST", "/api/messages/read-all", { folder: state.currentFolder });
                        toast(`Marked ${r.updated} as read`, "success");
                        loadMessages(true);
                    } catch (err) {
                        const message = userError(err, "Could not mark as read.");
                        if (message) toast(message, "error");
                    }
                }
            }, 800);
        };
        const cancel = () => clearTimeout(longTimer);
        allBtn.addEventListener("mousedown", start);
        allBtn.addEventListener("touchstart", start, { passive: true });
        ["mouseup", "touchend", "mouseleave"].forEach((ev) => allBtn.addEventListener(ev, cancel));
        // Also expose a keyboard shortcut: Shift+R reads all.
        window.addEventListener("keydown", (e) => {
            if (e.shiftKey && (e.key === "R" || e.key === "r") && !e.target.matches("input,textarea,select")) {
                e.preventDefault();
                allBtn.dispatchEvent(new MouseEvent("mousedown"));
                setTimeout(() => allBtn.dispatchEvent(new MouseEvent("mouseup")), 850);
            }
        });
    }

    // ---------- User info wiring ----------
    function renderUserInfo() {
        const me = state.me;
        if (!me) {
            // Always blank the sidebar when there's no authenticated user so we
            // never leak the previous (or any demo) identity.
            if (dom.userName) dom.userName.textContent = "";
            if (dom.userEmail) dom.userEmail.textContent = "";
            if (dom.navAdmin) dom.navAdmin.style.display = "none";
            return;
        }
        if (dom.userName) dom.userName.textContent = (me.name && me.name.trim()) || me.username;
        if (dom.userEmail) {
            dom.userEmail.textContent = me.default_mailbox
                ? me.default_mailbox.email_address
                : (me.email || `${me.username}@softrise.app`);
        }
        // Apply user emails_per_page setting
        const perPage = parseInt((me.settings && me.settings.emails_per_page) || 0, 10);
        if (perPage >= 5 && perPage <= 100) state.emailsPerPage = perPage;
        // Toggle admin link
        if (dom.navAdmin) dom.navAdmin.style.display = me.role === "admin" ? "" : "none";
    }

    // If /api/auth/me succeeds but for some reason doesn't include a default
    // mailbox (older sessions, manual DB tweaks, etc.), fetch /api/mailboxes
    // and patch state.me so the sidebar always shows the real address.
    async function ensureDefaultMailbox() {
        if (!state.me || (state.me.default_mailbox && state.me.default_mailbox.email_address)) {
            return;
        }
        try {
            const list = await apiRequest("GET", "/api/mailboxes?include_deleted=false");
            const def = (list || []).find((m) => m.is_default) || (list || [])[0];
            if (def) {
                state.me.default_mailbox = def;
                renderUserInfo();
            }
        } catch (_) { /* non-fatal */ }
    }

    function clearLegacyDemoStorage() {
        // Wipe any legacy localStorage keys that older builds may have left
        // behind so we never display demo identities like "Md Bayezid Hossain".
        try {
            const banned = ["softrise_user", "demo_user", "user", "current_user", "softrise_demo"];
            banned.forEach((k) => localStorage.removeItem(k));
        } catch (_) { /* private mode etc. */ }
    }

    // ---------- Bootstrap ----------
    function setupRefreshButton() {
        dom.refreshBtn?.addEventListener("click", () => {
            if (getPageConfig()?.kind === "messages") {
                state.page = 1;
                loadMessages(true);
            }
        });
    }

    async function bootstrap() {
        // Always start from a blank, no-user state so the placeholder text in
        // the sidebar can't be confused with real data while /api/auth/me is
        // in flight.
        const pageConfig = getPageConfig();
        state.me = null;
        renderUserInfo();
        try {
            const me = await apiRequest("GET", "/api/auth/me");
            state.me = me;
            renderUserInfo();
            await ensureDefaultMailbox();
            await updateInboxCountBadge();
            if (pageConfig && pageConfig.kind === "messages") {
                state.currentFolder = pageConfig.folder || "starred";
                await loadMessages(true);
            } else if (pageConfig && pageConfig.kind === "mailboxes") {
                setHtml(dom.mailboxesList, loadingStateHtml(), "#mailboxes-list");
                await refreshMailboxes();
            } else if (pageConfig && pageConfig.kind === "settings") {
                await loadSettingsPage();
            }
        } catch (err) {
            if (err && err.status === 401) {
                // Not signed in — go to /login, never expose technical details.
                redirectToLogin();
                return;
            }
            console.error("[softrise] bootstrap failed:", err);
            // Only surface a toast for genuine API errors. JS exceptions like
            // "Cannot set properties of null" should never reach the user.
            if (err && err.status) {
                toast(err.message || "Could not load your inbox.", "error");
            }
        }
    }

    // When the user navigates back to the inbox using browser back / forward,
    // modern browsers may restore from the back-forward cache (bfcache) and
    // skip running scripts. Re-fetch the message list so read/unread state
    // (and the unread count badge) reflect anything that changed on the
    // dedicated message page.
    window.addEventListener("pageshow", (event) => {
        if (event.persisted && state.me && getPageConfig()?.kind === "messages") {
            loadMessages(true);
        }
    });

    function isAppPage() {
        return !!getPageConfig();
    }

    function initAppPage() {
        const pageConfig = getPageConfig();
        clearLegacyDemoStorage();
        cacheDom();
        setupThemeToggle();
        setupSidebar();
        setupMobileSearch();
        setupAuth();
        setupNav();
        setupRefreshButton();
        if (pageConfig && pageConfig.kind === "messages") {
            setupSearch();
            setupFilterChips();
            setupEmailListInteractions();
            setupDeleteModal();
            setupLoadMore();
            setupReadAllAffordance();
        }
        if (pageConfig && pageConfig.kind === "mailboxes") {
            setupMailboxesPage();
        }
        if (pageConfig && pageConfig.kind === "settings") {
            setupSettingsPage();
        }
        bootstrap();
    }

    // Catch any stray runtime exception that escapes our handlers and route
    // it to the console — never let raw "Cannot set properties of null …"
    // text reach the user-facing toast.
    window.addEventListener("error", (event) => {
        const message = String((event && (event.message || (event.error && event.error.message))) || "");
        if (isTechnicalNullInnerHtmlError(message)) {
            console.error("[softrise] uncaught error suppressed from toast:", event.error || event.message);
            return;
        }
        console.error("[softrise] uncaught error:", event.error || event.message);
    });
    window.addEventListener("unhandledrejection", (event) => {
        const reason = event && event.reason;
        const message = String((reason && reason.message) || reason || "");
        if (isTechnicalNullInnerHtmlError(message)) {
            console.error("[softrise] unhandled rejection suppressed from toast:", event.reason);
            return;
        }
        console.error("[softrise] unhandled rejection:", event.reason);
    });

    document.addEventListener("DOMContentLoaded", () => {
        if (!isAppPage()) {
            console.debug("[softrise] app.js: not an app shell page — skipping init");
            return;
        }
        initAppPage();
    });
})();
