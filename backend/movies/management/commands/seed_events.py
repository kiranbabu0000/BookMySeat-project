from decimal import Decimal
from datetime import date

from django.core.management.base import BaseCommand
from django.utils import timezone

from movies.models import Movie, SeatCategory, ShowPrice, Theater
from admin_panel.models import (
    Genre, Language, Theatre, Screen, Show,
)
from admin_panel.services import sync_theater_from_show
from admin_panel.layouts import build_layout_spec, capacity_of, variant_for
from movies.poster_generator import generate_poster, generate_banner, generate_thumbnail

SHOW_DAYS = 4

CATEGORY_MULTIPLIERS = {
    'Economy': Decimal('0.85'),
    'Standard': Decimal('1.00'),
    'Premium': Decimal('1.30'),
    'VIP': Decimal('1.65'),
}

EVENTS = [
    {
        'name': 'GenZ Dad + Boomer Son',
        'tagline': 'A father-son comedy roast for every generation.',
        'category': 'laughing_therapy',
        'genre': 'Laughing Therapy',
        'languages': ['Tamil'],
        'top': (20, 20, 40), 'bottom': (120, 40, 60), 'accent': (232, 30, 46),
        'rating': '8.9', 'imdb_rating': '8.5', 'duration': 120,
        'certificate': 'U', 'cast': 'Daddy DK, Sonu S', 'release_date': '2026-08-01',
        'director': 'Stand-Up Slam Productions', 'producer': 'Laugh Riot Live',
        'description': ('The internet meets the living room. A GenZ dad and his Boomer son swap '
                        'life advice, roast each other\'s playlists and discover that growing up '
                        'is just a slow, funny process of becoming your parents.'),
        'story': ('Two generations. One stage. Zero filter. From WhatsApp forwards to Reels, '
                  'this side-splitting laugh-therapy session turns family feuds into the '
                  'funniest 2 hours of your month.'),
        'venue': {
            'name': 'Laugh Riot Comedy Club',
            'location': 'Anna Nagar', 'city': 'Chennai',
            'address': 'Tower 2, VR Mall, Anna Nagar, Chennai',
            'contact': '044 4000 8801',
            'screen_name': 'Main Comedy Hall', 'size': 'medium',
        },
        'base_price': Decimal('499.00'),
        'show_times': ['19:30', '21:30'],
    },
    {
        'name': 'ImagiNesan',
        'tagline': 'A live stand-up show by Nesan David.',
        'category': 'laughing_therapy',
        'genre': 'Laughing Therapy',
        'languages': ['Tamil'],
        'top': (40, 12, 40), 'bottom': (10, 10, 50), 'accent': (240, 170, 20),
        'rating': '9.1', 'imdb_rating': '8.8', 'duration': 100,
        'certificate': 'U', 'cast': 'Nesan David', 'release_date': '2026-08-03',
        'director': 'Nesan David Live', 'producer': 'Comedy Central Live',
        'description': ('Nesan David imagines a world where everyone is just a little bit '
                        'more like him. Expect sharp observations on Chennai life, unrealistic '
                        'dating scenarios and an imagination you can\'t un-hear.'),
        'story': ('A brand-new hour of stand-up from one of the sharpest young comics in the '
                  'country. Fresh bits, crowd banter and an imagination that refuses to sit '
                  'down.'),
        'venue': {
            'name': 'Comedy Central Live',
            'location': 'T. Nagar', 'city': 'Chennai',
            'address': 'Ramee Mall, T. Nagar, Chennai',
            'contact': '044 4000 8802',
            'screen_name': 'Black Box Stage', 'size': 'small',
        },
        'base_price': Decimal('599.00'),
        'show_times': ['20:00'],
    },
    {
        'name': 'Vijay Antony Live',
        'tagline': 'A night of thundering hits, live.',
        'category': 'live_concert',
        'genre': 'Live Concert',
        'languages': ['Tamil'],
        'top': (8, 20, 45), 'bottom': (2, 4, 15), 'accent': (232, 30, 46),
        'rating': '9.0', 'imdb_rating': '8.6', 'duration': 150,
        'certificate': 'U', 'cast': 'Vijay Antony', 'release_date': '2026-08-05',
        'music_director': 'Vijay Antony', 'producer': 'Anima Music Live',
        'description': ('The voice behind a generation of chart-toppers takes the stage. '
                        'Expect a high-energy setlist of the biggest Tamil hits, live band, '
                        'stunning lights and a crowd that knows every word.'),
        'story': ('An electrifying live concert experience with Vijay Antony and his full '
                  'band. Sing along to your favourites and dance till the last encore.'),
        'venue': {
            'name': 'Anima Music Arena',
            'location': 'ECR', 'city': 'Chennai',
            'address': 'East Coast Road, Chennai',
            'contact': '044 4000 8803',
            'screen_name': 'Open Air Concert Ground', 'size': 'large',
        },
        'base_price': Decimal('1499.00'),
        'show_times': ['19:00'],
    },
    {
        'name': 'Santhosh Narayanan Live',
        'tagline': 'Neo-urban sound, cinematic soul.',
        'category': 'live_concert',
        'genre': 'Live Concert',
        'languages': ['Tamil'],
        'top': (12, 40, 55), 'bottom': (2, 6, 20), 'accent': (38, 139, 210),
        'rating': '9.2', 'imdb_rating': '8.9', 'duration': 140,
        'certificate': 'U', 'cast': 'Santhosh Narayanan', 'release_date': '2026-08-06',
        'music_director': 'Santhosh Narayanan', 'producer': 'Gradient Sound Live',
        'description': ('From film scores to indie anthems, Santhosh Narayanan delivers a '
                        'genre-bending live show. Groove to neo-urban sounds, cinematic '
                        'melodies and the unmistakable beat of his live band.'),
        'story': ('An immersive concert blending funk, electronic and Tamil film music. '
                  'Santhosh Narayanan and his collective redefine what a live gig feels '
                  'like in Chennai.'),
        'venue': {
            'name': 'Gradient Sound Stage',
            'location': 'Koyambedu', 'city': 'Chennai',
            'address': 'OMR Track, Koyambedu, Chennai',
            'contact': '044 4000 8804',
            'screen_name': 'Main Concert Stage', 'size': 'medium',
        },
        'base_price': Decimal('1299.00'),
        'show_times': ['20:30'],
    },
]


class Command(BaseCommand):
    help = 'Seed sample live events (laughing therapy + live concerts) with bookable shows and seats'

    def _seed_event(self, data, show_dates):
        release_date = date.fromisoformat(data['release_date'])
        movie, created = Movie.objects.get_or_create(
            name=data['name'],
            defaults={
                'category': data['category'],
                'status': 'now_showing',
                'rating': Decimal(data['rating']),
                'imdb_rating': Decimal(data['imdb_rating']),
                'duration': data['duration'],
                'certificate': data['certificate'],
                'cast': data['cast'],
                'release_date': release_date,
                'description': data['description'],
                'story': data['story'],
                'director': data.get('director'),
                'producer': data.get('producer'),
                'music_director': data.get('music_director'),
            },
        )
        movie.category = data['category']
        movie.status = 'now_showing'
        movie.show_on_homepage = True
        movie.is_deleted = False
        movie.rating = Decimal(data['rating'])
        movie.imdb_rating = Decimal(data['imdb_rating'])
        movie.duration = data['duration']
        movie.certificate = data['certificate']
        movie.cast = data['cast']
        movie.release_date = release_date
        movie.description = data['description']
        movie.story = data['story']
        movie.director = data.get('director')
        movie.producer = data.get('producer')
        movie.music_director = data.get('music_director')
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

        genre = Genre.objects.filter(name=data['genre']).first()
        if genre:
            movie.genres.add(genre)
        for lang_name in data['languages']:
            language = Language.objects.filter(name=lang_name).first()
            if language:
                movie.languages.add(language)

        vc = data['venue']
        theatre, _ = Theatre.objects.get_or_create(
            name=vc['name'],
            defaults={
                'location': vc['location'],
                'city': vc['city'],
                'address': vc['address'],
                'contact': vc['contact'],
                'facilities': 'Acoustic Sound, Ambient Lighting, Standing & Seated Zones, Wheelchair Access',
                'is_active': True,
            },
        )
        spec = build_layout_spec(vc['size'], variant_for(vc['screen_name']))
        screen, _ = Screen.objects.get_or_create(
            theatre=theatre,
            name=vc['screen_name'],
            defaults={
                'capacity': capacity_of(spec),
                'size': vc['size'],
                'rows': spec['rows'],
                'cols_per_section': spec['cols_per_section'],
                'layout_spec': spec,
                'seat_layout': '',
            },
        )
        if not screen.layout_spec:
            screen.layout_spec = spec
            screen.capacity = capacity_of(spec)
            screen.rows = spec['rows']
            screen.cols_per_section = spec['cols_per_section']
            screen.size = vc['size']
            screen.save()

        for show_time in self._build_show_times(show_dates, data['show_times']):
            show, _ = Show.objects.get_or_create(
                movie=movie,
                theatre=theatre,
                screen=screen,
                date=show_time.date(),
                time=show_time.time(),
                defaults={'ticket_price': data['base_price'], 'status': 'active'},
            )
            Show.objects.filter(pk=show.pk).update(
                movie=movie, ticket_price=data['base_price'], status='active')
            theater = sync_theater_from_show(show)
            self._seed_pricing(theater, data['base_price'])

        self.stdout.write(self.style.SUCCESS(
            f'Event {data["name"]} ' + ('created' if created else 'updated')))

    def _build_show_times(self, show_dates, time_specs):
        times = []
        for day in show_dates:
            for time_str in time_specs:
                hour, minute = map(int, time_str.split(':'))
                dt = timezone.make_aware(
                    timezone.datetime(day.year, day.month, day.day, hour, minute))
                if day == show_dates[0] and dt <= timezone.now():
                    continue
                times.append(dt)
        return times

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

    def handle(self, *args, **options):
        today = timezone.now().date()
        show_dates = [today + timezone.timedelta(days=i) for i in range(SHOW_DAYS)]

        for name in ('Laughing Therapy', 'Live Concert'):
            Genre.objects.get_or_create(
                name=name, defaults={'slug': name.lower().replace(' ', '-')})

        for data in EVENTS:
            self._seed_event(data, show_dates)

        total = Movie.objects.filter(category__in=['laughing_therapy', 'live_concert']).count()
        self.stdout.write(self.style.SUCCESS(f'{total} events are now bookable'))
