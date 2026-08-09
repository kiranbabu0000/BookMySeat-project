# BookMySeat

Online movie ticket booking platform built with Django (server-rendered).

## Project structure

The project is split into a separated **frontend** (templates + static assets)
and **backend** (Django application).

```
BookMySeat/
│
├── frontend/
│   ├── templates/            # Django templates (base, movies, users, admin, partials, emails)
│   └── static/               # CSS, JS, images (css/, js/, img/, admin/)
│
├── backend/
│   ├── manage.py
│   ├── requirements.txt
│   ├── .env                  # local secrets (gitignored)
│   ├── bookmyseat/           # Django project settings/urls/wsgi/asgi
│   ├── movies/               # app: models, views, payments, services, qr, pdf
│   ├── users/                # app: auth, OTP, forms, views
│   ├── admin_panel/          # app: admin CMS, analytics, layouts
│   ├── media/                # user-uploaded posters/banners/thumbnails
│   └── db.sqlite3            # local SQLite database
│
├── .gitignore
├── vercel.json
└── README.md
```

## Running locally

```bash
cd backend
pip install -r requirements.txt
python manage.py runserver
```

Then open http://127.0.0.1:8000

## Notes

- `backend/bookmyseat/settings.py` points Django's template loader and static
  finder at `frontend/templates` and `frontend/static` via `FRONTEND_DIR`.
- Media uploads are stored under `backend/media` (`MEDIA_ROOT = BASE_DIR/media`).
- Local secrets live in `backend/.env` (loaded explicitly by settings).
- Optional commands:
  - `python manage.py seed_data` — seed the database
  - `python manage.py seed_events` — seed event categories
  - `python manage.py process_email_outbox` — flush queued emails
  - `python manage.py release_expired_reservations` — free expired holds
  - `python manage.py test` — run the test suite
