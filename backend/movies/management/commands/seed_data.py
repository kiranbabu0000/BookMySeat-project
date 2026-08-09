from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from movies.models import Movie, SeatCategory, ShowPrice, Theater
from admin_panel.models import (
    Genre, Language, Theatre, Screen, Show, GSTSlab, PricingConfig, Coupon,
    CastMember, Trailer,
)
from admin_panel.services import sync_theater_from_show
from admin_panel.layouts import build_layout_spec, capacity_of, variant_for
from movies.poster_generator import generate_poster, generate_banner, generate_thumbnail

LEGACY_THEATRES = [
    'PVR Cinemas', 'INOX', 'AGS Cinemas', 'SPI Cinemas', 'Miraj Cinemas',
]

THEATRE_CONFIGS = [
    {'name': 'Rasi Cinemas', 'location': 'City Center Mall', 'city': 'Chennai',
     'address': 'Level 3, City Center Mall, Anna Nagar', 'contact': '044 4000 1101',
     'screens': [('Screen 1', 'imax'), ('Screen 2', 'medium'), ('Screen 3', 'large'), ('Screen 4', 'premium')]},
    {'name': 'Ragava Theater', 'location': 'Parrys Corner', 'city': 'Chennai',
     'address': '102 NSC Bose Road, George Town', 'contact': '044 4000 1102',
     'screens': [('Screen 1', 'large'), ('Screen 2', 'small'), ('Screen 3', 'medium')]},
    {'name': 'Dare Devil Cinemas', 'location': 'T. Nagar', 'city': 'Chennai',
     'address': '18 Usman Road, T. Nagar', 'contact': '044 4000 1103',
     'screens': [('Screen 1', 'premium'), ('Screen 2', 'medium'), ('Screen 3', 'small'), ('Screen 4', 'large')]},
    {'name': 'Singam Cinemas', 'location': 'Velachery', 'city': 'Chennai',
     'address': 'Phoenix Marketcity, Velachery', 'contact': '044 4000 1104',
     'screens': [('Screen 1', 'imax'), ('Screen 2', 'medium'), ('Screen 3', 'large')]},
    {'name': 'Rathnam Theater', 'location': 'Adyar', 'city': 'Chennai',
     'address': 'Lattice Bridge Road, Adyar', 'contact': '044 4000 1105',
     'screens': [('Screen 1', 'large'), ('Screen 2', 'premium'), ('Screen 3', 'medium'), ('Screen 4', 'small')]},
    {'name': 'Galaxy Cinemas', 'location': 'Andheri West', 'city': 'Mumbai',
     'address': 'Infiniti Mall, Andheri West', 'contact': '022 4000 2101',
     'screens': [('Screen 1', 'imax'), ('Screen 2', 'medium'), ('Screen 3', 'large'), ('Screen 4', 'premium')]},
    {'name': 'Novelty Cinema', 'location': 'Marine Lines', 'city': 'Mumbai',
     'address': 'Marine Lines, Mumbai Central', 'contact': '022 4000 2102',
     'screens': [('Screen 1', 'large'), ('Screen 2', 'small'), ('Screen 3', 'medium')]},
    {'name': 'City Lights Cinemas', 'location': 'Connaught Place', 'city': 'Delhi',
     'address': 'Block E, Connaught Place, New Delhi', 'contact': '011 4000 3101',
     'screens': [('Screen 1', 'premium'), ('Screen 2', 'medium'), ('Screen 3', 'large')]},
    {'name': 'Metro Screens', 'location': 'Karol Bagh', 'city': 'Delhi',
     'address': 'Ajmal Khan Road, Karol Bagh, New Delhi', 'contact': '011 4000 3102',
     'screens': [('Screen 1', 'medium'), ('Screen 2', 'small')]},
    {'name': 'Anand Theater', 'location': 'Koramangala', 'city': 'Bengaluru',
     'address': '5th Block, Koramangala, Bengaluru', 'contact': '080 4000 4101',
     'screens': [('Screen 1', 'large'), ('Screen 2', 'premium'), ('Screen 3', 'small')]},
    {'name': 'Royal Cinemas', 'location': 'Malleshwaram', 'city': 'Bengaluru',
     'address': '15th Cross, Malleshwaram, Bengaluru', 'contact': '080 4000 4102',
     'screens': [('Screen 1', 'imax'), ('Screen 2', 'medium')]},
    {'name': 'Raja Theater', 'location': 'Banjara Hills', 'city': 'Hyderabad',
     'address': 'Road No 12, Banjara Hills, Hyderabad', 'contact': '040 4000 5101',
     'screens': [('Screen 1', 'premium'), ('Screen 2', 'medium'), ('Screen 3', 'large')]},
    {'name': 'Megha Cinemas', 'location': 'Secunderabad', 'city': 'Hyderabad',
     'address': 'MG Road, Secunderabad', 'contact': '040 4000 5102',
     'screens': [('Screen 1', 'large'), ('Screen 2', 'small')]},
    {'name': 'Bina Cinemas', 'location': 'Park Street', 'city': 'Kolkata',
     'address': 'Park Street, Kolkata', 'contact': '033 4000 6101',
     'screens': [('Screen 1', 'imax'), ('Screen 2', 'premium')]},
    {'name': 'Prachi Cinema', 'location': 'Salt Lake', 'city': 'Kolkata',
     'address': 'Sector V, Salt Lake, Kolkata', 'contact': '033 4000 6102',
     'screens': [('Screen 1', 'medium'), ('Screen 2', 'small'), ('Screen 3', 'large')]},
    {'name': 'Parihar Cinemas', 'location': 'Kothrud', 'city': 'Pune',
     'address': 'Paud Road, Kothrud, Pune', 'contact': '020 4000 7101',
     'screens': [('Screen 1', 'premium'), ('Screen 2', 'large')]},
    {'name': 'Manas Cinemas', 'location': 'Viman Nagar', 'city': 'Pune',
     'address': 'Viman Nagar Road, Pune', 'contact': '020 4000 7102',
     'screens': [('Screen 1', 'medium'), ('Screen 2', 'imax')]},
]

SHOW_TIMES = ['10:30', '13:45', '18:30', '21:45']
SHOW_DAYS = 4

BASE_PRICES = {'small': Decimal('150'), 'medium': Decimal('190'),
               'large': Decimal('260'), 'imax': Decimal('360'), 'premium': Decimal('320')}
CATEGORY_MULTIPLIERS = {
    'Economy': Decimal('0.85'),
    'Standard': Decimal('1.00'),
    'Premium': Decimal('1.30'),
    'VIP': Decimal('1.65'),
}

MOVIES = [
    {
        'name': 'Spider-Man: Evolving',
        'tagline': 'Every choice changed everything.',
        'top': (122, 6, 18), 'bottom': (12, 12, 60), 'accent': (232, 30, 46),
        'rating': '8.7', 'imdb_rating': '8.2', 'duration': 145,
        'certificate': 'UA', 'director': 'Jon Watts', 'producer': 'Kevin Feige',
        'writer': 'Chris McKenna', 'music_director': 'Michael Giacchino',
        'cinematographer': 'Jasin Boland', 'production_company': 'Marvel Studios',
        'release_date': '2026-07-17', 'cast': 'Tom Holland, Zendaya, Benedict Cumberbatch, Florence Pugh',
        'description': ('Peter Parker is torn between his life as a student and his '
                        'responsibility as Spider-Man. When a multiversal anomaly threatens '
                        'to collapse every reality he has fought to protect, Peter must make '
                        'the hardest choice of his life and accept the consequences of being a hero.'),
        'story': ('A grieving Peter Parker is drawn back into action when fragments of '
                  'other realities begin leaking into his own. Racing against a collapsing '
                  'multiverse, he must reunite with old allies and face the enemies he has '
                  'left behind before every universe he loves is erased.'),
        'genres': ['Action', 'Sci-Fi'], 'languages': ['English'],
        'trailer': 'https://www.youtube.com/watch?v=xwAz_6BQn4c',
        'cast_members': [
            ('Tom Holland', 'Peter Parker / Spider-Man', 'hero'),
            ('Zendaya', 'MJ', 'heroine'),
            ('Benedict Cumberbatch', 'Doctor Strange', 'supporting'),
            ('Florence Pugh', 'Yelena Belova', 'supporting'),
        ],
    },
    {
        'name': 'Shadow Protocol',
        'tagline': 'Trust no one. Track everyone.',
        'top': (8, 30, 55), 'bottom': (2, 8, 20), 'accent': (240, 170, 20),
        'rating': '8.3', 'imdb_rating': '7.9', 'duration': 132,
        'certificate': 'A', 'director': 'Christopher McQuarrie', 'producer': 'Jerry Bruckheimer',
        'writer': 'Christopher McQuarrie', 'music_director': 'Lorne Balfe',
        'cinematographer': 'Rob Hardy', 'production_company': 'Skydance Media',
        'release_date': '2026-07-03', 'cast': 'Henry Cavill, Priyanka Chopra, Jason Statham, Ana de Armas',
        'description': ('An elite intelligence agent is framed for a strike he never carried out. '
                        'On the run from every agency he once served, he must untangle a global '
                        'conspiracy that reaches the highest corridors of power before the clock '
                        'runs out on millions of lives.'),
        'story': ('After a failed mission leaves him framed for treason, elite agent Alex Vane '
                  'becomes the world\u2019s most wanted man. With a ruthless handler closing in '
                  'and a traitor inside the agency, he has 72 hours to expose the truth or watch '
                  'the world burn.'),
        'genres': ['Action', 'Thriller'], 'languages': ['English'],
        'trailer': 'https://www.youtube.com/watch?v=xwAz_6BQn4c',
        'cast_members': [
            ('Henry Cavill', 'Alex Vane', 'hero'),
            ('Priyanka Chopra', 'Agent Nadia', 'heroine'),
            ('Jason Statham', 'Mercer', 'villain'),
            ('Ana de Armas', 'Elena', 'supporting'),
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed sample theatres, screens, shows, and seats for demo movies'

    def _seed_movies(self):
        from datetime import date
        for data in MOVIES:
            movie, created = Movie.objects.get_or_create(
                name=data['name'],
                defaults={
                    'status': 'now_showing',
                    'rating': Decimal(data['rating']),
                    'cast': data['cast'],
                    'duration': data['duration'],
                    'certificate': data['certificate'],
                    'release_date': date.fromisoformat(data['release_date']),
                },
            )
            movie.rating = Decimal(data['rating'])
            movie.imdb_rating = Decimal(data['imdb_rating'])
            movie.duration = data['duration']
            movie.certificate = data['certificate']
            movie.director = data['director']
            movie.producer = data['producer']
            movie.writer = data.get('writer')
            movie.music_director = data.get('music_director')
            movie.cinematographer = data.get('cinematographer')
            movie.production_company = data.get('production_company')
            movie.story = data.get('story')
            movie.cast = data['cast']
            movie.description = data['description']
            movie.status = 'now_showing'
            movie.show_on_homepage = True
            movie.is_deleted = False
            movie.release_date = date.fromisoformat(data['release_date'])
            if not movie.image:
                movie.image = generate_poster(
                    data['name'], data['tagline'], data['top'], data['bottom'], data['accent'])
            if not movie.thumbnail:
                movie.thumbnail = generate_thumbnail(
                    data['name'], data['top'], data['bottom'], data['accent'])
            if not movie.banner:
                movie.banner = generate_banner(
                    data['name'], data['tagline'], data['top'], data['bottom'], data['accent'])
            movie.save()
            for g in data['genres']:
                genre = Genre.objects.filter(name=g).first()
                if genre:
                    movie.genres.add(genre)
            for lang in data['languages']:
                language = Language.objects.filter(name=lang).first()
                if language:
                    movie.languages.add(language)
            if data.get('trailer') and not movie.trailers.filter(url=data['trailer']).exists():
                Trailer.objects.create(movie=movie, title=f'{data["name"]} - Official Trailer',
                                       url=data['trailer'], is_featured=True)
            for name, character, role in data['cast_members']:
                CastMember.objects.get_or_create(
                    movie=movie, name=name,
                    defaults={'character_name': character, 'role': role})
            self.stdout.write(self.style.SUCCESS(
                f'Movie {data["name"]} ' + ('created' if created else 'updated')))
        return Movie.objects.filter(status='now_showing').order_by('-release_date')

    def handle(self, *args, **options):
        today = timezone.now().date()
        show_dates = [today + timezone.timedelta(days=i) for i in range(SHOW_DAYS)]

        # --- Clean up legacy placeholder data (safe: no bookings exist) ---
        old_theatres = Theatre.objects.filter(name__in=LEGACY_THEATRES)
        self.stdout.write(self.style.WARNING(
            f'Removing {old_theatres.count()} legacy theatre rows and orphaned flow data'))
        old_theatres.delete()
        orphaned = Theater.objects.filter(admin_show__isnull=True, booking__isnull=True)
        self.stdout.write(self.style.WARNING(f'Removing {orphaned.count()} orphaned flow theaters'))
        orphaned.delete()

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

        # --- Seat categories (layout bands) ---
        for idx, name in enumerate(CATEGORY_MULTIPLIERS):
            SeatCategory.objects.get_or_create(
                name=name,
                defaults={'row_start': 'A', 'row_end': 'Z', 'display_order': idx},
            )
        self.stdout.write(self.style.SUCCESS(f'Seeded {len(CATEGORY_MULTIPLIERS)} seat categories'))

        # --- GST slabs, pricing config, demo coupon ---
        GSTSlab.objects.get_or_create(
            min_amount=Decimal('0.00'), max_amount=Decimal('100.00'),
            defaults={'rate': Decimal('12.00'), 'display_order': 1},
        )
        GSTSlab.objects.get_or_create(
            min_amount=Decimal('100.01'), max_amount=None,
            defaults={'rate': Decimal('18.00'), 'display_order': 2},
        )
        PricingConfig.objects.get_or_create(
            pk=1, defaults={
                'platform_fee_per_ticket': Decimal('5.00'),
                'misc_fee_per_booking': Decimal('2.50'),
            },
        )
        Coupon.objects.get_or_create(
            code='BMS100', defaults={
                'description': 'Flat \u20b9100 off on orders above \u20b9400',
                'discount_amount': Decimal('100.00'),
                'min_order_amount': Decimal('400.00'),
                'max_uses': 1000,
                'is_active': True,
                'valid_from': timezone.now() - timezone.timedelta(days=30),
                'valid_to': timezone.now() + timezone.timedelta(days=365),
            },
        )

        movies = self._seed_movies()
        self.stdout.write(self.style.SUCCESS(f'{movies.count()} movies now showing'))

        created_theatre_count = 0
        created_screen_count = 0
        created_show_count = 0
        created_theater_count = 0
        created_seat_count = 0

        for config in THEATRE_CONFIGS:
            theatre, t_created = Theatre.objects.get_or_create(
                name=config['name'],
                defaults={
                    'location': config['location'],
                    'city': config['city'],
                    'address': config.get('address', ''),
                    'contact': config.get('contact', ''),
                    'facilities': 'Dolby Atmos, Recliners, 4K Projection, Wheelchair Access, Snack Counter',
                    'is_active': True,
                },
            )
            if t_created:
                created_theatre_count += 1

            movie_list = list(movies)
            for screen_idx, (screen_name, size) in enumerate(config['screens']):
                spec = build_layout_spec(size, variant_for(screen_name))
                capacity = capacity_of(spec)
                screen, s_created = Screen.objects.get_or_create(
                    theatre=theatre,
                    name=screen_name,
                    defaults={
                        'capacity': capacity,
                        'size': size,
                        'rows': spec['rows'],
                        'cols_per_section': spec['cols_per_section'],
                        'layout_spec': spec,
                        'seat_layout': '',
                    },
                )
                if not s_created:
                    screen.size = size
                    screen.rows = spec['rows']
                    screen.cols_per_section = spec['cols_per_section']
                    screen.layout_spec = spec
                    screen.capacity = capacity
                    screen.save()
                else:
                    created_screen_count += 1

                movie = movie_list[screen_idx % len(movie_list)]
                base_price = BASE_PRICES[size]
                times = self._build_show_times(show_dates, screen_idx)
                for show_time in times:
                    show, sh_created = Show.objects.get_or_create(
                        movie=movie,
                        theatre=theatre,
                        screen=screen,
                        date=show_time.date(),
                        time=show_time.time(),
                        defaults={
                            'ticket_price': base_price,
                            'status': 'active',
                        },
                    )
                    if not sh_created:
                        Show.objects.filter(pk=show.pk).update(
                            movie=movie, ticket_price=base_price, status='active')
                    theater_created = show.theater_id is None
                    theater = sync_theater_from_show(show)
                    if theater_created:
                        created_theater_count += 1
                    if sh_created:
                        created_show_count += 1

                    self._seed_pricing(theater, base_price)

        created_seat_count = models_count_seats()
        self.stdout.write(self.style.SUCCESS(f'Seeded {created_theatre_count} new theatres'))
        self.stdout.write(self.style.SUCCESS(f'Seeded {created_screen_count} new screens'))
        self.stdout.write(self.style.SUCCESS(f'Seeded {created_show_count} new shows'))
        self.stdout.write(self.style.SUCCESS(f'Seeded {created_theater_count} new Theater entries'))
        self.stdout.write(self.style.SUCCESS(f'Seeded {created_seat_count} new seats'))
        self.stdout.write(self.style.SUCCESS('Seeding complete!'))

    def _build_show_times(self, show_dates, screen_idx):
        times = []
        for day_offset, day in enumerate(show_dates):
            if day_offset == 0:
                # Today: only upcoming showtimes.
                for time_str in SHOW_TIMES:
                    hour, minute = map(int, time_str.split(':'))
                    dt = timezone.make_aware(
                        timezone.datetime(day.year, day.month, day.day, hour, minute))
                    if dt > timezone.now():
                        times.append(dt)
            else:
                for time_str in SHOW_TIMES:
                    hour, minute = map(int, time_str.split(':'))
                    times.append(timezone.make_aware(
                        timezone.datetime(day.year, day.month, day.day, hour, minute)))
        # Slight stagger per screen so adjacent screens rarely collide.
        shift = (screen_idx % len(SHOW_TIMES))
        return times[shift:] + times[:shift]

    def _seed_pricing(self, theater, base_price):
        categories = {c.name: c for c in SeatCategory.objects.all()}
        for name, mult in CATEGORY_MULTIPLIERS.items():
            category = categories.get(name)
            if not category:
                continue
            price = (base_price * mult).quantize(Decimal('0.01'))
            ShowPrice.objects.get_or_create(
                theater=theater,
                category=category,
                defaults={'price': price},
            )


def models_count_seats():
    from movies.models import Seat
    return Seat.objects.filter(theater__admin_show__isnull=False).count()
