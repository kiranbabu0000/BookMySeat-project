from django.core.management.base import BaseCommand

from movies.services import release_expired_reservations


class Command(BaseCommand):
    help = (
        'Release all expired seat reservations. Schedule this periodically '
        '(e.g. cron every minute) in production; user-facing requests also '
        'perform lazy expiry so no continuous polling is required.'
    )

    def handle(self, *args, **options):
        count = release_expired_reservations()
        if count:
            self.stdout.write(self.style.SUCCESS(f'Released {count} expired reservation(s).'))
        else:
            self.stdout.write('No expired reservations to release.')
