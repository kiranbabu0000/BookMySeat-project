"""Read-only audit of every uploaded-image reference in PostgreSQL.

Reports which ImageField values resolve to a real file in the ACTIVE storage
backend (local filesystem in development, Cloudinary when CLOUDINARY_URL is
set) and which are missing — e.g. posters lost to Render's ephemeral disk
before persistent storage was enabled.

This command NEVER modifies the database or any file. Missing records stay
intact so they can be re-uploaded through Django Admin.
"""
from django.core.management.base import BaseCommand

from admin_panel.models import CastMember, MovieImage
from movies.models import Movie

IMAGE_FIELDS = [
    ('Movie', 'image', 'poster'),
    ('Movie', 'thumbnail', 'thumbnail'),
    ('Movie', 'banner', 'banner'),
    ('CastMember', 'image', 'cast photo'),
    ('MovieImage', 'image', 'gallery image'),
]


class Command(BaseCommand):
    help = (
        'Audit all image references against the active storage backend. '
        'Read-only: prints missing files, changes nothing.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--json', action='store_true',
            help='Print machine-readable JSON instead of a table.',
        )

    def handle(self, *args, **options):
        from django.db import models as dj_models

        checks = [
            (Movie, 'image'), (Movie, 'thumbnail'), (Movie, 'banner'),
            (CastMember, 'image'), (MovieImage, 'image'),
        ]
        storage = None
        total = ok = missing = empty = 0
        missing_rows = []

        for model, field_name in checks:
            field = model._meta.get_field(field_name)
            storage = field.storage
            label = f'{model.__name__}.{field_name}'
            qs = model.objects.exclude(**{f'{field_name}__isnull': True}).exclude(
                **{field_name: ''}
            ).values_list('pk', field_name)
            for pk, name in qs:
                total += 1
                if storage.exists(name):
                    ok += 1
                else:
                    missing += 1
                    missing_rows.append({'model': label, 'pk': pk, 'name': name})

        backend = type(storage).__name__ if storage else 'n/a'
        if options['json']:
            import json
            self.stdout.write(json.dumps({
                'storage_backend': backend,
                'total': total, 'present': ok,
                'missing': missing,
                'missing_records': missing_rows,
            }, indent=2))
            return

        self.stdout.write(f'Storage backend: {backend}')
        self.stdout.write(
            f'Checked {total} image reference(s): {ok} present, {missing} MISSING.'
        )
        if missing:
            self.stdout.write('')
            self.stdout.write('Missing images (re-upload via Django Admin):')
            for row in missing_rows:
                self.stdout.write(f"  - {row['model']} #{row['pk']}: {row['name']}")
            self.stdout.write('')
            self.stdout.write(
                'Database rows were NOT modified. Re-upload the listed images '
                'through Django Admin; new uploads go to permanent storage.'
            )
