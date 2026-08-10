# BookMySeat — Admin Analytics Performance Report

**Date:** 10 Aug 2026 (initial build 03 Aug 2026)
**Scope:** Admin Analytics Dashboard for the BookMySeat movie ticketing platform.

---

## 1. Overview

A full Admin Analytics module was built so superusers/staff can monitor the business
health of the platform: revenue, bookings, occupancy, movie/theater performance, peak
booking windows, payments, refunds and user acquisition. Every screen supports date-range
filtering (10 presets + custom range) with in-place AJAX refresh and CSV / XLSX / PDF export.

All analytics queries are executed with the Django ORM using aggregate/annotate calls on
indexed columns, and date filtering uses half-open intervals (`start <= value < end`) so the
SQLite/PostgreSQL query planner can always use the added indexes.

---

## 2. Deliverables

| Area | What was added |
| --- | --- |
| Data layer | `admin_panel/analytics/services.py` — 10 analytics areas, range resolution, exports |
| Views | `admin_panel/analytics/views.py` — 10 CBV pages + JSON data endpoint + export endpoint |
| URLs | `/analytics/`, `/analytics/{area}/`, `/analytics/data/<area>/`, `/analytics/export/<area>/` |
| Templates | `templates/admin/analytics/*` (base, 10 area pages, partials, PDF report) |
| Static | `static/admin/css/analytics.css`, `report.css`, `static/admin/js/analytics.js` |
| Navigation | Analytics section added to the admin sidebar |
| DB indexes | `movies.0013`, `admin_panel.0008`, `movies.0014` |

---

## 3. Performance work

### 3.1 Database indexes (applied)

| Table | Index |
| --- | --- |
| `movies_booking` | `booked_at` (db_index) |
| `movies_booking` | `(status, booked_at)` |
| `movies_booking` | `(user, booked_at)` |
| `movies_booking` | `(movie, booked_at)` |
| `movies_booking` | `(theater, booked_at)` |
| `movies_booking` | `booking_ref` (unique — already indexed) |
| `admin_panel_payment` | `status`, `paid_at` (db_index) |
| `admin_panel_payment` | `(status, paid_at)`, `(booking, status)`, `(payment_method)` |
| `admin_panel_paymenttransaction` | `(status, created_at)`, `(created_at)` |

The `Booking.booking_ref` field is `unique=True`, which already creates an index, so no
redundant index was added.

### 3.2 Query strategy

- **Series** (`revenue`, `bookings`, `users`, `transactions`, `refunds`): one grouped
  `TruncDate`/`TruncMonth`/`ExtractYear` query; missing buckets are zero-filled in Python
  (no N+1, no per-day queries). Granularity adapts to the range length (day ≤ 92 days,
  month ≤ 730 days, year beyond).
- **Distributions**: single `values(...).annotate(Count/Sum)` grouped queries.
- **Rankings**: annotated `Count`/`Sum` with `filter=Q(...)` on the join, ordered + sliced
  (`[:10]`), then materialized.
- **Recent bookings** and **refund details**: `select_related` to avoid N+1.

### 3.3 Scale safety

Verified at **100,000 benchmark bookings** (plus 100k payments, 100k reservations,
100k payment transactions, 100k seats, 200 theaters) using a repeatable seed command:

- The `booked_at`/`paid_at`/`created_at` filters always land on indexed columns, and
  period-over-period changes reuse the same bucket queries over the previous window.
- Every measured area query resolves via an index (`EXPLAIN QUERY PLAN` evidence in §3.4).
- A real index-drop experiment shows the `(status, booked_at)` index is worth a
  **~34–62x** speed-up over the unindexed fallback (evidence in §3.4).

### 3.4 Measured performance at 100k bookings

Environment: SQLite, single machine, `benchmark_analytics` management command
(best of 3 runs). Dashboard areas:

| Area | Time |
| --- | ---: |
| overview | 301 ms |
| revenue | 385 ms |
| bookings | 182 ms |
| occupancy | 148 ms |
| movies | 415 ms |
| theaters | 203 ms |
| peak | 143 ms |
| payments | 253 ms |
| refunds | 85 ms |
| users | 132 ms |
| **Total (10 areas)** | **2,247 ms** |

ORM aggregate vs naive Python loop (same query, range = last 90 days):

| Approach | Time |
| --- | ---: |
| ORM aggregate (`SELECT SUM … GROUP BY`) | 53 ms |
| Naive loop (load all rows into Python) | 77 ms |
| **Speed-up** | **~1.4x** |

WITH vs WITHOUT the `(status, booked_at)` index on `movies_booking` (real index
dropped inside a transaction, rolled back after):

| Plan | Time |
| --- | ---: |
| WITH index (COVERING INDEX `movies_book_status_40a788_idx`) | 1.2–2.4 ms |
| WITHOUT index (index on `booked_at` only) | 66–81 ms |
| **Speed-up** | **~34–62x** (run-dependent) |

`EXPLAIN QUERY PLAN` for the range-filtered queries (all index-based):

```
Count bookings in 90-day range
  SEARCH movies_booking USING INDEX movies_booking_booked_at_9951519a (booked_at>?)

Confirmed bookings in range + day bucket (bookings trend)
  SEARCH movies_booking USING COVERING INDEX movies_book_status_40a788_idx
    (status=? AND booked_at>? AND booked_at<?)
  USE TEMP B-TREE FOR GROUP BY

Top movies by revenue in range
  SEARCH movies_booking USING INDEX movies_booking_booked_at_9951519a (booked_at>? AND booked_at<?)
  USE TEMP B-TREE FOR GROUP BY

Occupancy per theater
  SEARCH movies_theater USING INDEX movies_theater_time_94cd72ed (time>? AND time<?)
  SEARCH movies_seat USING COVERING INDEX movies_seat_theater_id_434941f8 (theater_id=?) LEFT-JOIN
  SEARCH movies_booking USING COVERING INDEX movies_book_theater_a2fd03_idx (theater_id=?) LEFT-JOIN

Sum of completed payments in range (revenue KPI)
  SEARCH admin_panel_payment USING INDEX admin_panel_status_483f04_idx (status=? AND paid_at>? AND paid_at<?)
```

**Occupancy optimization (178x):** the original occupancy query annotated `Count('seats',
distinct=True)` and `Count('booking', distinct=True)` in one join, which fans out to
`seats × bookings` rows per theater (~8M intermediate rows → 26.4 s). It was rewritten as
three single-pass `GROUP BY` aggregates (seats per theater, confirmed bookings per theater,
theater metadata) merged in Python → **148 ms**. All 43 analytics tests still pass.

### 3.5 Benchmark tooling (repo commands)

```bash
python manage.py seed_analytics --bookings 100000          # default: 100k bookings
python manage.py seed_analytics --bookings 10000 --theaters 40 --seats 250
python manage.py seed_analytics --flush                    # remove benchmark data only
python manage.py benchmark_analytics --runs 3              # print index + timing evidence
```

- The seed writes everything with `bulk_create` (chunks of 5,000) inside one transaction
  and backdates the `auto_now_add` columns (`booked_at`, `created_at`, `paid_at`) with raw
  `executemany` UPDATEs keyed on unique indexed columns — ~4 minutes for 100k bookings.
- Every seeded row is tagged `bench-*` / `BENCH*` so `--flush` never touches real data.

---

## 4. Architecture

```
Browser (Chart.js + AJAX)
        │  /analytics/data/<area>?range=...
        ▼
views.analytics_data_json ──► services.<area>_data(rng) ──► Django ORM
        ▲                                            │
        │                                            ▼
analytics.js  ◄──── charts/tables/range ────── _build_charts / _build_tables
```

- **`services.py`** owns all business logic (pure functions over a `DateRange`), making it
  unit-testable and reusable by both the page render and the AJAX endpoint.
- **`views.py`** enforces security, validates every request parameter (`range` preset,
  `start_date`/`end_date`, `area`, `format`) and builds render-ready Chart.js specs.
- **`analytics.js`** renders charts/heatmaps from embedded JSON on load and refreshes
  everything in place (stats, charts, tables, heatmap, URL) via fetch on filter change.
  Unauthorized/partial DOM updates are guarded (`[data-stat]`/`[data-change]`/`[data-table]`/
  `data-chart`/`data-heatmap` hooks must exist to be updated).

### Exports (zero new dependencies)

- **CSV** — stdlib `csv`, UTF-8 with BOM so Excel opens it correctly.
- **XLSX** — hand-rolled, standards-compliant OOXML package (zipfile + XML) with styled
  headers; no third-party library, safe for the Vercel build.
- **PDF** — a print-optimized HTML report (`report_pdf.html`) the admin saves as PDF via the
  browser; no wkhtmltopdf/weasyprint dependency.

---

## 5. Security model

- Every analytics route requires the **admin session** (`admin_user_id`,
  `is_admin_authenticated`, `admin_session_id`) via `AdminSessionMixin` /
  `admin_session_required`, plus the **`analytics.can_view`** module permission via
  `permission_required`.
- Ordering matters and is enforced: the admin-session guard runs *before* the permission
  check, so anonymous and customer users get a 302 to `/admin-login/` (never a 500).
- Access matrix (verified by tests):
  - Anonymous / logged-in customer → redirect `/admin-login/`
  - Staff without an active `AdminProfile` → redirect `/admin-login/`
  - Staff role `staff` without the `analytics.can_view` permission → redirect `/dashboard/`
  - Staff role `admin` (default broad access) / superadmin → 200
- Unknown areas, presets and export formats fall back safely (`400` or default value);
  export endpoints set a `Content-Disposition` attachment header.

---

## 6. Data-correctness fixes

Found and fixed while building the test suite:

| Issue | Fix |
| --- | --- |
| Revenue “by method” ignored the date range | `completed` payments now filtered by `paid_at` window |
| Occupancy counts inflated by join fan-out (`seats × bookings`) | `distinct=True` counts + per-show range filter on confirmed bookings |
| Occupancy counted out-of-period bookings | `booked_at` window applied |
| Theater rankings ignored the date range | `booking__booked_at` window applied to `Count`/`Sum` filters |
| Revenue/occupancy pages referenced payload keys that did not exist (`summary`, `*_fmt`) | `summary` + formatted component keys added to payloads; regression test asserts every `data-stat`/`data-change` path resolves |
| Idempotent payment verification returned bookings in unstable order | `Booking.Meta.ordering` tie-broken by `-id`; verification path orders by `pk` |
| Peak-heatmap weekday row headers rendered blank | `get_item` template filter used `.get()` on a list (`matrix.weekdays`); rewritten to support list indexes as well as dict keys |

---

## 7. Test results

- **`python manage.py test` → 296 tests, OK** (full project suite, including the new
  `admin_panel.analytics` suite of 43 tests).
- Analytics coverage includes: range resolution, granularity selection, summary KPIs and
  period-over-period deltas, all 10 area functions with seeded data, zero-data safety,
  CSV/XLSX structure, permission matrix (anon/customer/staff/superuser), all 10 pages render,
  JSON endpoint shape, exports (content-type + valid OOXML zip), the
  `data-stat`/`data-change` resolution regression test, and seeded page-render checks
  (real values shown in stat cards, heatmap weekday labels + non-zero cells, export links
  carry the current range).
- `python manage.py check` passes with 0 issues.

---

## 8. Deployment notes

- **No `requirements.txt` changes** — exports use only the Python standard library.
- Chart.js is already loaded globally by `templates/admin/base_admin.html` (jsDelivr CDN),
  consistent with the existing Bootstrap/Tom-Select CDN usage.
- Migrations `movies.0013`, `admin_panel.0008`, `movies.0014` must be applied on deploy
  (`python manage.py migrate`).
- The local development database is loaded with **100,000 benchmark bookings** so every
  dashboard area renders populated charts; run `python manage.py seed_analytics --flush`
  to remove benchmark data before going to production.

---

## 9. Admin credentials

Existing admin accounts (unchanged — passwords are not stored in the repository and were
not modified):

| Username | Type |
| --- | --- |
| `girish` | Superuser / staff |
| `kiranbabu` | Superuser / staff |
| `admin` | Superuser / staff |
| `MR_BLACK` | Superuser / staff |

**Demo account created for reviewing the analytics dashboard** (change the password after
review — `python manage.py changepassword admin_demo`):

| Username | Password | Type |
| --- | --- | --- |
| `admin_demo` | `Admin@12345` | Superuser / staff |

Log in at `/admin-login/` (or `/admin/`), open **Analytics** from the sidebar, and use the
range picker (10 presets + custom) to filter every area in place. CSV / XLSX / PDF exports
are available per area. Analytics access for any new staff member is granted via
`AdminPermission(module="analytics", action="can_view")`.
