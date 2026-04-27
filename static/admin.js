/* Admin panel for Soft Rice Mail. Requires user role='admin'. */

(() => {
    "use strict";

    const bodyPage = (document.body && document.body.dataset && document.body.dataset.page) || "";
    if (bodyPage && bodyPage !== "admin") {
        console.debug("[softrise] admin.js: not an admin page — skipping");
        return;
    }

    async function api(method, url, body) {
        const opts = {
            method,
            credentials: "include",
            cache: "no-store",
            headers: { Accept: "application/json" },
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
            const err = new Error((data && (data.error || data.detail)) || `HTTP ${res.status}`);
            err.status = res.status;
            throw err;
        }
        return data;
    }

    const $ = (s, r = document) => r.querySelector(s);
    const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

    function setHtml(el, html, name) {
        if (!el) {
            console.warn("[softrise] admin.js missing element:", name || "(unnamed)");
            return false;
        }
        el.innerHTML = html;
        return true;
    }

    function escapeHtml(t) {
        return String(t == null ? "" : t).replace(/[&<>"']/g, (c) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        })[c]);
    }
    function fmtTime(iso) {
        if (!iso) return "";
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return "";
        return d.toLocaleString();
    }
    function fmtBytes(n) {
        if (!n || n <= 0) return "0 B";
        const u = ["B", "KB", "MB", "GB"];
        const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
        return `${(n / Math.pow(1024, i)).toFixed(i ? 1 : 0)} ${u[i]}`;
    }

    function toast(message, kind = "info") {
        const stack = $("#admin-toast-stack");
        if (!stack) return;
        const el = document.createElement("div");
        const palette = {
            info: "bg-neutral-900 text-white dark:bg-neutral-800",
            success: "bg-primary text-white",
            error: "bg-error text-white",
        }[kind] || "bg-neutral-900 text-white";
        el.className = `pointer-events-auto rounded-full px-4 py-2 text-xs shadow-xl border border-black/5 dark:border-white/5 ${palette} opacity-0 translate-y-2 transition-all duration-200`;
        el.textContent = message;
        stack.appendChild(el);
        requestAnimationFrame(() => el.classList.remove("opacity-0", "translate-y-2"));
        setTimeout(() => {
            el.classList.add("opacity-0", "translate-y-2");
            setTimeout(() => el.remove(), 250);
        }, 3000);
    }

    // ---------- Theme toggle ----------
    function setupTheme() {
        const btn = $("#admin-theme-toggle");
        const icon = $("#admin-theme-icon");
        const update = () => {
            if (!icon) return;
            icon.className = document.documentElement.classList.contains("dark")
                ? "ph ph-sun text-lg md:text-xl"
                : "ph ph-moon text-lg md:text-xl";
        };
        update();
        btn?.addEventListener("click", () => {
            document.documentElement.classList.toggle("dark");
            localStorage.setItem(
                "color-theme",
                document.documentElement.classList.contains("dark") ? "dark" : "light",
            );
            update();
        });
    }

    // ---------- Tab nav ----------
    function setupTabs() {
        const activeCls = ["bg-primary/10", "text-primary", "dark:bg-neutral-800", "dark:text-neutral-200", "border-transparent", "font-medium"];
        const inactiveCls = ["bg-transparent", "text-neutral-500", "border-outline-variant/30", "font-normal"];
        $$(".admin-tab").forEach((b) => {
            b.addEventListener("click", () => switchTab(b.dataset.tab));
        });
        function switchTab(target) {
            $$(".admin-tab").forEach((b) => {
                b.classList.remove(...activeCls, ...inactiveCls);
                if (b.dataset.tab === target) {
                    b.classList.add(...activeCls);
                } else {
                    b.classList.add(...inactiveCls);
                }
            });
            $$(".admin-tab-pane").forEach((p) => p.classList.add("hidden"));
            $(`#tab-${target}`)?.classList.remove("hidden");
            loadTab(target);
        }
        window.adminSwitchTab = switchTab;
    }

    // ---------- Overview ----------
    async function loadOverview() {
        const grid = $("#stats-grid");
        if (!setHtml(grid, `<div class="col-span-full text-center text-neutral-400 py-6"><i class="ph ph-spinner-gap animate-spin text-2xl"></i></div>`, "#stats-grid")) return;
        try {
            const s = await api("GET", "/api/admin/stats");
            const cards = [
                { label: "Users", value: s.total_users, sub: `${s.active_users} active` },
                { label: "Mailboxes", value: s.total_mailboxes, sub: `${s.active_temp_mailboxes} temp · ${s.active_default_mailboxes} default` },
                { label: "Messages", value: s.total_messages, sub: `${s.messages_today} today` },
                { label: "Attachments", value: s.attachments_count, sub: fmtBytes(s.attachments_total_bytes) },
            ];
            setHtml(grid, cards.map((c) => `
                <div class="rounded-xl border border-outline-variant/20 dark:border-neutral-800 bg-white dark:bg-neutral-900 p-4">
                    <div class="text-[11px] uppercase tracking-wider text-neutral-500">${escapeHtml(c.label)}</div>
                    <div class="text-2xl font-serif font-semibold text-neutral-900 dark:text-white mt-1">${escapeHtml(c.value)}</div>
                    <div class="text-[12px] text-neutral-500 dark:text-neutral-400 mt-0.5">${escapeHtml(c.sub || "")}</div>
                </div>`).join(""), "#stats-grid");
        } catch (err) {
            setHtml(grid, `<div class="col-span-full text-center text-error py-6">${escapeHtml(err.message)}</div>`, "#stats-grid");
        }
    }

    // ---------- Users ----------
    const usersState = { page: 1, limit: 20, search: "" };
    async function loadUsers() {
        const tbody = $("#users-tbody");
        if (!setHtml(tbody, `<tr><td class="px-3 py-6 text-center text-neutral-400" colspan="9"><i class="ph ph-spinner-gap animate-spin"></i></td></tr>`, "#users-tbody")) return;
        try {
            const params = new URLSearchParams({ page: usersState.page, limit: usersState.limit });
            if (usersState.search) params.set("search", usersState.search);
            const data = await api("GET", `/api/admin/users?${params}`);
            setHtml(tbody, (data.items || []).map((u) => `
                <tr data-user="${escapeHtml(u.id)}">
                    <td class="px-3 py-2 text-neutral-800 dark:text-neutral-200">${escapeHtml(u.name || "-")}</td>
                    <td class="px-3 py-2">${escapeHtml(u.username)}</td>
                    <td class="px-3 py-2 text-neutral-500 dark:text-neutral-400">${escapeHtml(u.email || "-")}</td>
                    <td class="px-3 py-2 text-center">${u.mailbox_count}</td>
                    <td class="px-3 py-2 text-center">${u.message_count}</td>
                    <td class="px-3 py-2 text-center">
                        <select data-update="role" class="bg-stone-100 dark:bg-neutral-800 border border-outline-variant/20 dark:border-neutral-700 rounded-md text-[11px] py-0.5 px-2">
                            <option value="user" ${u.role === "user" ? "selected" : ""}>user</option>
                            <option value="admin" ${u.role === "admin" ? "selected" : ""}>admin</option>
                        </select>
                    </td>
                    <td class="px-3 py-2 text-center">
                        <input data-update="is_active" type="checkbox" ${u.is_active ? "checked" : ""} class="rounded text-primary"/>
                    </td>
                    <td class="px-3 py-2 text-center text-[12px] text-neutral-500">${escapeHtml(fmtTime(u.created_at))}</td>
                    <td class="px-3 py-2 text-right">
                        <button data-act="save" class="px-2 py-1 rounded-md text-[11px] bg-primary text-white hover:bg-[#4a4949]">Save</button>
                    </td>
                </tr>`).join(""), "#users-tbody");
            renderPagination("users-pagination", data, (p) => { usersState.page = p; loadUsers(); });
        } catch (err) {
            setHtml(tbody, `<tr><td class="px-3 py-6 text-center text-error" colspan="9">${escapeHtml(err.message)}</td></tr>`, "#users-tbody");
        }
    }

    function setupUsersHandlers() {
        const tbody = $("#users-tbody");
        const search = $("#users-search");
        if (!tbody || !search) return;
        tbody.addEventListener("click", async (e) => {
            const saveBtn = e.target.closest('[data-act="save"]');
            if (!saveBtn) return;
            const tr = saveBtn.closest("tr");
            const userId = tr.dataset.user;
            const roleEl = tr.querySelector('[data-update="role"]');
            const activeEl = tr.querySelector('[data-update="is_active"]');
            if (!roleEl || !activeEl) return;
            const role = roleEl.value;
            const is_active = activeEl.checked;
            try {
                await api("PATCH", `/api/admin/users/${userId}`, { role, is_active });
                toast("User updated", "success");
                loadUsers();
            } catch (err) {
                toast(err.message, "error");
            }
        });
        let t;
        search.addEventListener("input", (e) => {
            clearTimeout(t);
            t = setTimeout(() => {
                usersState.search = e.target.value.trim();
                usersState.page = 1;
                loadUsers();
            }, 300);
        });
    }

    // ---------- Mailboxes ----------
    const mailboxesState = { page: 1, limit: 20, search: "", status: "" };
    async function loadMailboxes() {
        const tbody = $("#mailboxes-tbody");
        if (!setHtml(tbody, `<tr><td class="px-3 py-6 text-center text-neutral-400" colspan="6"><i class="ph ph-spinner-gap animate-spin"></i></td></tr>`, "#mailboxes-tbody")) return;
        try {
            const params = new URLSearchParams({ page: mailboxesState.page, limit: mailboxesState.limit });
            if (mailboxesState.search) params.set("search", mailboxesState.search);
            if (mailboxesState.status) params.set("status", mailboxesState.status);
            const data = await api("GET", `/api/admin/mailboxes?${params}`);
            setHtml(tbody, (data.items || []).map((m) => `
                <tr data-mailbox="${escapeHtml(m.id)}" data-default="${m.is_default ? "1" : "0"}">
                    <td class="px-3 py-2 text-neutral-800 dark:text-neutral-200">${escapeHtml(m.email_address)}</td>
                    <td class="px-3 py-2 text-[12px] text-neutral-500">${escapeHtml(m.user_id)}</td>
                    <td class="px-3 py-2 text-center text-[11px] uppercase tracking-wider">${escapeHtml(m.type)}${m.is_default ? " · default" : ""}</td>
                    <td class="px-3 py-2 text-center">${m.deleted_at
                        ? '<span class="px-2 py-0.5 rounded-full bg-error-container/20 text-error text-[11px] uppercase tracking-wider">Deleted</span>'
                        : '<span class="px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300 text-[11px] uppercase tracking-wider">Active</span>'}</td>
                    <td class="px-3 py-2 text-center text-[12px] text-neutral-500">${escapeHtml(fmtTime(m.created_at))}</td>
                    <td class="px-3 py-2 text-right">
                        ${m.deleted_at
                            ? '<button data-act="restore" class="px-2 py-1 rounded-md text-[11px] bg-primary text-white hover:bg-[#4a4949]">Restore</button>'
                            : `<button data-act="delete" class="px-2 py-1 rounded-md text-[11px] bg-error-container/20 text-error hover:bg-error-container/40 dark:bg-red-900/20 dark:text-red-400" ${m.is_default ? 'data-default="1"' : ""}>Delete</button>`}
                    </td>
                </tr>`).join(""), "#mailboxes-tbody");
            renderPagination("mailboxes-pagination", data, (p) => { mailboxesState.page = p; loadMailboxes(); });
        } catch (err) {
            setHtml(tbody, `<tr><td class="px-3 py-6 text-center text-error" colspan="6">${escapeHtml(err.message)}</td></tr>`, "#mailboxes-tbody");
        }
    }

    function setupMailboxesHandlers() {
        const tbody = $("#mailboxes-tbody");
        const search = $("#mailboxes-search");
        const status = $("#mailboxes-status");
        if (!tbody || !search || !status) return;
        tbody.addEventListener("click", async (e) => {
            const btn = e.target.closest("button[data-act]");
            if (!btn) return;
            const tr = btn.closest("tr");
            const id = tr.dataset.mailbox;
            const action = btn.dataset.act;
            try {
                if (action === "delete") {
                    const isDefault = btn.dataset.default === "1" || tr.dataset.default === "1";
                    if (isDefault && !confirm("This is a DEFAULT mailbox. Delete anyway?")) return;
                    const url = isDefault
                        ? `/api/admin/mailboxes/${id}?confirm_default=true`
                        : `/api/admin/mailboxes/${id}`;
                    await api("DELETE", url);
                    toast("Deleted", "success");
                } else if (action === "restore") {
                    await api("POST", `/api/admin/mailboxes/${id}/restore`);
                    toast("Restored", "success");
                }
                loadMailboxes();
            } catch (err) {
                toast(err.message, "error");
            }
        });
        let t;
        search.addEventListener("input", (e) => {
            clearTimeout(t);
            t = setTimeout(() => {
                mailboxesState.search = e.target.value.trim();
                mailboxesState.page = 1;
                loadMailboxes();
            }, 300);
        });
        status.addEventListener("change", (e) => {
            mailboxesState.status = e.target.value;
            mailboxesState.page = 1;
            loadMailboxes();
        });
    }

    // ---------- Messages (admin view) ----------
    const messagesState = { page: 1, limit: 20, search: "", folder: "" };
    async function loadMessagesAdmin() {
        const tbody = $("#messages-tbody");
        if (!setHtml(tbody, `<tr><td class="px-3 py-6 text-center text-neutral-400" colspan="8"><i class="ph ph-spinner-gap animate-spin"></i></td></tr>`, "#messages-tbody")) return;
        try {
            const params = new URLSearchParams({ page: messagesState.page, limit: messagesState.limit });
            if (messagesState.search) params.set("search", messagesState.search);
            if (messagesState.folder) params.set("folder", messagesState.folder);
            const data = await api("GET", `/api/admin/messages?${params}`);
            setHtml(tbody, (data.items || []).map((m) => `
                <tr>
                    <td class="px-3 py-2 text-neutral-800 dark:text-neutral-200">${escapeHtml(m.from_email || "-")}</td>
                    <td class="px-3 py-2 text-neutral-800 dark:text-neutral-200">${escapeHtml(m.to_email || "-")}</td>
                    <td class="px-3 py-2">${escapeHtml(m.subject || "(no subject)")}</td>
                    <td class="px-3 py-2 text-center text-[11px] uppercase tracking-wider">${escapeHtml(m.is_deleted ? "trash" : m.folder)}</td>
                    <td class="px-3 py-2 text-center">${m.is_read ? '<i class="ph ph-check text-emerald-600"></i>' : '<i class="ph ph-circle text-neutral-300"></i>'}</td>
                    <td class="px-3 py-2 text-center">${m.is_starred ? '<i class="ph ph-fill ph-star text-tertiary"></i>' : '<i class="ph ph-star text-neutral-300"></i>'}</td>
                    <td class="px-3 py-2 text-center text-[12px] text-neutral-500">${escapeHtml(fmtTime(m.received_at))}</td>
                    <td class="px-3 py-2 text-right text-[12px] text-neutral-500">${escapeHtml(fmtBytes(m.size))}</td>
                </tr>`).join(""), "#messages-tbody");
            renderPagination("messages-pagination", data, (p) => { messagesState.page = p; loadMessagesAdmin(); });
        } catch (err) {
            setHtml(tbody, `<tr><td class="px-3 py-6 text-center text-error" colspan="8">${escapeHtml(err.message)}</td></tr>`, "#messages-tbody");
        }
    }

    function setupMessagesHandlers() {
        const search = $("#messages-search");
        const folder = $("#messages-folder");
        if (!search || !folder) return;
        let t;
        search.addEventListener("input", (e) => {
            clearTimeout(t);
            t = setTimeout(() => {
                messagesState.search = e.target.value.trim();
                messagesState.page = 1;
                loadMessagesAdmin();
            }, 300);
        });
        folder.addEventListener("change", (e) => {
            messagesState.folder = e.target.value;
            messagesState.page = 1;
            loadMessagesAdmin();
        });
    }

    // ---------- System Settings ----------
    const SETTING_DEFS = [
        { key: "temp_mailbox_limit", label: "Temp mailbox limit", type: "number", min: 1, max: 100 },
        { key: "allow_custom_temp_email", label: "Allow custom temp emails", type: "checkbox" },
        { key: "email_domain", label: "Email domain", type: "text" },
        { key: "max_attachment_size_mb", label: "Max attachment size (MB)", type: "number", min: 1, max: 100 },
        { key: "webhook_enabled", label: "Webhook enabled", type: "checkbox" },
    ];

    async function loadSettings() {
        const form = $("#admin-settings-form");
        if (!setHtml(form, `<div class="col-span-full text-center text-neutral-400 py-6"><i class="ph ph-spinner-gap animate-spin"></i></div>`, "#admin-settings-form")) return;
        try {
            const s = await api("GET", "/api/admin/settings");
            setHtml(form, SETTING_DEFS.map((d) => {
                const val = s[d.key];
                if (d.type === "checkbox") {
                    return `<label class="flex items-center gap-2 text-sm bg-stone-50 dark:bg-neutral-800/40 px-3 py-2 rounded-lg border border-outline-variant/20 dark:border-neutral-700">
                        <input type="checkbox" name="${d.key}" ${val ? "checked" : ""} class="rounded text-primary"/>
                        <span class="text-[13px]">${escapeHtml(d.label)}</span>
                    </label>`;
                }
                return `<label class="flex flex-col gap-1">
                    <span class="text-[12px] text-neutral-500">${escapeHtml(d.label)}</span>
                    <input type="${d.type}" name="${d.key}" value="${escapeHtml(val ?? "")}" ${d.min ? `min="${d.min}"` : ""} ${d.max ? `max="${d.max}"` : ""} class="bg-stone-100 dark:bg-neutral-800 border border-outline-variant/20 dark:border-neutral-700 rounded-lg py-2 px-3 text-sm focus:ring-0 focus:border-primary"/>
                </label>`;
            }).join("") + `<div class="col-span-full flex justify-end mt-2"><button type="submit" class="px-4 py-2 text-sm font-medium text-white bg-primary hover:bg-[#4a4949] rounded-lg shadow-sm">Save settings</button></div>`, "#admin-settings-form");
        } catch (err) {
            setHtml(form, `<div class="col-span-full text-error">${escapeHtml(err.message)}</div>`, "#admin-settings-form");
        }
    }

    function setupSettingsHandlers() {
        const form = $("#admin-settings-form");
        if (!form) return;
        form.addEventListener("submit", async (e) => {
            e.preventDefault();
            const fd = new FormData(e.target);
            const out = {};
            for (const d of SETTING_DEFS) {
                if (d.type === "checkbox") {
                    out[d.key] = fd.get(d.key) === "on";
                } else if (d.type === "number") {
                    out[d.key] = Number(fd.get(d.key));
                } else {
                    out[d.key] = (fd.get(d.key) || "").toString();
                }
            }
            try {
                await api("POST", "/api/admin/settings", { settings: out });
                const status = $("#admin-settings-status");
                if (!status) return;
                status.classList.remove("hidden");
                status.classList.remove("text-error");
                status.classList.add("text-primary");
                status.textContent = "Settings saved.";
                setTimeout(() => status.classList.add("hidden"), 3000);
                toast("Settings saved", "success");
            } catch (err) {
                toast(err.message, "error");
            }
        });
    }

    // ---------- Audit logs ----------
    const auditState = { page: 1, limit: 50 };
    async function loadAudit() {
        const tbody = $("#audit-tbody");
        if (!setHtml(tbody, `<tr><td class="px-3 py-6 text-center text-neutral-400" colspan="4"><i class="ph ph-spinner-gap animate-spin"></i></td></tr>`, "#audit-tbody")) return;
        try {
            const params = new URLSearchParams({ page: auditState.page, limit: auditState.limit });
            const data = await api("GET", `/api/admin/audit-logs?${params}`);
            setHtml(tbody, (data.items || []).map((r) => `
                <tr>
                    <td class="px-3 py-2 text-[12px] text-neutral-500 whitespace-nowrap">${escapeHtml(fmtTime(r.created_at))}</td>
                    <td class="px-3 py-2 font-medium">${escapeHtml(r.action)}</td>
                    <td class="px-3 py-2 text-[12px] text-neutral-500">${escapeHtml(r.user_id || "-")}</td>
                    <td class="px-3 py-2 text-[12px] text-neutral-500"><pre class="whitespace-pre-wrap break-words font-mono text-[11px] text-neutral-600 dark:text-neutral-400">${escapeHtml(JSON.stringify(r.metadata || {}, null, 0))}</pre></td>
                </tr>`).join(""), "#audit-tbody");
            renderPagination("audit-pagination", data, (p) => { auditState.page = p; loadAudit(); });
        } catch (err) {
            setHtml(tbody, `<tr><td class="px-3 py-6 text-center text-error" colspan="4">${escapeHtml(err.message)}</td></tr>`, "#audit-tbody");
        }
    }

    // ---------- Pagination ----------
    function renderPagination(containerId, data, onSelect) {
        const c = $("#" + containerId);
        if (!c) return;
        const { page, pages, total } = data;
        if (!total) {
            setHtml(c, `<span>No results.</span>`, `#${containerId}`);
            return;
        }
        if (!setHtml(c, `
            <button class="px-2 py-1 rounded border border-outline-variant/30 dark:border-neutral-700 ${page <= 1 ? "opacity-40 cursor-not-allowed" : "hover:bg-surface-container-low dark:hover:bg-neutral-800"}" ${page <= 1 ? "disabled" : ""}><i class="ph ph-arrow-left"></i></button>
            <span>Page ${page} of ${pages} · ${total} total</span>
            <button class="px-2 py-1 rounded border border-outline-variant/30 dark:border-neutral-700 ${page >= pages ? "opacity-40 cursor-not-allowed" : "hover:bg-surface-container-low dark:hover:bg-neutral-800"}" ${page >= pages ? "disabled" : ""}><i class="ph ph-arrow-right"></i></button>`, `#${containerId}`)) return;
        const [prev, , next] = c.children;
        if (!prev || !next) return;
        prev.addEventListener("click", () => onSelect(Math.max(1, page - 1)));
        next.addEventListener("click", () => onSelect(Math.min(pages, page + 1)));
    }

    // ---------- Loader dispatcher ----------
    function loadTab(target) {
        if (target === "overview") loadOverview();
        if (target === "users") loadUsers();
        if (target === "mailboxes") loadMailboxes();
        if (target === "messages") loadMessagesAdmin();
        if (target === "settings") loadSettings();
        if (target === "audit") loadAudit();
    }

    // ---------- Bootstrap ----------
    async function checkAdmin() {
        try {
            const me = await api("GET", "/api/auth/me");
            if (me.role !== "admin") {
                toast("Admin access required.", "error");
                setTimeout(() => (window.location.href = "/"), 1200);
                return false;
            }
            const adminUser = $("#admin-user");
            if (adminUser) adminUser.textContent = `${me.name || me.username} · ${me.email || ""}`;
            return true;
        } catch (err) {
            if (err.status === 401) {
                window.location.href = "/";
            } else {
                toast(err.message || "Authentication error", "error");
            }
            return false;
        }
    }

    document.addEventListener("DOMContentLoaded", async () => {
        setupTheme();
        setupTabs();
        setupUsersHandlers();
        setupMailboxesHandlers();
        setupMessagesHandlers();
        setupSettingsHandlers();
        const ok = await checkAdmin();
        if (ok) loadOverview();
    });
})();
