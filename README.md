# BookMySeat

Online movie ticket booking platform built with Django (server-rendered). Customers can discover movies, select seats on a live seat map, pay through Razorpay, and receive PDF/QR tickets by email — while administrators manage content and monitor the business through an analytics dashboard.

## Features

- Movie discovery with search, filters, sorting and personalised recommendations
- Movie details with trailers, gallery, cast, reviews and ratings
- Theater and show selection
- Live seat selection with temporary reservation holds
- Razorpay payment with verification, webhooks, retries and refunds
- PDF/QR M-ticket generation and email confirmation
- Booking history in the user profile
- Admin portal for movies, shows, seats, bookings, reviews and notifications
- Analytics dashboard with revenue, bookings, occupancy and refund reporting
- Responsive, mobile-friendly interface

## Technology Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.13, Django 6.0.7 |
| Frontend | HTML5, CSS3, JavaScript, Bootstrap 5 |
| Database | SQLite (local), PostgreSQL (production) |
| Payments | Razorpay |
| PDF / QR | ReportLab, qrcode |
| Email | SMTP or Brevo API via a database-backed outbox worker |
| Deployment | Render, Vercel |

## Project Structure

```
BookMySeat/
├── frontend/                # Django templates and static assets
├── backend/
│   ├── bookmyseat/          # Django project settings/urls/wsgi
│   ├── movies/              # movies, seats, reservations, payments, tickets, email outbox
│   ├── users/               # customer auth, OTP, home, profile
│   ├── admin_panel/         # admin portal and analytics
│   └── manage.py
├── render.yaml
├── vercel.json
└── README.md
```

## Installation

1. Clone the repository:

   ```bash
   git clone <repository-url>
   cd BookMySeat
   ```

2. Create and activate a virtual environment:

   ```bash
   python -m venv venv
   venv\Scripts\activate        # Windows
   source venv/bin/activate     # Linux / macOS
   ```

3. Install dependencies:

   ```bash
   cd backend
   pip install -r requirements.txt
   ```

4. Configure your environment file:

   ```bash
   copy .env.example .env       # Windows
   cp .env.example .env         # Linux / macOS
   ```

   Fill in the values you need (see below).

5. Apply database migrations:

   ```bash
   python manage.py migrate
   ```

6. (Optional) Seed sample data:

   ```bash
   python manage.py seed_data
   python manage.py seed_events
   ```

## Environment Variables

Configuration is read from `.env` (local development) or from environment variables on the hosting platform.

**Django**
- `DJANGO_DEBUG` — `True` in development, `False` in production
- `DJANGO_SECRET_KEY` — Django secret key (required in production)
- `DJANGO_ALLOWED_HOSTS` — comma-separated allowed hosts

**Database**
- `DATABASE_URL` — PostgreSQL connection string (omit to use local SQLite)
- `DATABASE_SSL_REQUIRE` — whether the Postgres connection requires SSL

**Payment**
- `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` — Razorpay API keys
- `RAZORPAY_WEBHOOK_SECRET` — secret used to verify Razorpay webhooks
- `RAZORPAY_DEMO_MODE` — `True` simulates payments locally without real keys

**Email**
- `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` — SMTP settings (leave `EMAIL_HOST` empty to print emails to the console in development)
- `DEFAULT_FROM_EMAIL` — sender address
- `BREVO_API_KEY` — optional; when set, emails are sent via Brevo's HTTPS API instead of SMTP

> Administrative access credentials are provided separately for evaluation purposes.

## Running the Project

```bash
cd backend
python manage.py runserver
```

Then open http://127.0.0.1:8000

The runserver command auto-starts the background email worker, so booking confirmation emails are sent without any extra setup.

## Testing

```bash
cd backend
python manage.py test
```

## Deployment

The repository includes configuration for two platforms:

- **Render** — a `render.yaml` blueprint provisions a Python web service and a managed PostgreSQL database.
- **Vercel** — a `vercel.json` deploys the Django WSGI app.

Set the environment variables listed above on the hosting platform before deploying.

## Internship Project

This project was developed as part of an internship and contains the six assigned tasks covering movie management, seat reservation, payments, the admin dashboard, movie discovery, and ticket/email automation.

[View Internship Report](REPORT.md)
