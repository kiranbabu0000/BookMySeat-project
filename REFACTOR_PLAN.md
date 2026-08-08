# BookMySeat — Phase 3 & 4: Feature Audit + Target Architecture

**Date:** 04 Aug 2026
**Status:** Design proposal for ratification before Phase 5 implementation.
**Baseline:** Commit `d1c5732` ("Baseline: analytics module, payment gateway, rate limiting, test suite") — 178 tests passing, working tree clean.

---

## Phase 3 — Feature Audit

### 3.1 Current model ownership

| Concept | `movies` app | `admin_panel` app |
| --- | --- | --- |
| Movie catalog | `Movie` | — (references `movies.Movie`) |
| Cinema | `Theater` (movie+time+cinema-name, denormalized) | `Theatre` |
| Screen | — | `Screen` (capacity, seat_layout) |
| Showtime | (encoded inside `Theater.time`) | `Show` (movie, theatre, screen, date, time) |
| Physical seat | `Seat` (FK → `Theater`) | — |
| Tier price | `SeatCategory`, `ShowPrice` (FK → `Theater`) | `GSTSlab`, `PricingConfig` |
| Order | `Booking` (FK → `Theater`) | — |
| Payment | — | `Payment`, `PaymentTransaction` |
| Reservation | `Reservation` (FK → `Theater`), `ReservedSeat` | — |
| Reviews | — (uses `admin_panel.Review`) | `Review` |
| Notifications | — (uses `admin_panel.Notification`) | `Notification` |
| Wishlist | `Wishlist` | — |
| Coupons | `Reservation.coupon` FK → `admin_panel.Coupon` | `Coupon` |
| Metadata | — | `Genre`, `Language`, `CastMember`, `Trailer`, `MovieImage` |
| Ops/security | — | `AdminProfile`, `AdminPermission`, `AuditLog` |

**Key finding:** the two apps are already mutually coupled — `movies.Reservation` imports
`admin_panel.Coupon`, while `admin_panel.Review/Payment` reference `movies.Booking` /
`movies.Reservation` / `movies.Movie`. There is **no clean app boundary today**; the
practical goal is a single source of truth per concept, not a pure layering.

### 3.2 The core duplication (primary refactor target)

The booking pipeline runs on `movies.Theater` + `movies.Seat`, which are **not** the same
objects the admin manages (`admin_panel.Theatre` / `Screen` / `Show`). They are bridged only by:

- `admin_panel/services.py` → `create_seats_for_theater()` / `sync_theater_from_show()`
- `movies/management/commands/seed_data.py` (creates **both** worlds: admin `Theatre/Screen/Show` AND `movies.Theater/Seat`)

Any admin edit of a show/theatre does **not** propagate to the customer booking pipeline —
the bridge is one-time seeding. This is the top architectural defect.

### 3.3 Feature verdicts (keep / improve / merge / remove)

#### Customer (`movies` + `users`)
| Feature | Verdict | Notes |
| --- | --- | --- |
| Register / login / logout / profile / password reset | KEEP | `users/tests.py` is a 60-byte stub → add coverage in Phase 8 |
| Wishlist | KEEP | already tested |
| Notifications (user inbox) | KEEP | model `admin_panel.Notification` — optional later re-home to `users` |
| Home / movie list / search + `search-suggestions` | KEEP | |
| Movie detail + reviews + report review | KEEP | `Review` model is in `admin_panel` but owned by customer flow → optional re-home to `movies` |
| Theatre/show listing (`theater_list`) | MERGE/REWIRE | change source from `movies.Theater` → `admin_panel.Show` (real cinema + screen + showtimes) |
| Seat selection map + live polling (`seat_selection.js`) | KEEP | re-anchor `Seat` from `Theater` → `Show`; JS workflow unchanged (show id stays) |
| Reservation (2-min hold, `select_for_update`, expiry) | KEEP | core strength; unchanged |
| Coupon validation | KEEP | `Reservation.coupon` already points at `admin_panel.Coupon` |
| Payment (Razorpay + demo + webhook + refund) | KEEP | unchanged |
| Confirmation + ticket download + cancel | KEEP | templates reference `theater` → update to `show` |
| `seed_data.py` | REPLACE | dual creation removed; single path after unify |

#### Admin (`admin_panel` + `analytics`)
| Feature | Verdict | Notes |
| --- | --- | --- |
| Admin auth (separate session + middleware) | KEEP | good isolation; preserve as-is |
| Movie CRUD / toggle / restore / removal | KEEP | |
| Genre / Language / Cast CRUD + ajax-add | KEEP | |
| Theatre / Screen / Show CRUD | KEEP | becomes **the single source of truth** |
| Seat management | MERGE | operate on `Show`/`Screen` directly; drop `movies.Theater` path |
| Pricing dashboard / `GSTSlab` / `PricingConfig` | MERGE | consolidate with `ShowPrice` under the unified show |
| Trailer / Image CRUD | KEEP | |
| Bookings list/detail/cancel/reserve/modify/resend | KEEP | re-anchor from `Theater` → `Show` |
| Payments + refund | KEEP | |
| Users + staff + permissions | KEEP | |
| Coupons CRUD | KEEP | |
| Review moderation | KEEP | |
| Audit logs / settings / search-suggestions | KEEP | |
| Analytics (10 areas) | KEEP | just built + tested; rewire any direct `Theater` references after unify |
| Dashboard | KEEP | |

#### Remove / consolidate (dead or duplicated)
| Item | Action |
| --- | --- |
| `movies.Theater` model + its FK chain | DELETE (re-anchor to `Show`) |
| `admin_panel/services.py` sync functions | DELETE (seats owned by `Show` directly) |
| `seed_data.py` dual creation | REPLACE with single-path seeding |
| `templates/users/basic.html` | DELETE (5-line stub, never rendered) |
| `base.html` inline offcanvas nav (duplicate of `_navbar.html`) | MERGE into `_navbar.html` partial |
| Seat summary "Convenience Fee" row (`d-none` in `seat_selection.html`) | RESOLVE — show it or drop it (pricing uses platform_fee + misc_fee + GST) |
| `users/tests.py` stub | IMPROVE (coverage added Phase 8) |

---

## Phase 4 — Target Architecture

### 4.1 Design decision (recommended)

**Keep `admin_panel.Theatre` / `Screen` / `Show` as the canonical operations domain.
Delete `movies.Theater`. Re-anchor `movies.Seat`, `ShowPrice`, `Booking`, and `Reservation`
onto `admin_panel.Show` (via `Screen` → `Theatre`).**

Why this direction (vs. moving the domain into `movies`):

1. All rich operations data already lives in `admin_panel`: screen capacity, `seat_layout`,
   per-show `ticket_price`, `GSTSlab`, `PricingConfig`, plus the full CRUD UI for
   Theatre/Screen/Show. Zero churn on the admin side.
2. `movies` already imports `admin_panel.Coupon`, so a `movies → admin_panel` reference is
   not a structural regression.
3. The churn is confined to the customer flow (`movies` views/templates/JS/tests), which is
   exactly where a single source of truth is needed.

**Seat ownership:** anchor `Seat` to `Show` (replacing `Theater`), preserving the current
per-show seat behavior. Per-show availability already derives from `Reservation` /
`ReservedSeat` / `Booking` — `Seat.is_booked` is legacy and can be dropped or ignored.
(Physical-seats-per-screen is more "correct" but higher-risk; documented as a later
optional refinement, not part of Phase 5.)

### 4.2 Target model map

```
movies.Movie  ──┬── admin_panel.CastMember / Trailer / MovieImage / Show
                │
                ├── movies.Wishlist
                └── admin_panel.Review            (customer-facing, optional later re-home)

admin_panel.Theatre ── admin_panel.Screen ── admin_panel.Show ── movies.Seat (per show)
                        (capacity)          (ticket_price)    ├─ movies.ShowPrice (Show × SeatCategory)
                                                                └─ movies.Reservation ── movies.ReservedSeat
                                                                     └── movies.Booking ── admin_panel.Payment
                                                                          └── admin_panel.PaymentTransaction
                                                                          └── admin_panel.Review.booking
admin_panel.GSTSlab / PricingConfig ── pricing for Show + Booking
admin_panel.Coupon ── movies.Reservation.coupon (unchanged)
```

Changes to existing models:

| Model | Change |
| --- | --- |
| `movies.Theater` | **deleted** |
| `movies.Seat.theater` | → `show = FK(admin_panel.Show)` |
| `movies.ShowPrice.theater` | → `show = FK(admin_panel.Show)` |
| `movies.Booking.theater` | → `show = FK(admin_panel.Show)` |
| `movies.Reservation.show` | `Theater` → `admin_panel.Show` (field already named `show`) |
| `admin_panel.Show` | optionally add `Seat` helper accessors / `get_price_for(category)` |

### 4.3 Dependency direction after unify

```
users        ──► movies          (customer identity + notification inbox)
movies       ──► admin_panel     (booking/payment pipeline uses Show, Coupon, GSTSlab)
admin_panel  ──► movies          (Show.movie, Review.booking, Payment.bookings)
```

No cycles at the import level after `Theater` removal; `admin_panel.services.py` and the
dual `seed_data.py` path are deleted.

### 4.4 Customer flow after unify

| Page | Before (movies.Theater) | After (admin_panel.Show) |
| --- | --- | --- |
| `theater_list` | flat list of Theater (movie+time+name) | Shows grouped by Theatre → Screen → showtime, real capacity/price |
| `book_seats` / `seat_status` | by `theater_id` | by `show_id` (same URL shape, new source) |
| `seat_selection.html` | `theaters.name/.time/.movie` | `show.theatre.name` / `show.time` / `show.movie` |
| `ticket` / `confirmation` | `booking.theater.*` | `booking.show.*` |
| seat map JS | show id in `data-show-id` | same; reservation/payment APIs unchanged |

### 4.5 Staging plan (keeps the suite green at every step)

- **Phase 5a — Model unify (no UI change):** add `show` FK columns; data-migrate
  `Theater` → `Show` (join on movie + date/time + theatre name); drop `movies.Theater`;
  re-point `Seat`/`ShowPrice`/`Booking`/`Reservation`; delete `admin_panel/services.py`
  sync functions; rewrite `seed_data.py` to create admin shows only; update all tests.
- **Phase 5b — UI rewiring:** `theater_list`, `book_seats`/`seat_status`, and the
  `seat_selection` / `ticket` / `booking_confirmation` templates render `Show` data.
- **Phase 5c — Cleanup:** delete `users/basic.html`; consolidate the offcanvas nav into
  `_navbar.html`; resolve the hidden "Convenience Fee" row; verify analytics has no
  lingering `movies.Theater` references.
- Phase 6+: as per master plan (payments/analytics re-check, admin panel consolidation,
  production hardening), each module tested after implementation.

### 4.6 Risks / mitigations

| Risk | Mitigation |
| --- | --- |
| Data migration mapping `Theater` → `Show` may not resolve for hand-created rows | test on the real `db.sqlite3` (currently 0 bookings); fallback to best-match + audit report |
| `movies` importing `admin_panel` grows surface | contained to models + services; admin app is the canonical ops domain by design |
| Test churn in `movies/tests.py` (reservation/concurrency suites) | run suite after 5a; assertions target behaviors, not model internals |
| Analytics queries referencing `Booking.theater` | grep + update in 5b/5c |

---

## Decisions to ratify

1. Adopt **Section 4.1** (admin_panel show domain canonical, `movies.Theater` deleted)?
2. `Seat` anchored to **Show** (per-show, low-risk) — accept physical-per-screen as later option?
3. Proceed with Phase 5a → 5b → 5c order, running `python manage.py test` after each?
4. Optional re-homes (`Review` → `movies`, `Notification` → `users`) deferred to later phase — acceptable?
