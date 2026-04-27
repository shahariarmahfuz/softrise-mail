# Soft Rice Mail

A production-ready Python email **receiving** service for the `@softrise.app`
domain. Inbound email is forwarded by a Cloudflare Email Worker to
`POST /webhook/email`, parsed, and stored in Neon PostgreSQL. Users register,
get an auto-provisioned default mailbox, can spin up to **10 active temporary
emails**, and read/search/star/archive/trash messages from a clean web UI that
re-uses the existing `index.html` design unchanged.

- Backend: **FastAPI** + **SQLAlchemy 2.x**
- Database: **Neon PostgreSQL** (`psycopg` driver)
- Auth: HTTP-only signed JWT cookie (bcrypt password hashing)
- Frontend: existing `index.html` + Tailwind CDN + Phosphor icons + a single
  `static/app.js` (no visual redesign)
- Admin panel: `/admin` (separate UI, same design tokens)
- Port: **5000**

---

## 1. Quick start (local)

```bash
# 1. Clone / cd into the project
cd mail/

# 2. Create a virtualenv and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure secrets (DATABASE_URL is the Neon URL)
cp .env.example .env
# edit .env and put real values for DATABASE_URL and APP_SECRET_KEY.
# WEBHOOK_SECRET is no longer required — /webhook/email is public and matches
# the Cloudflare Email Worker contract which sends Content-Type only.

# 4. Start the server on port 5000
python app.py
# or, equivalently:
# uvicorn app.main:app --host 0.0.0.0 --port 5000
```

Open <http://localhost:5000> in a browser. Unauthenticated visitors are
redirected to <http://localhost:5000/login>; new users can register at
<http://localhost:5000/register>.

> The first request triggers schema creation. All tables, indexes and the
> partial unique index on `mailboxes.email_address` (active mailboxes only) are
> created automatically.

### Requirements
- Python 3.11+ (developed on 3.12)
- A Neon PostgreSQL project (free tier is fine)

---

## 2. Configuration (`.env`)

| Variable              | Required | Description                                       |
| --------------------- | -------- | ------------------------------------------------- |
| `DATABASE_URL`        | yes      | Neon connection URL (`postgresql://...?sslmode=require&channel_binding=require`) |
| `APP_SECRET_KEY`      | yes      | 32+ char random string used to sign session JWTs  |
| `WEBHOOK_SECRET`      | no       | Reserved (currently unused). `/webhook/email` is public to match the Cloudflare Worker contract. |
| `APP_DOMAIN`          | no       | `softrise.app` (default)                          |
| `BASE_URL`            | no       | Public URL, e.g. `https://mail.softrise.app`. Drives `Secure` cookie flag. |
| `PORT`                | no       | `5000`                                            |
| `APP_ENV`             | no       | `development` (default) or `production` (hides stack traces, forces secure cookies) |
| `MAX_ATTACHMENT_SIZE_MB` | no    | Per-attachment cap saved to disk (default 10 MB) |
| `MAX_WEBHOOK_PAYLOAD_MB` | no    | Total webhook body size cap (default 25 MB)      |

`DATABASE_URL` is **server-side only** — never exposed to the frontend or any
API response.

---

## 3. Neon setup

1. Create a project on <https://console.neon.tech/>.
2. Go to **Connection details** and copy the pooled connection URL. Make sure
   it ends with `?sslmode=require&channel_binding=require`.
3. Paste it into `.env` as `DATABASE_URL`.
4. Restart the server — tables are auto-created on startup.

The schema (tables + indexes) is defined in <code>app/models.py</code>:

| Table                | Purpose                                              |
| -------------------- | ---------------------------------------------------- |
| `users`              | Auth + role (`user` / `admin`) + per-user settings   |
| `mailboxes`          | Each user's default + temporary `@softrise.app` boxes |
| `email_messages`     | Parsed inbound emails (raw + sanitized)              |
| `email_attachments`  | Attachment metadata; files saved under `storage/attachments/<msg_id>/` |
| `admin_settings`     | Adjustable settings (e.g. temp_mailbox_limit)        |
| `audit_logs`         | Webhook deliveries, mailbox/admin actions, login failures |

There is a partial unique index that enforces uniqueness on **active**
mailbox addresses only:

```sql
CREATE UNIQUE INDEX uq_mailboxes_active_email
    ON mailboxes (lower(email_address))
    WHERE deleted_at IS NULL;
```

---

## 4. Cloudflare Email Routing setup

1. In your Cloudflare dashboard, enable **Email Routing** for the
   `softrise.app` domain.
2. Create / paste the Email Worker:

```js
export default {
    async email(message, env, ctx) {
        try {
            const rawEmail = await new Response(message.raw).text();
            const headersObj = {};
            for (const [key, value] of message.headers) {
                headersObj[key] = value;
            }
            const payload = {
                from: message.from,
                to: message.to,
                size: message.rawSize,
                headers: headersObj,
                raw_email: rawEmail,
            };
            const response = await fetch("https://mail.softrise.app/webhook/email", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify(payload),
            });
            if (!response.ok) {
                throw new Error(`Backend rejected with status: ${response.status}`);
            }
            console.log(`Forwarded email from ${message.from} to backend.`);
        } catch (error) {
            console.error("Failed to process email:", error.message);
        }
    },
};
```

3. Add a routing rule for `*@softrise.app` → forward to this worker.

The Worker only sends `Content-Type: application/json`. The backend
`/webhook/email` endpoint is **public**: no `X-Webhook-Secret` is required
or validated. Successful delivery returns `200 {ok:true, stored:true,
message_id:"..."}`; an unknown recipient returns `202 {ok:true,
stored:false, reason:"mailbox_not_found"}`.

---

## 5. Promote a user to admin

```bash
source .venv/bin/activate

# Promote an existing user (or create one if missing):
python -m scripts.create_admin alice                              # promotes existing
python -m scripts.create_admin newadmin --password 's3cret!'      # creates new admin
```

The admin link appears in the sidebar (only) for `role='admin'` users and the
admin panel is served at <http://localhost:5000/admin>.

---

## 6. Endpoint reference

### Public

| Method | Path             | Purpose                                                |
| ------ | ---------------- | ------------------------------------------------------ |
| GET    | `/`              | Serves `templates/index.html` (SPA shell)              |
| GET    | `/login`         | Serves `templates/login.html`                          |
| GET    | `/register`      | Serves `templates/register.html`                       |
| GET    | `/admin`         | Serves admin panel (admin only)                        |
| GET    | `/health`        | Liveness + Neon DB check                               |
| POST   | `/webhook/email` | Cloudflare Worker webhook (public — no secret needed) |

### Authentication

| Method | Path                  | Notes                              |
| ------ | --------------------- | ---------------------------------- |
| POST   | `/api/auth/register`  | `{name, username, email, password}`. Auto-creates `username@softrise.app` mailbox. |
| POST   | `/api/auth/login`     | `{identifier, password}` (username **or** email) |
| POST   | `/api/auth/logout`    | Clears the session cookie         |
| GET    | `/api/auth/me`        | Returns current user + default mailbox |

### Mailboxes

| Method | Path                                   | Purpose |
| ------ | -------------------------------------- | ------- |
| GET    | `/api/mailboxes`                       | List default + temp mailboxes |
| GET    | `/api/mailboxes/check?local_part=foo`  | Availability check |
| POST   | `/api/mailboxes/temp`                  | Create temp mailbox (max 10 active) |
| DELETE | `/api/mailboxes/{id}`                  | Soft-delete temp mailbox |
| POST   | `/api/mailboxes/{id}/restore`          | Restore if not taken by another user |

### Messages (all enforce ownership)

| Method | Path                                | Purpose |
| ------ | ----------------------------------- | ------- |
| GET    | `/api/messages`                     | `?folder=inbox|archive|trash&starred=&read=&search=&mailbox_id=&page=&limit=` |
| GET    | `/api/messages/{id}`                | Full detail. `?mark_read=true` marks read |
| POST   | `/api/messages/{id}/read`           | `{is_read}` |
| POST   | `/api/messages/{id}/star`           | `{is_starred}` |
| POST   | `/api/messages/{id}/archive`        |  |
| POST   | `/api/messages/{id}/inbox`          |  |
| POST   | `/api/messages/{id}/trash`          |  |
| DELETE | `/api/messages/{id}`                | Trash if not in trash; `?force=true` to permanently delete |
| POST   | `/api/messages/read-all`            | `{folder?, mailbox_id?}` |
| POST   | `/api/messages/bulk`                | `{message_ids:[], action: read|unread|star|unstar|archive|trash|inbox|delete}` |

### User settings

| Method | Path             |
| ------ | ---------------- |
| GET    | `/api/settings`  |
| POST   | `/api/settings`  |

### Admin (role=admin only)

| Method | Path                                            |
| ------ | ----------------------------------------------- |
| GET    | `/api/admin/stats`                              |
| GET    | `/api/admin/users?page=&limit=&search=`         |
| PATCH  | `/api/admin/users/{user_id}`                    |
| GET    | `/api/admin/mailboxes?...`                      |
| POST   | `/api/admin/users/{user_id}/mailboxes`          |
| DELETE | `/api/admin/mailboxes/{id}?confirm_default=true`|
| POST   | `/api/admin/mailboxes/{id}/restore`             |
| GET    | `/api/admin/messages?...`                       |
| GET    | `/api/admin/settings`                           |
| POST   | `/api/admin/settings`                           |
| GET    | `/api/admin/audit-logs?page=&limit=&action=&user_id=` |

> Privacy note: admins **can** view inbound message metadata (and bodies if
> needed via the same `/api/messages/{id}` endpoint while operating as that
> user). Password hashes are never returned by any endpoint.

---

## 7. Sample `curl` smoke tests

`/webhook/email` is **public** — the Cloudflare Worker only sends
`Content-Type: application/json`, so no header is required.

```bash
BASE=http://localhost:5000

# 1. Health
curl -s $BASE/health | jq

# 2. Register and capture cookies
curl -s -c cookies.txt -b cookies.txt -X POST $BASE/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"name":"Demo User","username":"demo1","email":"demo1@example.com","password":"password123"}' | jq

# 3. Verify session
curl -s -b cookies.txt $BASE/api/auth/me | jq

# 4. Send a sample inbound email through the webhook (NO secret header)
RAW=$'From: friend@example.com\nTo: demo1@softrise.app\nSubject: Hello!\n\nHi, this is a test message.'
curl -s -X POST $BASE/webhook/email \
  -H 'Content-Type: application/json' \
  -d "{\"from\":\"friend@example.com\",\"to\":\"demo1@softrise.app\",\"size\":${#RAW},\"headers\":{},\"raw_email\":$(jq -Rsc <<<"$RAW")}" | jq

# 5. Look in inbox
curl -s -b cookies.txt "$BASE/api/messages?folder=inbox" | jq

# 6. Mark first message read / star / archive / trash / delete
ID=$(curl -s -b cookies.txt "$BASE/api/messages?folder=inbox" | jq -r '.items[0].id')
curl -s -b cookies.txt -X POST "$BASE/api/messages/$ID/read"   -H 'Content-Type: application/json' -d '{"is_read":true}'
curl -s -b cookies.txt -X POST "$BASE/api/messages/$ID/star"   -H 'Content-Type: application/json' -d '{"is_starred":true}'
curl -s -b cookies.txt -X POST "$BASE/api/messages/$ID/archive"
curl -s -b cookies.txt -X POST "$BASE/api/messages/$ID/trash"
curl -s -b cookies.txt -X DELETE "$BASE/api/messages/$ID?force=true"

# 7. Mailbox management
curl -s -b cookies.txt $BASE/api/mailboxes | jq
curl -s -b cookies.txt -X POST $BASE/api/mailboxes/temp \
  -H 'Content-Type: application/json' -d '{"local_part":"shopping"}' | jq
```

A ready-to-run version of those commands lives in
[`tests/curl_smoke.sh`](./tests/curl_smoke.sh).

---

## 8. Running the test suites

```bash
source .venv/bin/activate

# 1. End-to-end browser test (Playwright). Starts no extra processes; expects
#    the FastAPI server to already be running on http://127.0.0.1:5000.
python tests/e2e_smoke.py

# 2. Curl-based API smoke (also expects the server running on :5000).
bash tests/curl_smoke.sh
```

Test coverage / checklist:

- [x] Register user → default `@softrise.app` mailbox auto-created
- [x] Login + `/api/auth/me` returns profile + default mailbox
- [x] Create up to 10 temp mailboxes; 11th is rejected with the documented message
- [x] Delete temp mailbox; restore allowed only if address is not taken by another active mailbox
- [x] Webhook stores incoming email in correct user's inbox
- [x] Unknown recipient is logged in audit_logs but does not crash
- [x] Webhook is public (no `X-Webhook-Secret` required) and matches the Cloudflare Worker contract
- [x] Cross-user message access returns 404 (ownership enforced)
- [x] Read / read-all / star / archive / trash / delete (incl. permanent) / bulk
- [x] Search by subject/from/to/body
- [x] Normal user receives 403 on `/api/admin/*`

---

## 9. Security notes

- Passwords hashed with bcrypt via `passlib`.
- Sessions are HS256 JWTs in an `HttpOnly` `SameSite=Lax` cookie with a
  14-day `Max-Age`.
- The `Secure` flag is detected from the **actual request scheme** (honoring
  `X-Forwarded-Proto` set by upstream proxies), so:
  - `http://localhost:5000` and `http://<server-ip>:5000` → cookie has **no**
    `Secure` (works on plain HTTP during development).
  - `https://mail.softrise.app` (direct or behind a TLS-terminating proxy) →
    cookie is `Secure`.
  - In production set `APP_ENV=production` to additionally force `Secure` even
    if the proxy doesn't pass `X-Forwarded-Proto`.
- All SQL is generated via SQLAlchemy ORM/`text()` with bound parameters.
- Every per-user endpoint scopes its query by `user_id`.
- Email HTML is sanitized server-side (`bleach` + `CSSSanitizer`); `<script>`
  / `<style>` / `<iframe>` content is dropped before render.
- `/webhook/email` is intentionally public to match the Cloudflare Worker
  contract. Defence-in-depth comes from: payload size cap
  (`MAX_WEBHOOK_PAYLOAD_MB` -> 413), recipient must resolve to an existing
  mailbox to be persisted, and per-user data isolation downstream.
- Webhook payloads larger than `MAX_WEBHOOK_PAYLOAD_MB` are rejected with 413.
- `DATABASE_URL` and other secrets are never returned by any API.
- Audit logs capture: registrations, login failures, mailbox create/delete/
  restore, webhook successes & rejections, admin user/setting updates.

---

## 10. Production-style start

```bash
APP_ENV=production \
DATABASE_URL=... APP_SECRET_KEY=... \
uvicorn app.main:app --host 0.0.0.0 --port 5000 --workers 2 --proxy-headers
```

Place a TLS-terminating reverse proxy in front (Cloudflare / Nginx /
Traefik). The Cloudflare Worker only ever needs to reach
`https://mail.softrise.app/webhook/email`.

---

## 11. Project layout

```
mail/
├── app/
│   ├── main.py            # FastAPI app, lifespan, error handlers
│   ├── config.py          # env loader + Settings
│   ├── database.py        # engine + session + Base
│   ├── models.py          # SQLAlchemy ORM models + indexes
│   ├── schemas.py         # Pydantic request/response models
│   ├── auth.py            # bcrypt + JWT + auto-mailbox provisioning
│   ├── deps.py            # FastAPI dependencies (current_user, current_admin)
│   ├── audit.py           # audit-log helper
│   ├── email_parser.py    # MIME / multipart parsing
│   ├── utils.py           # localpart slugify, HTML sanitization
│   └── routes/            # auth, mailboxes, messages, settings, webhook, admin
├── scripts/
│   └── create_admin.py    # `python -m scripts.create_admin <username>`
├── static/
│   ├── app.js             # main frontend behaviour (replaces inline JS)
│   └── admin.js           # admin panel
├── storage/attachments/   # parsed attachments are stored here
├── tests/
│   ├── e2e_smoke.py       # Playwright headless test
│   └── curl_smoke.sh      # curl-based smoke
├── prompts/               # original specification
├── index.html             # untouched visual design (only minimal IDs added)
├── admin.html             # admin panel page
├── app.py                 # `python app.py` entry point
├── requirements.txt
├── .env.example
└── README.md
```
