from django.core.management.base import BaseCommand
from django.utils import timezone
from movies.models import Movie, Theater, Seat
from admin_panel.models import Genre, Language, Theatre, Screen, Show
import random


class Command(BaseCommand):
    help = 'Seed sample theatres, screens, shows, and seats for now-showing movies'

    def handle(self, *args, **options):
        today = timezone.now().date()

        # --- Genres ---
        genre_names = ['Action', 'Comedy', 'Drama', 'Horror', 'Romance', 'Sci-Fi', 'Thriller', 'Animation']
        for name in genre_names:
            Genre.objects.get_or_create(name=name, defaults={'slug': name.lower().replace(' ', '-')})
        self.stdout.write(self.style.SUCCESS(f'Seeded {len(genre_names)} genres'))

        # --- Languages ---
        lang_data = [('English', 'EN'), ('Hindi', 'HI'), ('Tamil', 'TA'), ('Telugu', 'TE'), ('Malayalam', 'ML')]
        for name, code in lang_data:
            Language.objects.get_or_create(name=name, defaults={'code': code})
        self.stdout.write(self.style.SUCCESS(f'Seeded {len(lang_data)} languages'))

        # --- Theatres ---
        theatre_configs = [
            {'name': 'PVR Cinemas', 'location': 'City Center Mall', 'city': 'Chennai',
             'screens': [{'name': 'Screen 1', 'size': 'small'}, {'name': 'Screen 2', 'size': 'small'}, {'name': 'Screen 3', 'size': 'large'}]},
            {'name': 'INOX', 'location': 'Phoenix Market City', 'city': 'Chennai',
             'screens': [{'name': 'Screen 1', 'size': 'small'}, {'name': 'Screen 2', 'size': 'large'}]},
            {'name': 'AGS Cinemas', 'location': 'Villivakkam', 'city': 'Chennai',
             'screens': [{'name': 'Screen 1', 'size': 'small'}, {'name': 'Screen 2', 'size': 'small'}, {'name': 'Screen 3', 'size': 'large'}]},
            {'name': 'SPI Cinemas', 'location': 'Sathyam', 'city': 'Chennai',
             'screens': [{'name': 'Screen 1', 'size': 'small'}, {'name': 'Screen 2', 'size': 'large'}]},
            {'name': 'Miraj Cinemas', 'location': 'VR Mall', 'city': 'Chennai',
             'screens': [{'name': 'Screen 1', 'size': 'small'}, {'name': 'Screen 2', 'size': 'small'}, {'name': 'Screen 3', 'size': 'large'}]},
        ]
        show_times = ['09:00', '12:30', '15:45', '19:00', '22:15']

        created_theatre_count = 0
        created_screen_count = 0
        created_show_count = 0
        created_theater_count = 0
        created_seat_count = 0

        movies = list(Movie.objects.filter(status='now_showing'))
        if not movies:
            movies = list(Movie.objects.all()[:3])

        for config in theatre_configs:
            theatre, t_created = Theatre.objects.get_or_create(
                name=config['name'],
                defaults={
                    'location': config['location'],
                    'city': config['city'],
                    'is_active': True,
                }
            )
            if t_created:
                created_theatre_count += 1

            for screen_cfg in config['screens']:
                screen_name = screen_cfg['name']
                is_large = screen_cfg['size'] == 'large'
                capacity = 600 if is_large else 300
                screen, s_created = Screen.objects.get_or_create(
                    theatre=theatre,
                    name=screen_name,
                    defaults={'capacity': capacity, 'seat_layout': ''}
                )
                if s_created:
                    created_screen_count += 1

                for movie in movies:
                    for time_str in show_times:
                        hour, minute = map(int, time_str.split(':'))
                        show_datetime = timezone.make_aware(
                            timezone.datetime(today.year, today.month, today.day, hour, minute)
                        )
                        if show_datetime < timezone.now():
                            continue
                        show, sh_created = Show.objects.get_or_create(
                            movie=movie,
                            theatre=theatre,
                            screen=screen,
                            date=today,
                            time=show_datetime.time(),
                            defaults={
                                'ticket_price': random.choice([120, 150, 180, 220, 250]),
                                'status': 'active',
                            }
                        )
                        if sh_created:
                            created_show_count += 1

        # Create Theater (movies.models) entries with seats for booking flow
        # Only create a limited set per movie for performance
        for movie in movies[:3]:
            for time_str in show_times[:3]:
                for config in theatre_configs[:3]:
                    hour, minute = map(int, time_str.split(':'))
                    show_datetime = timezone.make_aware(
                        timezone.datetime(today.year, today.month, today.day, hour, minute)
                    )
                    if show_datetime < timezone.now():
                        continue
                    theater_name = config['name']
                    theater_obj, th_created = Theater.objects.get_or_create(
                        name=theater_name,
                        movie=movie,
                        time=show_datetime,
                    )
                    if th_created:
                        created_theater_count += 1
                        is_large = config['screens'][-1]['size'] == 'large'
                        rows = 20 if is_large else 15
                        seats_per_row = 30 if is_large else 20
                        seats_to_create = []
                        for r in range(1, rows + 1):
                            row_label = chr(64 + r) if r <= 26 else f'R{r}'
                            for s in range(1, seats_per_row + 1):
                                seats_to_create.append(
                                    Seat(theater=theater_obj, seat_number=f'{row_label}{s}', is_booked=False)
                                )
                        Seat.objects.bulk_create(seats_to_create, ignore_conflicts=True)
                        created_seat_count += len(seats_to_create)

        self.stdout.write(self.style.SUCCESS(f'Seeded {created_theatre_count} new theatres'))
        self.stdout.write(self.style.SUCCESS(f'Seeded {created_screen_count} new screens'))
        self.stdout.write(self.style.SUCCESS(f'Seeded {created_show_count} new shows'))
        self.stdout.write(self.style.SUCCESS(f'Seeded {created_theater_count} new Theater entries'))
        self.stdout.write(self.style.SUCCESS(f'Seeded {created_seat_count} new seats'))
        self.stdout.write(self.style.SUCCESS('Seeding complete!'))
