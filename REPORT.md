# Internship Project Report

**BookMySeat – Online Movie Ticket Booking System**

## Project Overview

BookMySeat is a complete online movie ticket booking platform built with the Django web framework. It was developed during my internship to create a production-style web application covering the full journey of booking a movie ticket — from discovering a movie and selecting seats to paying online and receiving a digital ticket by email.

**Problem it solves:** Booking a ticket usually means queues, paper tickets, and the risk of the same seat being sold twice. BookMySeat moves the whole experience online and, more importantly, makes seat selection safe — a seat can never be double-booked, even when two customers try to take it at the same time.

**Main purpose of the system:**
- Help customers search, discover and filter movies.
- Let customers select seats with a temporary hold while they pay.
- Process payments securely through the Razorpay gateway.
- Generate PDF tickets with QR codes and email them automatically.
- Give administrators a full dashboard with revenue, bookings and occupancy analytics.

**Main users:**
- **Customers** — who discover movies, book seats and pay.
- **Administrators** — who manage movies, shows, seats, bookings, reviews, notifications and refunds, and monitor business analytics.

## Technology Stack

| Category | Technology |
|---|---|
| Backend | Python 3.13, Django 6.0.7 |
| Frontend | HTML5, CSS3, JavaScript, Bootstrap 5 (server-rendered templates) |
| Database | SQLite (local development), PostgreSQL (production) |
| Payment Gateway | Razorpay, with a demo mode for development |
| PDF Generation | ReportLab |
| QR Code | qrcode (Python) |
| Email / Background Processing | SMTP or Brevo HTTPS API, delivered through a database-backed email outbox with a background worker (not Celery) |
| Deployment | Render (web service + managed PostgreSQL), Vercel (WSGI entry point) |
| Version Control | Git / GitHub |

## Task 1 – Movie Management with Trailer, Reviews and Ratings

### Objective

The task was to build the content backbone of the platform — a movie management module where administrators can create and manage movies together with genres, languages, cast, posters, trailers and age certifications, and where customers can view rich movie pages, rate movies and write trustworthy, moderated reviews.

### Implementation

- **Movie management** — a `Movie` model stores the title, description, story, duration, release date, age certificate, status, poster/thumbnail/banner images, production credits, trailer and ratings, with soft deletion.
- **Genre and language management** — `Genre` and `Language` are managed as master data and linked to movies through many-to-many relationships.
- **Cast management** — a structured `CastMember` model links actors to movies with name, photo, role and character name, shown as a cast grid on the detail page.
- **Multiple movie images** — each movie has a primary poster plus thumbnail and banner, and an additional gallery of images on the detail page.
- **Movie details** — a dedicated page shows the story, cast, gallery, trailer, show timings, rating breakdown and reviews.
- **Trailer support** — trailers are stored as YouTube URLs and embedded safely through a custom filter that extracts only the video ID and builds a fixed, safe embed URL; the admin forms also validate that only YouTube hosts are accepted.
- **Ratings** — customers rate movies 1–5 stars; the average is calculated from approved, visible reviews and shown with a star distribution.
- **Reviews** — a customer can post one review per movie, and only if they have a confirmed booking for that movie whose show has already ended.
- **Verified viewer functionality** — reviews linked to a real booking display a "Verified Viewer" badge.
- **Review editing / reporting / moderation** — customers can edit and report reviews, and administrators can approve, hide, restore or delete them from the admin portal.
- **Similar / trending / recent movies** — the discovery engine provides "More Like This" recommendations (based on shared genres and languages), plus trending, top-rated and recently-released sections on the home page.

### Outcome

The module delivers a complete movie content system: administrators can publish rich movie pages, and customers can browse them safely while contributing ratings and reviews that are genuine (booking-verified) and moderated before they count toward averages.

## Task 2 – Smart Seat Reservation with Live Availability

### Objective

The task was to build a seat selection and reservation engine that is safe under concurrency — the same seat must never be sold twice — while still giving customers a temporary, timed hold on their chosen seats so they can complete payment comfortably.

### Implementation

- **Seat selection** — each show has a seat map generated from its configuration, with per-seat prices (standard, wheelchair and couple seats, best-view markers, and a pricing catalogue per seat category).
- **Multiple seat selection** — customers can pick several seats at once, with a live price breakdown.
- **Temporary seat reservation/hold** — chosen seats are held through a reservation record for a fixed period (five minutes), enforced by the server.
- **Automatic release after timeout** — expired holds are released automatically: the system expires stale reservations, frees their seat rows and makes the seats bookable again.
- **Available / reserved / booked states** — the seat map shows three clear states, with each seat's true state derived from the database rather than trusted from the client.
- **Concurrent booking protection** — reservation mutations run inside database transactions and lock the show and seat rows; a one-to-one reserved-seat record guarantees at the database level that a seat belongs to at most one active reservation.
- **Django transactions** — every mutation (create, modify, confirm, release, cancel, expire) is wrapped in `transaction.atomic()`.
- **Prevention of double booking** — row locking plus the database constraint make double booking impossible even under a race.
- **Modify seat selection before payment** — while seats are held, the customer can add or remove seats; the server re-locks the reservation and affected seats, re-validates seating rules and recomputes the price.
- **Live availability** — the seat-status endpoint returns the current state of every seat as JSON with an ETag, and the browser polls every few seconds using conditional requests, so updates appear live with minimal traffic.

### Outcome

The result is a live, safe seat reservation system: customers see honest availability in real time, benefit from a timed hold and the freedom to change seats before paying, and the database guarantees that no seat is ever double-booked.

## Task 3 – Complete Payment Workflow with Booking Management

### Objective

The task was to integrate a complete, secure payment workflow with the Razorpay payment gateway — order creation, payment verification, webhooks, failure and cancellation handling, retries and refunds — and to make sure a booking is confirmed only after a genuinely successful payment.

### Implementation

- **Payment gateway** — Razorpay, integrated through a gateway module that creates orders, verifies signatures, verifies webhooks, fetches payments and creates refunds. A demo mode simulates the full flow locally when no real keys are configured.
- **Payment order creation** — when a customer starts payment, the client sends only the coupon code — never an amount. The server recomputes the full price from the database (seat prices, fees, GST and the validated coupon) and creates the Razorpay order for exactly that amount.
- **Payment verification** — on success the server verifies the payment signature, re-checks the payment against the gateway (order ID, amount and captured status) and only then confirms the booking.
- **Successful payments** — confirmation creates the booking records, marks seats booked, completes the reservation and enqueues the confirmation email.
- **Failed payments** — the failure is recorded and the reservation is marked failed, but the seats stay held so the customer can retry; a failure notification is queued.
- **Cancelled payments** — closing the payment modal records the cancellation as a failed attempt ("Payment cancelled by user"); seats remain held for a retry.
- **Payment retries** — a customer can start payment again for the same reservation; the system creates a fresh transaction (or reuses one when the amount is unchanged).
- **Webhook verification** — webhook events are verified with an HMAC-SHA256 signature over the raw request body using a constant-time comparison; the endpoint fails closed if the secret is not configured.
- **Payment status tracking** — every attempt is recorded with gateway order ID, payment ID, signature, amount, currency, status, method, failure reason, coupon and payload.
- **Transaction/reference IDs** — both the gateway transaction IDs and a human-friendly booking reference are stored and shown to the customer and the admin.
- **Booking confirmation after successful payment** — the confirmation page is reachable only once the reservation is actually booked, and booking completion happens strictly inside the verified server-side flow.
- **Seat release after failed/expired payment** — seats are freed when the hold expires, when the customer releases them, or when the booking is cancelled; a failed attempt keeps seats held for retry.
- **Booking/payment history** — the customer profile lists bookings grouped by transaction, and the admin portal shows the same data in its booking and payment management screens.
- **Duplicate payment protection** — callbacks that were already processed are ignored (row locking + idempotent booking creation), and confirmation/failure emails are sent at most once per transaction.
- **Refunds** — refunds go through the Razorpay refund API for captured transactions, with the refund ID recorded. Refunds can be triggered by customers when cancelling a booking and by administrators from the payment/booking management screens.

### Outcome

The payment module delivers a full, secure payment loop: prices are decided and validated on the server, bookings are confirmed only after verified captures, failures are handled gracefully with retry, and refunds are managed end-to-end for both customers and administrators.

## Task 4 – Comprehensive Admin Dashboard

### Objective

The task was to build a complete administrative portal that lets the business manage content and operations and understand performance through an analytics dashboard — revenue, bookings, occupancy, peak hours, refunds and user growth — with exportable reports.

### Implementation

- **Admin authentication** — a dedicated, session-based admin login separate from customer accounts, with roles, per-module permissions, session-ID rotation, an inactivity timeout, rate-limited login attempts and an audit log.
- **Booking management** — administrators can view, cancel and refund bookings, with multi-seat purchases grouped and shown as one transaction.
- **Revenue analytics** — total revenue, today's revenue and revenue trends over 7/30/90 days and 12 months, broken down by payment method and component (tickets, GST, fees, discount).
- **Daily/weekly/monthly/yearly analytics** — time-series charts adapt to the selected range.
- **Booking trends** — booking counts over time, status distribution, and distribution by weekday and hour.
- **Theatre occupancy** — seats sold vs total seats per show and per theatre, with an overall occupancy rate.
- **Most booked movies** — top movies by bookings and by revenue, with share percentages.
- **Top performing theatres** — top theatres by revenue, total shows and average revenue per show.
- **Peak booking hours** — the busiest hours and weekdays, presented as distributions and an hour-by-weekday heatmap.
- **Cancellation/refund statistics** — cancellation counts, value and rate, plus refund count, amount, rate and average refund.
- **User growth** — new-user growth over time, total/active users and a top-user ranking by bookings and spend.
- **Date filtering** — ten presets plus custom ranges, with change compared against the previous period.
- **CSV/report export** — every analytics area can be exported as CSV, XLSX and PDF.
- **Database-level aggregation/query optimization** — metrics are computed with database aggregation, composite indexes are defined on the hot booking/payment columns, empty buckets are zero-filled, and analytics responses are cached briefly.

### Outcome

The admin portal gives the business a secure operations console and a rich, exportable analytics suite built on real, aggregated booking and payment data, with queries that stay efficient as the dataset grows.

## Task 5 – Movie Discovery with Search, Filters and Recommendations

### Objective

The task was to build a discovery experience that helps customers find the right movie quickly — through search, multi-criteria filtering, sorting and personalised recommendations — while keeping queries efficient and the interface fully usable on mobile.

### Implementation

- **Movie search** — a case-insensitive title search with an autocomplete suggestion endpoint used in the navigation bar and on the discovery page.
- **Genre filtering** — multiple genres can be selected; a movie matches if it belongs to any selected genre.
- **Language filtering** — multiple languages can be selected.
- **City filtering** — filters to movies playing in a chosen city; the selected city is remembered and used as the default across pages.
- **Theatre filtering** — filters to a specific theatre, scoped to the selected city with a dependent dropdown.
- **Release-date filtering** — preset windows (this week, this month, last three months, last year).
- **Rating filtering** — a minimum rating floor on the movie's rating.
- **Show-time filtering** — named time buckets (morning, afternoon, evening, night) based on show start time.
- **Sorting** — seven options: popularity, newest release, rating, price (ascending/descending) and alphabetical (ascending/descending).
- **Ticket-price filtering** — a price range, evaluated against each movie's lowest active-show price.
- **Dynamic result counts** — the page shows how many movies match the current filters, updating on every change.
- **Pagination** — results are paginated with page links that preserve the filter state, and filtering runs over AJAX so the page does not reload.
- **Recommended movies** — "Recommended for You" is built from the customer's own booking history (preferred genres, languages and theatres), with trending movies as a fallback for new users.
- **Recently-viewed recommendations** — recently viewed movies are tracked in the session and shown on the home page, alongside "More Like This" recommendations on each movie page.
- **Optimized filtering using Django ORM** — all filters combine into a single query using chained conditions, annotations and subqueries for price/popularity, with duplicate rows removed and expensive feed queries cached briefly.

### Outcome

The discovery module turns a plain list of movies into a fast, filterable, personalised experience — customers can combine many criteria to find exactly what they want, get relevant suggestions from their own history, and use everything comfortably on a phone.

## Task 6 – Automated Ticket Generation and Email Confirmation

### Objective

The task was to complete the post-payment experience: generate a professional, verifiable PDF ticket for every confirmed booking, let the customer view and download it, and deliver a confirmation email with the ticket and QR code — without ever blocking the booking on the email infrastructure.

### Implementation

- **Booking confirmation** — as soon as payment is verified, the booking is confirmed and the ticket is generated from the confirmed booking data.
- **PDF ticket generation** — a branded landscape M-ticket rendered with ReportLab, containing the movie name, cinema, screen, show time, seat numbers, booking reference, amount paid and payment reference, plus the QR code.
- **QR code** — each ticket carries a QR code generated from a signed payload (booking reference, movie, theatre and seats). A venue-side endpoint verifies the QR signature, checks the booking is confirmed, and performs a one-time claim so a used ticket cannot be reused.
- **Ticket download** — customers can view and download the PDF ticket from their profile; downloads are recorded with the user, IP address and user agent.
- **Booking history** — the customer profile lists all bookings grouped by transaction, each with view, share, invoice and cancel actions, plus payment history and recent ticket downloads.
- **Email confirmation** — a confirmation email with the booking summary, QR code and PDF attachment is queued as soon as the booking is confirmed.
- **Background/asynchronous processing** — email delivery is decoupled from the request. Emails are written to a database outbox table, and a background worker delivers them. The worker starts automatically with the app (and with `runserver` during development) and can also be run as a standalone command. This is **not Celery** — no external queue is required.
- **Retry handling for failed emails** — failed deliveries are retried with exponential backoff up to a maximum number of attempts; emails that permanently fail are moved to a failed state with the last error recorded.

### Outcome

The ticket module completes the booking loop: every confirmed booking immediately yields a downloadable PDF ticket with a signed, single-use QR code, and a confirmation email that arrives in the background without ever holding up the customer or the booking.

## Overall System Workflow

The booking flow implemented by the six tasks:

```
Movie Discovery
      ↓
Movie Details
      ↓
Theater & Show Selection
      ↓
Seat Selection
      ↓
Temporary Seat Reservation
      ↓
Payment
      ↓
Payment Verification
      ↓
Booking Confirmation
      ↓
PDF / QR Ticket Generation
      ↓
Email Confirmation
      ↓
Booking History
```

## Skills and Learning Outcomes

Through this internship I gained practical, hands-on experience in:

- **Django development** — structuring a multi-app Django project with models, views, forms, services, template tags and management commands.
- **Python backend development** — separating business logic into service layers and building reliable server-side workflows.
- **Frontend development** — responsive interfaces with Bootstrap, JavaScript for live seat maps, polling, AJAX filtering and the payment modal.
- **Database design** — schema design, relationships, constraints, indexes and migrations.
- **Django ORM** — filtering, aggregation, annotation, subqueries and query optimisation.
- **Transactions and concurrency** — atomic transactions, row locking and database constraints to prevent double bookings.
- **Payment gateway integration** — order creation, signature and webhook verification, retries, idempotency and refunds.
- **PDF and QR generation** — generating branded PDF tickets and signed, verifiable QR codes.
- **Email processing** — a database-backed outbox with a background worker, retries with backoff and dead-letter handling.
- **Authentication and authorization** — customer and admin authentication, session hardening, roles and permissions.
- **Admin dashboard development** — KPIs, charts, date filtering and report exports.
- **Query optimization** — indexing, aggregation and avoiding unnecessary record loading for scalable analytics.
- **Testing** — writing automated tests covering logic, security, concurrency, exports and the full payment/email flows.
- **Deployment** — preparing a Python web service with a managed PostgreSQL database, static file handling and environment-based configuration.

## Conclusion

This internship provided practical experience in developing a complete movie-ticket-booking platform. Working through backend, frontend, database, payment, ticket generation, email, administration and deployment concepts, I built a system that covers the entire booking lifecycle — from discovering a movie and reserving seats to paying securely and receiving a verifiable ticket. The project strengthened my understanding of Django, database design, payment integration, concurrency and security, and gave me a solid foundation for future software development work.
