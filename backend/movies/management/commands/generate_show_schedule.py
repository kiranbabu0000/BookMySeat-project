"""Roll the show schedule forward so the next few days always have theaters.

Calendar days pass but seeded shows don't move, so the rolling 4-day date tabs
on the booking pages would eventually run out of shows. This command re-applies
each scheduled movie's most recent daily slate onto the freshly appearing days
(and is safe to run repeatedly — it never duplicates existing shows).

Run it on a schedule (e.g. a daily cron / Render scheduled job) or use the
lazy hook that runs automatically when the theater_list page is visited.
"""
from django.core.management.base import BaseCommand

from movies.models import Movie
from admin_panel.models import Show
from admin_panel.services import (
    SCHEDULE_HORIZON_DAYS,
    ensure_movie_schedule,
    ensure_rolling_schedule,
)


class Command(BaseCommand):
    help = 'Generate shows for the next N days, rolling the schedule forward.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--horizon', type=int, default=SCHEDULE_HORIZON_DAYS,
            help='Number of days (starting today) that should carry shows.',
        )
        parser.add_argument(
            '--movie', type=int, default=None,
            help='Limit the run to a single movie id.',
        )

    def handle(self, *args, **options):
        horizon = max(1, options['horizon'])
        movie_id = options['movie']
        if movie_id is not None:
            movie = Movie.objects.get(pk=movie_id)
            created = ensure_movie_schedule(movie, horizon)
            self.stdout.write(self.style.SUCCESS(
                f'Movie "{movie.name}": created {created} new shows.'))
            return

        total = ensure_rolling_schedule(horizon)
        movie_count = (
            Show.objects.filter(status='active')
            .values('movie_id').distinct().count()
        )
        self.stdout.write(self.style.SUCCESS(
            f'Created {total} new shows across {movie_count} movies '
            f'for the next {horizon} days.'))
