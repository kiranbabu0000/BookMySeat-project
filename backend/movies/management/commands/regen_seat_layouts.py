"""Regenerate seat layout specs and seat categories for every screen and show.

Picks a deterministic layout variant for each screen name and rebuilds the
stored ``layout_spec`` on every admin ``Screen`` and booking ``Theater`` row,
then re-materialises the seat grid (updating existing seats in place so booked
and reserved seats are preserved).

Usage::

    python manage.py regen_seat_layouts [--variant straight|curved|...]
"""
from django.core.management.base import BaseCommand, CommandError

from admin_panel.models import Screen
from admin_panel.layouts import LAYOUT_VARIANTS, build_layout_spec, variant_for
from admin_panel.services import _ensure_categories
from movies.models import Booking, ReservedSeat, Seat, SeatCategory, Theater

UPDATE_FIELDS = [
    'seat_type', 'category', 'row_label', 'row_idx', 'col_idx',
    'side', 'gap_before', 'is_best_view', 'couple_group',
]


def _rebuild_theater_seats(theater, spec):
    """Make a show's Seat rows match a layout spec, preserving bookings."""
    names = {s['category'] for s in spec['seats']}
    _ensure_categories(names)
    cats = {c.name: c for c in SeatCategory.objects.filter(name__in=names)}

    group = 0
    pair_lookup = {}
    for pair in spec.get('couple_pairs', []):
        group += 1
        for num in pair:
            pair_lookup[num] = group

    rows = {}
    for s in spec['seats']:
        rows[s['num']] = {
            'seat_type': s['type'],
            'category': cats.get(s['category']),
            'row_label': s['row'],
            'row_idx': s['r'],
            'col_idx': s['c'],
            'side': s['side'],
            'gap_before': s['gap_before'],
            'is_best_view': s['best_view'],
            'couple_group': pair_lookup.get(s['num'], 0),
        }

    existing = {seat.seat_number: seat for seat in Seat.objects.filter(theater=theater)}
    updates = []
    creates = []
    for num, vals in rows.items():
        seat = existing.get(num)
        if seat:
            for field, value in vals.items():
                setattr(seat, field, value)
            updates.append(seat)
        else:
            creates.append(Seat(theater=theater, seat_number=num, is_booked=False, **vals))

    protected = set(
        Booking.objects.filter(theater=theater).values_list('seat__seat_number', flat=True)
    )
    protected |= set(
        ReservedSeat.objects.filter(
            reservation__show=theater, reservation__status='active'
        ).values_list('seat__seat_number', flat=True)
    )
    orphans = [
        seat for num, seat in existing.items()
        if num not in rows and num not in protected
    ]
    Seat.objects.filter(pk__in=[s.pk for s in orphans]).delete()
    if updates:
        Seat.objects.bulk_update(updates, UPDATE_FIELDS)
    if creates:
        Seat.objects.bulk_create(creates, ignore_conflicts=True)


class Command(BaseCommand):
    help = 'Regenerate seat layout specs + seat categories for all screens and shows.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--variant', choices=LAYOUT_VARIANTS, default=None,
            help='Force a single layout variant for every screen (default: deterministic per screen name).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without writing to the database.',
        )

    def handle(self, *args, **options):
        forced = options.get('variant')
        dry = options.get('dry_run')
        if not dry:
            screens = list(Screen.objects.all())
            for screen in screens:
                size = screen.size if screen.size else 'small'
                variant = variant_for(screen.name, forced)
                spec = build_layout_spec(size, variant)
                screen.layout_spec = spec
                screen.rows = spec['rows']
                screen.cols_per_section = spec['cols_per_section']
                screen.save(update_fields=['layout_spec', 'rows', 'cols_per_section'])
                self.stdout.write(
                    'Screen {} {} -> {} ({} seats)'.format(
                        screen.id, screen.name, variant, len(spec['seats']))
                )

            theaters = Theater.objects.filter(status='active')
            for theater in theaters:
                size = (theater.layout_spec or {}).get('size', 'small')
                variant = variant_for(theater.screen_name, forced)
                spec = build_layout_spec(size, variant)
                _rebuild_theater_seats(theater, spec)
                theater.layout_spec = spec
                theater.save(update_fields=['layout_spec'])
                Theater.objects.filter(pk=theater.pk).update(
                    seat_revision=theater.seat_revision + 1
                )
                self.stdout.write(
                    'Theater {} {} -> {} ({} seats)'.format(
                        theater.id, theater.screen_name, variant, len(spec['seats']))
                )
            self.stdout.write(self.style.SUCCESS(
                'Regenerated {} screens and {} active shows.'.format(
                    len(screens), theaters.count()))
            )
        else:
            screens = list(Screen.objects.all())
            self.stdout.write(
                'Would update {} screens and {} active shows.'.format(
                    len(screens),
                    Theater.objects.filter(status='active').count(),
                )
            )
