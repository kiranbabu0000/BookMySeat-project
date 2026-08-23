"""Copy existing locally-stored media to the active persistent storage.

Run ON Render (where the still-valid local uploads live) after enabling
Cloudinary:

    python manage.py migrate_media_to_cloudinary            # dry run
    python manage.py migrate_media_to_cloudinary --commit   # apply

For every ImageField row whose file is MISSING from active storage
(Cloudinary) but EXISTS on the local filesystem under MEDIA_ROOT, the local
file is uploaded and the row's path updated to whatever name persistent
storage assigned. Rows already present in Cloudinary are skipped; rows with
no file anywhere are reported as lost and left untouched for re-upload via
Django Admin.

Safety:
- Dry-run by default; nothing changes without --commit.
- Never deletes database rows.
- Local files are kept unless --delete-local is passed explicitly.
- A failure while uploading one image leaves that row untouched.
"""
from django.core.management.base import BaseCommand, CommandError

from admin_panel.models import CastMember, MovieImage
from movies.models import Movie

CHECKS = [
    (Movie, 'image'), (Movie, 'thumbnail'), (Movie, 'banner'),
    (CastMember, 'image'), (MovieImage, 'image'),
]


class Command(BaseCommand):
    help = (
        'Upload existing MEDIA_ROOT files into the active persistent '
        '(Cloudinary) storage and repoint DB references. Non-destructive: '
        'dry-run unless --commit is given.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit', action='store_true',
            help='Actually upload files and update rows (default: dry run).',
        )
        parser.add_argument(
            '--delete-local', action='store_true',
            help='After a successful commit-upload, delete the local copy.',
        )

    def handle(self, *args, **options):
        import os

        from django.conf import settings
        from django.core.files.base import File
        from django.core.files.storage import FileSystemStorage, default_storage

        if not options['commit'] and options['delete_local']:
            raise CommandError('--delete-local requires --commit.')

        cloud = default_storage
        if isinstance(cloud, FileSystemStorage):
            raise CommandError(
                'Active storage is FileSystemStorage. Set CLOUDINARY_URL '
                '(persistent storage) before migrating.'
            )

        media_root = settings.MEDIA_ROOT
        migrated = skipped_cloud = lost = failed = 0
        lost_rows, failed_rows = [], []

        for model, field_name in CHECKS:
            label = f'{model.__name__}.{field_name}'
            qs = (
                model.objects.exclude(**{f'{field_name}__isnull': True})
                .exclude(**{field_name: ''})
            )
            for obj in qs:
                name = getattr(obj, field_name).name
                if not name:
                    continue
                if cloud.exists(name):
                    skipped_cloud += 1
                    continue
                local_path = os.path.join(media_root, name.replace('/', os.sep))
                if not os.path.isfile(local_path):
                    lost += 1
                    lost_rows.append(f'{label} #{obj.pk}: {name}')
                    continue
                self.stdout.write(
                    f'{"[dry-run] " if not options["commit"] else ""}Migrating '
                    f'{label} #{obj.pk}: {name}'
                )
                if not options['commit']:
                    migrated += 1
                    continue
                try:
                    with open(local_path, 'rb') as fh:
                        new_name = cloud.save(name, File(fh))
                    setattr(obj, field_name, new_name)
                    obj.save(update_fields=[field_name])
                    migrated += 1
                    if options['delete_local']:
                        try:
                            os.remove(local_path)
                        except OSError:
                            self.stdout.write(
                                f'  Could not delete local copy: {local_path}'
                            )
                except Exception as exc:  # noqa: BLE001 — report and continue
                    failed += 1
                    failed_rows.append(f'{label} #{obj.pk}: {name} ({exc})')

        mode = 'COMMITTED' if options['commit'] else 'DRY RUN'
        self.stdout.write('')
        self.stdout.write(f'[{mode}] migrated={migrated} '
                          f'already_in_cloud={skipped_cloud} '
                          f'lost(no file anywhere)={lost} failed={failed}')
        if lost_rows:
            self.stdout.write('Lost images (cannot be recovered automatically):')
            for row in lost_rows:
                self.stdout.write(f'  - {row}')
        if failed_rows:
            self.stdout.write('Failures:')
            for row in failed_rows:
                self.stdout.write(f'  - {row}')
