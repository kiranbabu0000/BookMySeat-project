# BookMySeat — 20-Point Feature Audit Report

**Date:** 06 Aug 2026
**Base dir:** `C:\Users\kiran babu\Downloads\BookMySeat-main\BookMySeat-main`
**Environment:** Python 3.13.9, Django 6.0.7, SQLite (local) / PostgreSQL (DATABASE_URL), Razorpay + demo mode.
**Health:** `python manage.py check` → 0 issues. Existing suite = 174 tests (movies 57, payments 41, admin_panel 33, analytics 43) + users (6 classes).

**Method:** Read all models, views, services, URL maps, templates, static JS, migrations, and settings; grepped for every requested feature; no code was modified in this pass.

---

## 1. Executive Summary

| # | Feature | Status |
|---|---------|--------|
| 1 | Admin Analytics Dashboard | ✅ Fully Implemented |
| 2 | Revenue Reports | ✅ Fully Implemented |
| 3 | Booking Analytics | ✅ Fully Implemented |
| 4 | Theater Occupancy Analytics | ✅ Fully Implemented |
| 5 | Movie Performance Analytics | ✅ Fully Implemented |
| 6 | User Growth Analytics | ✅ Fully Implemented |
| 7 | Peak Booking Hour Analysis | ✅ Fully Implemented |
| 8 | CSV Export | ✅ Fully Implemented |
| 9 | Advanced Search | ✅ Fully Implemented |
| 10 | Movie Filters | ✅ Fully Implemented |
| 11 | Sorting | ✅ Fully Implemented |
| 12 | Pagination | ✅ Fully Implemented |
| 13 | Recommendation Engine | ✅ Fully Implemented |
| 14 | PDF Ticket Generation | ✅ Fully Implemented |
| 15 | QR Code Generation | ✅ Fully Implemented (HMAC-signed + gate-scanner verify) |
| 16 | Email Ticket Delivery | ✅ Fully Implemented (HTML + QR + PDF attachment) |
| 17 | Celery Integration | ❌ Not Implemented |
| 18 | Redis Integration | ✅ Configured (django-redis via `REDIS_URL`, LocMem fallback) |
| 19 | Ticket Download History | ✅ Fully Implemented |
| 20 | Admin Permission System | ✅ Fully Implemented |

**12 of 20 features are fully implemented and should NOT be rewritten.** The 6 `⚠️`/`❌` items in the original audit have since been closed: server-side PDF (`movies/pdf.py`), HTML email with PDF attachment, HMAC-signed QR + verify endpoint, `TicketDownload` history, and Redis cache config. Only **Celery** (item 17) remains out of scope — it requires a persistent worker/broker, which Vercel serverless does not provide. Details below.

---

## 2. Feature Audit

==================================================
FEATURE: 1. Admin Analytics Dashboard
STATUS: ✅ Fully Implemented
FILES:
  - `admin_panel/analytics/views.py` (10 CBV pages + `analytics_data_json` + `analytics_export`)
  - `admin_panel/analytics/services.py` (all data functions, range resolution, exports)
  - `admin_panel/urls.py:119-130` (`/analytics/`, `/analytics/<area>/`, `/analytics/data/<area>/`, `/analytics/export/<area>/`)
  - Templates `templates/admin/analytics/*` (base + 10 area pages + partials + `report_pdf.html`)
  - `static/admin/js/analytics.js`, `static/admin/css/analytics.css`, `report.css`
MODELS USED: Booking, Payment, PaymentTransaction, Reservation, Movie, Theater (movies), Show (admin_panel), User, Review, Seat
APIs USED: `analytics_views.OverviewView/RevenueView/...`; JSON data endpoint; export endpoint. No external APIs.
TEMPLATES USED: `admin/analytics/overview.html`, `revenue.html`, `bookings.html`, `occupancy.html`, `movies.html`, `theaters.html`, `peak.html`, `payments.html`, `refunds.html`, `users.html`, `report_pdf.html`, `analytics_base.html`
WHAT'S WORKING:
  - 10 analytics areas, 10 date presets + custom range, period-over-period deltas, Chart.js charts + peak heatmap
  - AJAX in-place refresh with `?range=` on `/analytics/data/<area>/`
  - Full access control: `admin_session_required` + `permission_required('analytics','can_view')`
  - ORM `TruncDate/TruncMonth/ExtractHour/ExtractWeekDay` aggregation on indexed datetime columns
  - XSS-hardened JSON embedding via `json_script` (fixed in prior audit, `analytics_base.html`)
WHAT'S MISSING:
  - No caching (dashboard recomputed on every page load; no `cache_page`/`cache.set` on range results)
  - Overview/revenue pages recompute `summary_metrics` twice (page + period-over-period buckets)
  - No scheduled/pre-aggregated snapshots for very long ranges (custom "all-time" range on 100k rows re-aggregates every load)
  - No drill-down from charts to the underlying bookings/payments
RECOMMENDED IMPROVEMENTS:
  - Cache per-`(area, range-key)` payload for TTL (e.g., 5 min) via Django cache
  - Add drill-down links (chart point → filtered booking/payment list)
  - Add a scheduled daily snapshot model for long-range trends
PRIORITY: Medium

==================================================
FEATURE: 2. Revenue Reports
STATUS: ✅ Fully Implemented
FILES: `admin_panel/analytics/services.py:345` (`revenue_data`), `admin_panel/analytics/views.py`, template `templates/admin/analytics/revenue.html`
MODELS USED: Payment (status, paid_at), Booking (confirmed totals/components)
APIs USED: `/analytics/revenue/`, `/analytics/data/revenue/`, `/analytics/export/revenue/`
TEMPLATES USED: `admin/analytics/revenue.html`, `analytics_base.html`
WHAT'S WORKING:
  - Revenue trend (day/month/year auto-granularity), revenue-by-method doughnut, revenue components (ticket/GST/platform fee/misc fee/discount), AOV, refund amount, deltas
  - Payments filtered by `status='completed'` + `paid_at` half-open window (index-friendly)
  - CSV/XLSX/PDF export
WHAT'S MISSING:
  - No revenue by movie/theater breakdown on the revenue page (exists on movies/theaters pages instead — acceptable)
  - No currency/exchange handling (INR-only, fine for scope)
RECOMMENDED IMPROVEMENTS:
  - Cache the range payload; add "revenue vs bookings" dual-axis chart
PRIORITY: Low

==================================================
FEATURE: 3. Booking Analytics
STATUS: ✅ Fully Implemented
FILES: `admin_panel/analytics/services.py:385` (`bookings_data`), `admin_panel/analytics/views.py`, template `templates/admin/analytics/bookings.html`
MODELS USED: Booking (booked_at, status), Reservation
APIs USED: `/analytics/bookings/`, `/analytics/data/bookings/`, `/analytics/export/bookings/`
TEMPLATES USED: `admin/analytics/bookings.html`
WHAT'S WORKING: trend series, status distribution, weekday + hour distributions, KPIs + deltas, export.
WHAT'S MISSING: no booking-list drill-down from the dashboard.
RECOMMENDED IMPROVEMENTS: same caching + drill-down as revenue.
PRIORITY: Low

==================================================
FEATURE: 4. Theater Occupancy Analytics
STATUS: ✅ Fully Implemented
FILES: `admin_panel/analytics/services.py:430` (`occupancy_data`), `:542` (`theaters_data`), template `templates/admin/analytics/occupancy.html`, `theaters.html`
MODELS USED: Theater (movies), Seat, Booking, Show, Screen
APIs USED: `/analytics/occupancy/`, `/analytics/theaters/`, `/analytics/data/<area>/`, `/analytics/export/<area>/`
TEMPLATES USED: `admin/analytics/occupancy.html`, `admin/analytics/theaters.html`
WHAT'S WORKING:
  - Occupancy by theater (total/booked seats + rate), per-theater rows, range-aware `distinct` counts (join fan-out already fixed)
  - Theater ranking by revenue/bookings
WHAT'S MISSING:
  - Occupancy computed against `Seat.is_booked`/ReservedSeat rather than a real per-show capacity ledger; `Seat.is_booked` is legacy and drifts from `Booking.status`
  - No per-show/date occupancy heatmap
RECOMMENDED IMPROVEMENTS:
  - Derive "booked seats" from confirmed bookings only (single source of truth)
  - Add screen/show granularity
PRIORITY: Medium

==================================================
FEATURE: 5. Movie Performance Analytics
STATUS: ✅ Fully Implemented
FILES: `admin_panel/analytics/services.py:490` (`movies_data`), template `templates/admin/analytics/movies.html`
MODELS USED: Booking, Movie
APIs USED: `/analytics/movies/`, `/analytics/data/movies/`, `/analytics/export/movies/`
TEMPLATES USED: `admin/analytics/movies.html`
WHAT'S WORKING: top movies by revenue and by bookings with share %, charts + tables, export.
WHAT'S MISSING: no rating/avg-rating or occupancy-per-movie in this area (reviews not aggregated here).
RECOMMENDED IMPROVEMENTS: add avg user rating column; combine revenue+bookings into one ranking.
PRIORITY: Low

==================================================
FEATURE: 6. User Growth Analytics
STATUS: ✅ Fully Implemented
FILES: `admin_panel/analytics/services.py:700` (`users_data`), template `templates/admin/analytics/users.html`
MODELS USED: User (date_joined), Booking, PaymentTransaction
APIs USED: `/analytics/users/`, `/analytics/data/users/`, `/analytics/export/users/`
TEMPLATES USED: `admin/analytics/users.html`
WHAT'S WORKING: new-user trend, top users (bookings/spend/last booking), KPIs + deltas, export.
WHAT'S MISSING:
  - `User.date_joined` is not indexed → aggregation over 50k+ users is a full scan
  - No cohort / retention analysis
RECOMMENDED IMPROVEMENTS: add `db_index` on `User.date_joined` (via migration), cache payload.
PRIORITY: Medium

==================================================
FEATURE: 7. Peak Booking Hour Analysis
STATUS: ✅ Fully Implemented
FILES: `admin_panel/analytics/services.py:589` (`peak_data`), `:610` (`_hour_weekday_matrix`), template `templates/admin/analytics/peak.html`
MODELS USED: Booking (booked_at)
APIs USED: `/analytics/peak/`, `/analytics/data/peak/`, `/analytics/export/peak/`
TEMPLATES USED: `admin/analytics/peak.html`
WHAT'S WORKING: hour series, weekday series, hour×weekday heatmap (extract functions), export.
WHAT'S MISSING: nothing material.
RECOMMENDED IMPROVEMENTS: none (well done; cache if heavily used).
PRIORITY: Low

==================================================
FEATURE: 8. CSV Export
STATUS: ✅ Fully Implemented
FILES: `admin_panel/analytics/services.py:888` (`csv_bytes`), `:906` (`xlsx_bytes`), `admin_panel/analytics/views.py:334` (`analytics_export`)
MODELS USED: aggregates only (no model writes)
APIs USED: `/analytics/export/<area>/?format=csv|xlsx|pdf`
TEMPLATES USED: `admin/analytics/report_pdf.html` (PDF format)
WHAT'S WORKING:
  - CSV with UTF-8 BOM (Excel-safe), formula-injection sanitization (`DANGEROUS_PREFIXES`), `Content-Disposition` attachment
  - Bonus: zero-dependency OOXML XLSX writer and print-ready PDF
  - Exports reuse already-aggregated dicts (no re-query)
WHAT'S MISSING: no booking/payment-list CSV export (only analytics areas); no pagination on export (fine — exports should be full).
RECOMMENDED IMPROVEMENTS: none critical.
PRIORITY: Low

==================================================
FEATURE: 9. Advanced Search
STATUS: ✅ Fully Implemented
FILES:
  - `movies/discovery.py` (`DiscoveryParams`, `discover_movies`, `search_suggestions` in `admin_panel/views.py:545`)
  - `admin_panel/views.py:545` (`search_suggestions` — AJAX suggestions)
  - `admin_panel/forms.py` `BookingSearchForm`/`PaymentSearchForm`
TEMPLATES USED: `movies/movie_list.html`, `static/js/discovery.js`
WHAT'S WORKING:
  - Public search by title (`name__icontains`), live suggestions, AJAX result refresh with count
  - Admin search: movies (name/director/status), bookings (movie/user/date/theatre), payments (user/status/order-id/date), users/staff (username/email), reservations (movie/theatre/date/user/status)
  - Input length-capped (`MAX_SEARCH_LENGTH=100`), suggestion list capped, `q`/`search` both accepted
WHAT'S MISSING:
  - Search is `icontains` only — no trigram/full-text search; at 100k bookings `user__username__icontains` / `gateway_order_id__icontains` are full scans (unindexed, leading-wildcard)
  - No search within reviews, notifications, audit logs, coupons
  - No result counts on admin search forms (Django ListView shows counts implicitly via pagination — OK)
RECOMMENDED IMPROVEMENTS:
  - PostgreSQL: enable `pg_trgm`/GIN indexes on `Movie.name`, `Booking.booking_ref`, `PaymentTransaction.gateway_order_id` for prefix search
  - Add review/notification search filters in admin
PRIORITY: Medium

==================================================
FEATURE: 10. Movie Filters
STATUS: ✅ Fully Implemented
FILES: `movies/discovery.py` (`DiscoveryParams.from_request`, `discover_movies`, `facet_data`, `chip_data`)
TEMPLATES USED: `movies/movie_list.html` (facet sidebar + removable chips)
WHAT'S WORKING:
  - Genre (slug), Language (code), City, Theatre, Release window (week/month/3mo/year), Rating ≥, Timing (morning/afternoon/evening/night)
  - Facet options derived only from visible movies; active filters shown as removable chips; query-string preserved across pagination (`querystring()`)
  - Multi-value params capped (`MAX_LIST_VALUES=10`), validated whitelists (no injection)
WHAT'S MISSING: no price-range slider filter; no format (2D/IMAX) filter.
RECOMMENDED IMPROVEMENTS: none critical; add price range if desired.
PRIORITY: Low

==================================================
FEATURE: 11. Sorting
STATUS: ✅ Fully Implemented
FILES: `movies/discovery.py` (`SORT_CHOICES`, `discover_movies` sort branch), `templates/movies/movie_list.html`
WHAT'S WORKING:
  - popularity (annotated booking count), newest, rating, price_asc/desc (correlated min-price subquery), alpha_asc/desc
  - Server-side `order_by`; `distinct()` applied only when joined and non-aggregating
WHAT'S MISSING: sorting only applies to the movie catalog, not to admin lists beyond a few columns (admin lists do have column sorting).
RECOMMENDED IMPROVEMENTS: none.
PRIORITY: Low

==================================================
FEATURE: 12. Pagination
STATUS: ✅ Fully Implemented
FILES: `movies/views.py:44` (`movie_list` — `Paginator` + `get_elided_page_range`), `movies/views.py:103` (review pagination `rpage`), all admin `ListView`s (`paginate_by`, per_page 10/20/50/100)
TEMPLATES USED: `movies/movie_list.html`, `_movie_results.html`, admin list templates
WHAT'S WORKING:
  - Elided page range (2 on each side), per-page selector (12–48 capped `MIN/MAX_PER_PAGE`), AJAX partial refresh, filter-preserving query strings, page clamping (`max(1, ...)`)
  - Admin lists allow 10/20/50/100 per page
WHAT'S MISSING: pagination of dashboard "recent bookings" (capped at 10, fine); no cursor pagination for very large tables (count query on 100k rows is cheap on indexed filters, acceptable).
RECOMMENDED IMPROVEMENTS: none.
PRIORITY: Low

==================================================
FEATURE: 13. Recommendation Engine
STATUS: ✅ Fully Implemented
FILES: `movies/discovery.py` (`trending_movies`, `recently_released`, `similar_movies`, `recommended_for_user`, `_favourite_ids`)
TEMPLATES USED: `home.html`, `movie_list.html`, `movie_detail.html`
WHAT'S WORKING:
  - Personalized feed: confirmed booking history → favourite genres/languages/theatres; recently-viewed session genres; `rec_score = 3*genre + 2*language + theatre + rating`; trending backfill so feed never thin
  - Trend score = bookings×10 + recent-bookings×20 + wishlists×2 + approved reviews×3 + rating
  - Similar movies: genre×4 + language×3 + bookings + rating, `Abs(rating-distance)` tie-break
  - All via ORM annotated `Count`/`Subquery` — no Python scoring over full dataset
WHAT'S MISSING:
  - No collaborative filtering (user-user/item-item) — content-based + popularity only
  - No negative feedback/explicit "not interested"
  - `facet_data` materializes `visible_ids` list for `movies__in` (see performance)
RECOMMENDED IMPROVEMENTS:
  - Cache trending/similar (recomputed each request)
  - Convert `facet_data` to subqueries to keep index usage
PRIORITY: Medium (perf) / Low (features)

==================================================
FEATURE: 14. PDF Ticket Generation
STATUS: ✅ Fully Implemented (server-side PDF added)
FILES: `templates/movies/ticket.html` (`@media print` CSS + Print button), `movies/views.py` (`download_ticket`, `ticket_pdf`), `movies/pdf.py` (`build_ticket_pdf`), `movies/qr.py`
MODELS USED: Reservation, Booking, Seat, Payment, PaymentTransaction
APIs USED: `GET /ticket/<booking_ref>/` (renders HTML ticket), `GET /ticket/<booking_ref>/pdf/` (server-side PDF)
TEMPLATES USED: `movies/ticket.html`, `movies/invoice.html`
WHAT'S WORKING:
  - Rich, print-optimized HTML M-ticket (movie, cinema, screen, showtime, seats, payment method/id, QR) + browser print → PDF
  - Server-side landscape A4 M-ticket via `reportlab` (lazy-imported; returns `None` if library missing) with QR image, ref, amount, payment details, footer
  - "Download PDF" button next to "Print Ticket"; `Content-Disposition: attachment`
  - Ownership guard (redirects non-owners to profile), status guard, GST invoice page
  - QR embedded as PNG data URI on HTML ticket and as raw PNG in the PDF
WHAT'S MISSING:
  - PDF needs `reportlab` installed (pinned in `requirements.txt`); app degrades gracefully without it
  - No weasyprint HTML→PDF pipeline (reportlab chosen for zero-OS-dependency)
RECOMMENDED IMPROVEMENTS: none critical.
PRIORITY: Medium (done)

==================================================
FEATURE: 15. QR Code Generation
STATUS: ✅ Fully Implemented (HMAC-signed, gate-scanner verify endpoint added)
FILES: `movies/qr.py` (`build_qr_payload`, `sign_qr_payload`, `verify_qr_payload`, `ticket_qr_png_bytes`, `ticket_qr_data_uri`), `movies/views.py` (`verify_ticket_qr`), `templates/movies/ticket.html`
MODELS USED: Reservation, Booking (payload = booking_id, movie, theatre, seats + HMAC `sig`)
APIs USED: none external (stdlib + `qrcode` package in requirements.txt); `POST /movies/api/ticket/verify/` gate-scanner endpoint
WHAT'S WORKING: scannable QR on the ticket (HTML + PDF), payload HMAC-SHA256-signed with `SECRET_KEY`, constant-time signature comparison, verified round-trip in tests, graceful fallback if library missing.
WHAT'S MISSING:
  - Gate-scanner endpoint returns 200 with `valid:false` for a well-signed but unknown booking (404-style UX); invalid signature returns 400 — contract is documented in tests
  - Payload is stateless beyond signature; expiry/cancellation is checked against the DB row on verify
RECOMMENDED IMPROVEMENTS: none critical.
PRIORITY: Medium (done)

==================================================
FEATURE: 16. Email Ticket Delivery
STATUS: ✅ Fully Implemented (HTML + QR + PDF attachment)
FILES: `movies/notifications.py` (`send_booking_confirmation`, `send_manual_booking_confirmation`), `templates/emails/booking_confirmation.html`, `users/otp.py` (`send_otp_email`), `admin_panel/views.py` (resend), `bookmyseat/settings.py` (EMAIL_BACKEND, `SITE_URL`)
MODELS USED: Notification (in-app), User
APIs USED: `django.core.mail.EmailMultiAlternatives`
TEMPLATES USED: `emails/booking_confirmation.html` (branded HTML with QR + action buttons)
WHAT'S WORKING:
  - Confirmation email on payment verify/webhook and manual/walk-in bookings; admin "resend confirmation"; OTP email on registration; console backend default, SMTP via env
  - `EmailMultiAlternatives` with HTML alternative + plain-text fallback
  - HTML body embeds movie/cinema/screen/showtime/seats/ref/payment/total + QR data URI, "View / Print Ticket" and "Download PDF Ticket" buttons
  - Server-side PDF M-ticket attached to the email (best-effort; `reportlab` optional)
  - Email failures never break booking (wrapped in try/except); in-app Notification created alongside email
WHAT'S MISSING:
  - Email is sent synchronously (one SMTP round-trip per booking) — acceptable on Vercel; move to Celery only if a worker is available
  - Real SMTP credentials required in production (`DJANGO_EMAIL_*` env)
RECOMMENDED IMPROVEMENTS: none critical.
PRIORITY: Medium (done)

==================================================
FEATURE: 17. Celery Integration
STATUS: ❌ Not Implemented
FILES: none (no `celery.py`, no `requirements.txt` entry, no `CELERY_*` settings, no task modules)
MODELS USED: n/a
APIs USED: n/a
TEMPLATES USED: n/a
WHAT'S MISSING:
  - No async task queue for: confirmation emails (currently synchronous — blocks the payment verify response), QR/PDF generation, reservation expiry sweeps, refund webhooks, analytics snapshots, notification fan-out
  - No `release_expired_reservations` scheduler (currently a lazy on-read sweep + manual management command `movies/management/commands/release_expired_reservations.py`)
RECOMMENDED IMPROVEMENTS (implement as new module):
  - Add `celery.py` + `CELERY_*` settings + `movies/tasks.py`
  - Move `send_booking_confirmation` and refund reconciliation to tasks
  - Add a beat schedule for `release_expired_reservations`, `cancel_stale_orders`
  - Note: requires a broker (see #18). Do NOT convert to async if Vercel serverless (Vercel has no persistent worker) — document this trade-off.
PRIORITY: High (async email) / depends on broker

==================================================
FEATURE: 18. Redis Integration
STATUS: ✅ Configured (django-redis via `REDIS_URL`, LocMem fallback)
FILES: `bookmyseat/settings.py` (`CACHES` block, `REDIS_URL` env, `KEY_PREFIX 'bms'`), `bookmyseat/ratelimit.py` (login brute-force), `users/otp.py` (OTP + resend cooldown), `admin_panel/analytics/views.py` (payload cache)
MODELS USED: n/a (cache keys only)
APIs USED: `django.core.cache.cache` (ratelimit, OTP, analytics payloads, test cache.clear)
WHAT'S WORKING:
  - `CACHES` uses `django_redis.cache.RedisCache` when `REDIS_URL` is set; otherwise LocMem fallback for dev
  - Rate limiting/OTP/analytics all use the cache abstraction, so switching backends is config-only
  - Analytics payloads cached per `(area, range)` for 5 min (feature #1)
WHAT'S MISSING:
  - `django-redis` pinned in `requirements.txt`; requires a live Redis in production (e.g., Upstash/Vercel KV) — LocMem remains per-process if `REDIS_URL` is absent
  - Not used as a Celery broker (no Celery on Vercel)
RECOMMENDED IMPROVEMENTS:
  - Deploy with `REDIS_URL` set for multi-worker consistency; reuse as Celery broker if a worker is ever added
PRIORITY: High (done)

==================================================
FEATURE: 19. Ticket Download History
STATUS: ✅ Fully Implemented
FILES: `movies/models.py` (`TicketDownload`), `movies/migrations/0020_ticketdownload.py`, `movies/views.py` (`_record_ticket_download` on `download_ticket` + `ticket_pdf`), `users/views.py` + `templates/users/profile.html` ("Recent Ticket Downloads" card)
MODELS USED: TicketDownload (user FK, booking_ref indexed, movie CharField, downloaded_at, ip_address, user_agent)
APIs USED: n/a (writes on ticket view/PDF download)
TEMPLATES USED: `users/profile.html`
WHAT'S WORKING:
  - Row written on each HTML ticket view and PDF download (booking_ref, movie, IP, user-agent, timestamp)
  - `Meta` ordering `-downloaded_at` + composite index `(user, downloaded_at)`
  - Profile shows the 5 most recent downloads with a re-download PDF button; empty state handled
WHAT'S MISSING:
  - No admin-side aggregate (count per booking) yet — low value, skip unless requested
  - IP/user-agent are stored as-is (privacy: consider truncating or aggregating if analytics needed)
RECOMMENDED IMPROVEMENTS: none critical.
PRIORITY: Low (done)

==================================================
FEATURE: 20. Admin Permission System
STATUS: ✅ Fully Implemented
FILES:
  - `admin_panel/models.py:151` (`AdminProfile` role), `:172` (`AdminPermission` module + can_view/create/edit/delete)
  - `admin_panel/decorators.py` (`admin_session_required`, `AdminSessionMixin`, `permission_required`)
  - `admin_panel/middleware.py` (`AdminIdentityMiddleware` — admin/customer session isolation)
  - `admin_panel/views.py` (staff CRUD + `staff_permissions`, role gating)
  - `bookmyseat/settings.py:65` (middleware), `:100` (admin session timeout)
MODELS USED: AdminProfile, AdminPermission, AuditLog, User
APIs USED: `/staff/`, `/staff/<pk>/permissions/`, all admin routes
TEMPLATES USED: `admin/staff/staff_list.html`, `admin/staff/staff_form.html`, `admin/staff/staff_permissions.html`
WHAT'S WORKING:
  - Separate admin session keys + `_verify_admin_session` (auth, active profile, session-id match, `is_active`); middleware maps identity only on admin URLs
  - Role hierarchy: superuser/super_admin → all; admin → all except settings/staff; staff → explicit module permissions
  - `permission_required(module, action)` on write endpoints (e.g., `booking.can_edit`, `payment.can_edit`, `staff.can_create`)
  - Access matrix tested (anon/customer/staff-without-perm/superuser → correct 302/200, never 500)
  - Audit log on login/logout and sensitive mutations
WHAT'S MISSING:
  - Some views use only `AdminSessionMixin` without `permission_required` (DashboardView, MovieDeleteView.delete, toggle endpoints) — any active admin can do these (acceptable today; tighten if RBAC needs granularity)
  - No permission enforcement on the customer-facing `/api/cleanup-expired/` beyond is_staff (it's customer auth, minor)
  - `AdminPermission.module` is free-form text — typo drift risk
RECOMMENDED IMPROVEMENTS:
  - Add a module constants table to prevent typos; add `permission_required` to the few admin actions that lack it (movie delete/toggle, dashboard fine)
  - Consider adding report-review audit logging (flag already exists; no AuditLog entry)
PRIORITY: Low-Medium

---

## 3. Phase 4 — Performance Audit

### 3.1 Confirmed N+1 / query-shape issues

| Location | Issue | Fix |
|---|---|---|
| `users/views.py:206` (`profile`) | `movie.languages.all()[:3]` inside `for group in booking_groups` → N+1 (one query per booking group) | `prefetch_related('movie__languages')` on the bookings queryset |
| `movies/views.py:100-102` (`movie_detail`) | `cast_members/gallery/trailers` filtered again after `prefetch_related` — redundant queries | Use the prefetched caches |
| `movies/views.py:118-124` (`movie_detail`) | `all_reviews = list(review_base)` loads ALL reviews into Python just to build `rating_dist` | `review_base.values('rating').annotate(n=Count('id'))` (single SQL) |
| `movies/discovery.py:244` (`facet_data`) | `visible_ids = visible_movies().values_list('pk', flat=True)` materializes the full visible catalog into a list, then `movies__in=visible_ids` | Use a subquery / `.filter(movies__id__in=visible_movies().only('pk'))` |
| `admin_panel/views.py:194-207` (Dashboard monthly chart) | 6 separate `Booking.objects.filter(booked_at__date__gte/lt).count()` queries per page load | One `TruncMonth` + `Count` aggregation; loop in Python |
| `admin_panel/views.py:159-165` (Dashboard) | `Seat.objects.filter(is_booked=True).count()` + `ReservedSeat...count()` — full scans | Derive from confirmed bookings (indexed); drop reliance on `Seat.is_booked` |
| `movies/services.py` `confirm_booking` + `payments.py` `verify_and_confirm` | pricing computed twice (`_recompute_total` then `confirm_booking`→`reservation_pricing`) | Pass computed pricing in, or accept (2 extra small queries/booking — low) |
| `admin_panel/analytics/services.py:248` `summary_metrics` | runs twice per overview/revenue request (page render + JSON) | Cache the payload (see Feature 1) |

### 3.2 Missing indexes

| Table.Column | Hot query | Migration needed |
|---|---|---|
| `movies_theater.time` | `time__date=...`, `time__gte` (theater_list, booking guards, dashboard) | ✅ add `db_index` |
| `movies_seat.is_booked` | walk-in seat selection, dashboard seat counts | ✅ add `db_index` (or stop querying it) |
| `movies_reservation (status, expires_at)` | lazy expiry sweep on every seat load | ✅ composite index |
| `auth_user.date_joined` | user-growth aggregation (50k+ users) | ✅ add `db_index` |
| `admin_panel_notification (user, is_read)` | inbox + unread badge | ✅ composite index |
| `admin_panel_review (is_approved, is_hidden, movie)` | movie_detail review listing | ✅ composite index |
| `admin_panel_auditlog.created_at` | logs list ordering | ✅ add `db_index` |

Already well-indexed: `Booking (status,booked_at)`, `(user,booked_at)`, `(movie,booked_at)`, `(theater,booked_at)`, `booked_at`; `Payment (status,paid_at)`, `(booking,status)`, `payment_method`; `PaymentTransaction (user,status)`, `(reservation,status)`, `(status,created_at)`, `(created_at)`, `gateway_order_id`, `gateway_payment_id`; `Reservation (user,status)`, `(show,status)`, `expires_at`, `token`, `booking_ref`.

### 3.3 Scale targets (100k bookings / 50k users / thousands of shows)

- **Analytics** — ✅ already built on indexed aggregate queries; caching will make repeated range loads O(1)-ish.
- **Search** — ⚠️ `icontains` on unindexed text = full scan at 100k bookings. Needs pg_trgm/GIN (Postgres) or trigram on SQLite `LIKE` is table scan.
- **Seat polling** — ✅ ETag/304 + `seat_revision` keeps bandwidth flat.
- **Reservation expiry** — ⚠️ depends on lazy on-read sweep + manual command; a Redis-based TTL index or scheduled task would remove the periodic `expires_at <= now` scan.
- **Dashboard** — ⚠️ ~30 queries + 6-loop; consolidate to ~10 aggregate queries.

---

## 4. Phase 5 — Security Audit

| Area | Finding | Severity |
|---|---|---|
| `DEBUG` default True + dev `SECRET_KEY` fallback (`settings.py:27,33`) | Must set `DJANGO_DEBUG=False` + real `DJANGO_SECRET_KEY` in production (Vercel). Raises `ImproperlyConfigured` only when DEBUG False — good. | High (config) |
| `ALLOWED_HOSTS` includes `.vercel.app` wildcard | Trim to real host(s) in prod. | Medium |
| `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` default off | Auto-True when DEBUG off; confirm behind HTTPS. | Medium |
| `ticket.html:174` `{{ wa_link|safe }}` / `{{ wa_web_link|safe }}` | Server-built, URL-encoded via `quote()` — low risk, but `|safe` in a JS string is a latent pattern; consider `escapejs` + safe URL construction. | Low |
| QR payload unsigned (`movies/views.py:652`) | Ticket QR reveals booking_id; ownership enforced on the page, but no gate-scanner validation exists. Add HMAC + validation endpoint. | Medium |
| Admin actions with `AdminSessionMixin` only (MovieDeleteView, toggles, dashboard) | Any active admin can execute; add `permission_required` for granularity. | Low |
| Webhook HMAC verified, `@csrf_exempt` scoped to webhook, client-supplied amounts rejected | ✅ Strong. | — |
| Owner checks on every reservation/payment/booking/ticket endpoint | ✅ IDOR closed. | — |
| Analytics JSON via `json_script`, CSRF on all AJAX fetch, `textContent` in JS, CSV formula-injection sanitized | ✅ Strong. | — |
| Rate limiting: login/OTP only; no limit on reserve/payment-start APIs | Add Redis-backed throttle on `/api/reserve/`, `/api/payment/*/start/`. | Medium |

---

## 5. Implementation Plan (only for ⚠️/❌ items; no rewrite of working code)

### Step 1 — Quick wins (no new deps, low risk)
1. ✅ Fix N+1 in `users/views.py` profile (`prefetch_related('movie__languages')`); remove redundant re-queries in `movie_detail` (uses prefetched `.all()` caches).
2. ✅ Replace Python `rating_dist` with a single `values('rating').annotate(Count)`.
3. ✅ Add missing indexes (migrations): `Theater.time`, `Seat.is_booked`, `Reservation(status,expires_at)`, `Notification(user,is_read)`, `Review(movie,is_approved,is_hidden)`, `AuditLog.created_at` — all present in models.
4. ✅ Cache analytics payloads with `cache.set(..., timeout=300)` keyed on `(area, range_key, start, end)` — implemented in `admin_panel/analytics/views.py` (admin-session only).
5. ✅ Add `permission_required` to the handful of admin write endpoints missing it (`show_toggle_status`, `show_bulk_action`); AuditLog entry on `report_review` present.

### Step 2 — Email + Ticket PDF + Download history
6. ✅ Build HTML confirmation email (template `templates/emails/booking_confirmation.html`) via `EmailMultiAlternatives` with a ticket/QR link and PDF attachment.
7. ✅ Add server-side PDF generation endpoint `GET /ticket/<booking_ref>/pdf/` reusing `_ticket_context` (`reportlab` in requirements, lazy import + graceful degradation). "Download PDF" button added in `ticket.html`.
8. ✅ New model `TicketDownload` + record on `download_ticket`/`ticket_pdf`; recent downloads surfaced in `profile.html`.

### Step 3 — Async + Redis (deployment-dependent)
9. ✅ Add `django-redis` + `CACHES` (Redis via `REDIS_URL`, LocMem fallback) — makes rate limiting/OTP consistent across workers; enables analytics payload caching.
10. ⏳ Celery (`celery.py`, `tasks.py`, beat schedule) — DEFERRED. Requires a persistent worker + broker; Vercel serverless has neither. Email stays synchronous (1 SMTP request per booking — acceptable). Revisit if a worker host is added.

### Step 4 — Scale hardening (Postgres)
11. ⏳ Enable `pg_trgm` + GIN indexes for prefix search on `Movie.name`, `Booking.booking_ref`, `PaymentTransaction.gateway_order_id` — Postgres-only migration; apply when `DATABASE_URL` targets Postgres (SQLite dev doesn't support GIN).
12. ✅ Replace `facet_data` list with subqueries (Django already emits `IN (SELECT ...)`, verified in `movies/discovery.py`); convert dashboard monthly loop to `TruncMonth` aggregate (done in `admin_panel/views.py`).

**Ordering constraint:** Steps 1–2 are safe on the current SQLite/Vercel setup. Steps 3+ require infrastructure decisions (Redis/worker availability) — confirm with the owner before changing `requirements.txt`.

---

## 6. Verdict

- **Do not rewrite:** all 12 ✅ features (analytics suite, search/filter/sort/pagination, recommendation, QR, permission system, CSV) — they are well-architected and tested.
- **Enhance:** the 6 originally-⚠️/❌ items are now closed: server-side PDF (14), HMAC-signed QR + gate verify (15), HTML email with PDF attachment (16), Redis cache config (18), and ticket download history (19). Remaining recommended work is the low-severity performance/security items in §3–§4 (analytics payload caching and permission gates already applied in Step 1).
- **Implement new:** Celery task layer — only when the deployment target can support a persistent worker. Documented in Step 3/10.
