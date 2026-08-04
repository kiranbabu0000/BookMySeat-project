# BookMySeat — End-to-End Audit Report

**Date:** 03 Aug 2026
**Scope:** Complete audit + bug-fix pass of the BookMySeat Django project against the original requirements (Movie Management, Smart Seat Reservation, Payment Workflow, Admin Analytics + Security, Database, Performance, UI/UX, Testing).
**Environment:** Python 3.13.9, Django 6.0.7, SQLite, Windows / PowerShell. Base dir `C:\Users\kiran babu\Downloads\BookMySeat-main\BookMySeat-main`.
**Test result:** **174 / 174 tests pass** (movies=57, payments=41, admin_panel=33, analytics=43). `python manage.py check` → 0 issues.

---

## 1. Executive Summary

The application is feature-complete and functionally sound. This audit verified every requirement area end to end, fixed **8 issues** (4 security/correctness, 4 polish), and confirmed the whole suite stays green. No existing architecture was rewritten — all fixes extend existing files and follow the patterns already in the codebase.

New bugs fixed this session (details in §6):

| # | Severity | Issue | Fix |
|---|----------|-------|-----|
| 1 | HIGH | Analytics dashboard XSS — `analytics_json\|safe` allows `</script>` breakout via user-controlled strings (usernames, emails, movie names) | Replaced raw `<script>...\|safe</script>` with the Django `json_script` filter |
| 2 | HIGH | `report_review` was a state-changing GET with no CSRF and no self-report guard (CSRF/forgery vector) | Now `@require_POST` with `{% csrf_token %}` form; server rejects reporting your own review |
| 3 | HIGH | Demo checkout "Pay Securely" always failed — `verify()` never sent `demo: true`, so the real signature path ran and rejected the demo signature | `verify()` now posts `demo: demoCheckout` |
| 4 | MEDIUM | Reservation countdown computed from the client clock; server `remaining` field ignored (skewed clocks show wrong hold time) | Countdown anchored to server time via a one-time offset (`syncClockOffset`), seat-selection + payment pages |
| 5 | LOW | `register.html` bypassed autoescape with `{{ field.help_text\|safe }}` | Removed `\|safe` |
| 6 | LOW | `youtube_embed` passed non-YouTube URLs straight into an `<iframe src>`; `TrailerForm.clean_url` only checked a substring | Filter now returns `''` for non-YouTube; template guards the iframe; form validates the hostname via `urlparse` |
| 7 | LOW | Dark-mode OS-preference listener was dead — `applyTheme(getPreferredTheme())` always wrote `localStorage`, so the `!localStorage.getItem(...)` guard could never fire | Load now applies without persisting; system change only applies while the user has no explicit choice; Safari `addListener` fallback |
| 8 | LOW | `main.js` form-loading spinner stored the original HTML but never restored it (button stayed disabled on back/forward) | Restore via `pageshow` |

No new models, URLs, templates, forms, or views were created. No migration was needed for any fix.

---

## 2. Feature Verification Matrix

### 2.1 Movie Management ✅
- **Movie CRUD (admin):** list / create / edit / delete, soft-delete (`is_deleted`), archive/hide/toggle status (draft, coming_soon, now_showing, archived, hidden), restore — all wired in `admin_panel/views.py`, permission-gated.
- **Fields:** `rating` (required), `cast`, `duration`, `release_date`, `certificate`, `trailer_url`, M2M genres/languages, images (`MovieImage`), trailers (`Trailer`), ordering `-booked_at,-id`.
- **Public listing:** search, genre/language filters, trending/recent/released sections, similar movies, pagination.
- **Reviews:** authenticated users with a completed (past) booking get a **Verified Viewer badge** (`movie_detail.html:411-415`, backed by `booking=` assignment in `submit_review`); duplicate review prevented (one review per user/movie, updated in place); unverified users are blocked from creating a review; admin review approval/hide/report handling present. User can **edit** their own review (no user-facing delete — admin-only, by design).
- **Trailers:** YouTube embed filter (`youtube_embed`), featured flag, admin validation — hardened this session (see §6).

### 2.2 Smart Seat Reservation ✅
- **Core locking:** `movies/services.py` uses `select_for_update()` row locks + a unique `ReservedSeat.seat` OneToOne DB backstop; `create_reservation` / `modify_reservation` / `release_reservation` / `confirm_booking` all run in `transaction.atomic()`.
- **Hold window:** `RESERVATION_HOLD_SECONDS = 120`, expiry jobs (`expire_stale_for_show`, `release_expired_reservations`, admin cleanup endpoint).
- **Concurrency:** `test_single_seat_race` proves two users cannot reserve the same seat; seat map is live-polled (5 s) with ETag/304 (`seat_revision`); server rejects stale selections.
- **Ownership:** every reservation/token endpoint enforces `reservation.user_id == user.id` (`services.py:388,589,619`, `payments.py:40`, `views.py:305,444,509,530,548`).
- **Countdown timer:** now server-time anchored (fixed this session), releases seats and returns to the seat map at 0.

### 2.3 Payment Workflow ✅
- **Orchestration:** `movies/payments.py` — order start (`start_checkout`), verify (`verify_and_confirm`), failure (`record_failure`), webhook (`handle_webhook`). Fully **idempotent** (re-verify returns existing booking).
- **Server-side pricing:** the client never supplies an amount; `start_checkout` creates the order, `verify_and_confirm` recomputes `total` from current prices/GST/coupon and re-checks `total == locked.amount` and the gateway amount.
- **Coupon:** validated server-side, bound at order start, recomputed on verify.
- **Gateway:** thin mockable Razorpay wrapper (`movies/gateway.py`); **demo mode** auto-enabled when keys are absent (`RAZORPAY_DEMO_MODE` defaults True) — and the demo checkout path itself now works (bug #3).
- **Webhook:** HMAC + entity checks, `@csrf_exempt`, never trusts body amount.
- **Refunds:** admin refund flow records `PaymentTransaction` rows; analytics track refund rate/amount.

### 2.4 Admin Analytics + Security ✅
- **Auth model:** custom admin login/session (`AdminIdentityMiddleware`, `AdminSessionMixin`, `admin_session_required`, `permission_required('module', 'can_view')`). Anon → `/admin-login/`; staff without active `AdminProfile` → `/admin-login/`; staff lacking module permission → `/dashboard/`; never a 500.
- **`AdminIdentityMiddleware`** maps the `admin_user_id` identity onto `request.user` only for admin URLs, so admin and customer sessions never bleed into each other.
- **Analytics:** 10 areas (overview, revenue, bookings, occupancy, movies, theaters, peak, payments, refunds, users) with preset + custom ranges, period-over-period deltas, Chart.js charts + peak heatmap, and **CSV / XLSX (pure-stdlib) / PDF (print HTML)** exports. All data queries use ORM aggregates over the indexed datetime columns.
- **Security fixes this session:** analytics JSON XSS (bug #1), report-review CSRF/GET (bug #2) — see §6.

### 2.5 Database ✅
- SQLite; migrations applied through `movies/0014` and `admin_panel/0008`.
- Composite indexes added for the reporting hot paths (see §7): booking `booked_at/status/range`, payment `paid_at/status`, transactions `created_at/status`.

### 2.6 Performance ✅
- Seat polling is bandwidth-light via ETag/304 (`seat_revision`).
- Analytics services are written against 100k+ rows: `TruncDate/TruncMonth/ExtractYear/ExtractHour/ExtractWeekDay` bucketing, filtered `Count`/`Sum` with `Q`, `select_related` on recent lists, `.values().annotate()` distributions — no Python-side row iteration over the full dataset.
- N+1 audits: recent bookings, refund details, and top-user/top-theater lists all use `select_related`/`annotate`. See existing `PERFORMANCE_REPORT.md`.

### 2.7 UI/UX ✅
- Responsive Bootstrap 5 UI, dark/light theme toggle (now respecting OS preference — bug #7), loading spinners, empty states, flash messages, wishlist toggle, notifications drawer, seat map with tier colors, live timer urgency styling, print-optimized PDF template.
- Accessibility touchpoints: `aria-label` on icon buttons/toggles, `alt` on posters.

### 2.8 Testing ✅
- 174 tests across 4 modules (see §9); deterministic seat race, idempotent payment verify, admin auth matrix, analytics payload/render regressions, CSV/XLSX/PDF exports, `get_item` list support, overview `/analytics/` route.
- Prior known constraint: tests must run with `SERVER_NAME='127.0.0.1'` where they hit ALLOWED_HOSTS.

---

## 3. Files Modified / Created

### 3.1 Changed in this audit session
| File | Change |
|------|--------|
| `templates/admin/analytics/analytics_base.html` | JSON data now via `json_script` (XSS fix) |
| `movies/views.py` | `report_review` → `@require_POST` + self-report guard; `payment_page` passes `remaining` to template |
| `templates/movies/movie_detail.html` | Report button → CSRF POST form; trailer iframe guarded by `youtube_embed` result |
| `templates/movies/payment.html` | `verify()` sends `demo`; countdown anchored to server time |
| `static/js/seat-selection.js` | `syncClockOffset()`; countdown uses server-time offset |
| `static/js/theme.js` | Non-persisting load + working OS-preference listener + Safari fallback |
| `static/js/main.js` | Form-loading buttons restored on `pageshow` |
| `templates/users/register.html` | Removed unnecessary `\|safe` |
| `movies/templatetags/movie_extras.py` | `youtube_embed` returns `''` for non-YouTube URLs |
| `admin_panel/forms.py` | `TrailerForm.clean_url` validates hostname via `urlparse` |

### 3.2 Created (this session)
| File | Purpose |
|------|---------|
| `AUDIT_REPORT.md` | This report (final deliverable) |

### 3.3 Pre-existing project delta (from prior work, untracked/modified in git)
- **New modules:** `movies/payments.py`, `movies/gateway.py`, `movies/tests_payments.py`, `admin_panel/analytics/` (`services.py`, `views.py`, `tests.py`, `urls.py`), `admin_panel/services.py`, `admin_panel/context_processors.py`.
- **New templates/static:** `templates/admin/analytics/*`, `templates/admin/payments/*`, `templates/users/wishlist.html`, `templates/users/notifications.html`, `static/admin/js/analytics.js`, `static/admin/css/analytics.css`, `static/admin/css/report.css`.
- **Extended:** `movies/models.py`, `admin_panel/models.py`, `admin_panel/views.py`, `movies/views.py`, `users/views.py`, all three `urls.py`, `bookmyseat/settings.py`, `db.sqlite3`.
- **No files were deleted.**

---

## 4. Migrations

All applied and up to date (`showmigrations` clean). The reporting/perf migrations are:
- `movies/0013` — composite indexes on `Booking` (`user,booked_at` etc.) and datetime columns.
- `movies/0014_alter_booking_options` — ordering `-booked_at,-id`.
- `admin_panel/0008` — `Booking booked_at/status/range` composite indexes + `Payment` (`paid_at`, `status`) and `PaymentTransaction` (`created_at`, `status`) indexes.

**No new migration was required for any audit fix** (all changes were template/JS/form/view logic).

---

## 5. Security Posture

### Already strong (verified)
- Custom admin session keys + `_verify_admin_session` (auth, superuser OR active `AdminProfile`, session key match, `is_active`); identity middleware prevents admin/customer session bleed.
- Owner checks on every reservation / payment / booking / ticket endpoint (IDOR closed).
- Server-authoritative pricing: client never sends an amount; totals recomputed and re-verified at confirm.
- Webhook signature verified; `@csrf_exempt` only on the webhook.
- CSRF tokens attached to all AJAX `fetch` calls (`X-CSRFToken`); login/register forms CSRF-protected.
- `SESSION_COOKIE_HTTPONLY/SAMESITE=Lax`, `CSRF_COOKIE_HTTPONLY/SAMESITE=Lax`, `XFrameOptionsMiddleware`, `SecurityMiddleware` present.
- Review comments, movie names, booking refs, notification text, search query all rendered with Django autoescape; `seat-selection.js` and `analytics.js` use `textContent`/`escapeHtml()` (no `innerHTML` injection of user data).

### Fixed this session
See §1 (bugs #1 and #2 are the security items) and §6 for details.

### Remaining hardening recommendations (non-blocking)
| Area | Recommendation |
|------|----------------|
| `settings.py` | `DEBUG` defaults **True** and `SECRET_KEY` has a published dev fallback; `DJANGO_DEBUG=False` + a unique `DJANGO_SECRET_KEY` must be set in production/Vercel env vars. |
| Cookies | `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` default False — set True under HTTPS. |
| `ALLOWED_HOSTS` | Default includes `.vercel.app` — trim to the real host in production. |
| Report button | Already POST+CSRF; consider an audit log entry for reports (currently `is_reported` flag only). |

---

## 6. Bugs Found & Fixed (detail)

1. **Analytics XSS (`templates/admin/analytics/analytics_base.html:23`)** — `{{ analytics_json|safe }}` inside a `<script type="application/json">` block. The payload (`json.dumps` of charts/tables incl. usernames, emails, movie names) was not HTML-escaped, so a username containing `</script><script>…` would execute in the admin browser. **Fix:** `{{ analytics_json|json_script:"analytics-data" }}` — the same pattern already used by `seat_selection.html` and `movie_removal.html`; `analytics.js` reads `textContent` + `JSON.parse`, which still works.

2. **`report_review` CSRF/forgery (`movies/views.py`)** — `@login_required` but a **GET** that set `is_reported=True`; Django CSRF only protects unsafe methods, and `confirm()` was client-only, so a logged-in user could be tricked (`<img src=…/report/>`) into reporting any review. **Fix:** `@require_POST`, template converted to a `{% csrf_token %}` form, and server-side `review.user_id != request.user.id` guard added.

3. **Demo checkout "Pay Securely" broken (`templates/movies/payment.html`)** — `startCheckout()` correctly detected `checkout.demo` and called `verify(order_id, 'pay_DEMO_pending', demo_signature)`, but `verify()` never sent `demo`, so `payment_verify_api` defaulted to the real path and the demo signature was rejected → "Payment signature verification failed." every time. **Fix:** `verify()` payload now includes `demo: demoCheckout`.

4. **Client-clock countdown (`seat-selection.js`, `payment.html`)** — countdown used `Date.now()` against the server's absolute `expires_at` while the server already sent `remaining`. A skewed client clock overstated/understated the hold. **Fix:** one-time offset `serverNow = expires_at − remaining`, applied on reservation apply/sync and page load; `payment_page` view now also passes `remaining` to the template.

5. **`register.html` autoescape bypass** — `{{ field.help_text|safe }}` removed (help text is static; `|safe` is a latent injection pattern).

6. **Trailer URL passthrough** — `youtube_embed` returned non-YouTube URLs unchanged into an `<iframe src>`, and `TrailerForm.clean_url` accepted `youtube.com.evil.example` (substring check). **Fix:** filter returns `''` for anything that doesn't match a real YouTube video pattern; template renders the iframe only when the embed URL is non-empty; form now validates the URL **hostname** (`youtube.com`, `www.youtube.com`, `m.youtube.com`, `youtu.be`).

7. **Dead dark-mode listener (`theme.js`)** — `applyTheme(getPreferredTheme())` on load always persisted to `localStorage`, so `if (!localStorage.getItem(STORAGE_KEY))` could never be true and OS theme changes were ignored. **Fix:** load applies without persisting; the OS-change listener only overrides while the user has no explicit choice; legacy `addListener` fallback for old Safari.

8. **Form-loading button never restored (`main.js`)** — `btn.dataset.originalHtml` was saved but never used, leaving submit buttons disabled with a spinner after back/forward navigation. **Fix:** `pageshow` restores the saved HTML and re-enables the button.

---

## 7. ORM Optimizations & Indexes

- **Seat reservation:** `select_for_update()` row locks; unique OneToOne backstop; `seat_revision` + ETag/304 avoids re-serializing the seat map; `expire_stale_for_show` prunes stale holds before reads.
- **Analytics:** all series/distributions use SQL aggregation (`TruncDate`, `TruncMonth`, `ExtractYear/Hour/WeekDay`, filtered `Count`/`Sum` with `Q`, `Coalesce`), keeping query count constant vs. dataset size; recent/recent-bookings and refund/top lists use `select_related`; exports reuse the same pre-aggregated dicts (no re-query).
- **Indexes (applied):**
  - `Booking`: `(user, booked_at)`, `(booked_at, status)`, range on `booked_at` — serve profile history + every analytics range filter.
  - `Payment`: `paid_at`, `status`; `PaymentTransaction`: `created_at`, `status` — serve revenue/refund aggregation.
  - `Reservation`: `(user, status)` — serves active-reservation lookups on seat page and payment.
- **Avoided:** no `SELECT *` over whole tables in hot paths; `values(...).annotate(...)` distributions ordered by `-count` server-side.

---

## 8. UI/UX Audit Notes (verified, non-blocking)

- **Verified Viewer badge** exists next to reviews tied to a completed booking.
- Search suggestions built with `createElement`/`textContent`; empty states present for movies, reviews, wishlist, notifications, analytics tables.
- The `data-loading` form helper is currently unused by any template (kept for future use, now correct).
- `admin.js` polls `'/notifications/unread-count/'` via a hardcoded absolute URL — minor; fine for single-deploy.
- Razorpay checkout SDK loads whenever not in demo mode even if keys are empty; the in-page `typeof Razorpay !== 'function'` guard handles load failure gracefully.

---

## 9. Testing Checklist

Command: `python manage.py test movies.tests movies.tests_payments admin_panel.tests admin_panel.analytics.tests`

| Suite | Tests | Result |
|-------|------:|--------|
| `movies.tests` | 57 | ✅ PASS |
| `movies.tests_payments` | 41 | ✅ PASS |
| `admin_panel.tests` | 33 | ✅ PASS |
| `admin_panel.analytics.tests` | 43 | ✅ PASS |
| **Total** | **174** | ✅ PASS |

`python manage.py check` → **0 issues**.

Notable coverage: single-seat reservation race, idempotent demo verify/confirm, admin auth matrix (anon/staff/no-permission/superuser → correct 302/200, never 500), analytics revenue/occupancy/theater range filtering, heatmap `get_item` list regression, CSV/XLSX/PDF exports, `format=csv` URL escaping, overview route at `/analytics/`.

Note: the full suite takes ~5–6 minutes (mostly the concurrency and payment tests) — run each module separately if you need incremental feedback.

---

## 10. Limitations & Production-Readiness

**Known limitations (acceptable for the scope):**
- SQLite (single-writer); move to PostgreSQL for true 100k+ concurrent deployments. The analytics queries are already portable to Postgres.
- `EMAIL_BACKEND=console` — wire a real SMTP provider for production confirmation emails.
- Razorpay is the only gateway; demo mode is the offline fallback.
- User-facing review deletion intentionally admin-only.

**Production-readiness: confirm** once these env vars are set:
- `DJANGO_DEBUG=False`, a real `DJANGO_SECRET_KEY`, trimmed `DJANGO_ALLOWED_HOSTS`.
- `SESSION_COOKIE_SECURE=True`, `CSRF_COOKIE_SECURE=True` behind HTTPS.
- `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` / `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_DEMO_MODE=False`.
- Real `EMAIL_*` SMTP settings.

**Verdict:** the application is **feature-complete and production-ready** with the configuration above. All 174 tests pass, `manage.py check` is clean, security posture is sound, and the 8 issues found this session are fixed with no architectural change.
