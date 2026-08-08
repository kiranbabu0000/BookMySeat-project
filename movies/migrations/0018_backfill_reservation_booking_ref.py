import secrets
import string

from django.db import migrations


def _make_ref(model, charset=string.ascii_uppercase + string.digits):
    for _ in range(200):
        ref = 'BMS' + ''.join(secrets.choice(charset) for _ in range(8))
        if not model.objects.filter(booking_ref=ref).exists():
            return ref
    raise RuntimeError('Could not allocate a unique booking reference.')


def backfill_reservation_booking_refs(apps, schema_editor):
    Reservation = apps.get_model('movies', 'Reservation')
    Booking = apps.get_model('movies', 'Booking')
    for reservation in Reservation.objects.filter(booking_ref__isnull=True).order_by('created_at'):
        bookings = list(
            Booking.objects.filter(reservation=reservation).order_by('id')
        )
        if not bookings:
            continue
        taken = {
            ref
            for ref in Reservation.objects.exclude(pk=reservation.pk).values_list(
                'booking_ref', flat=True
            )
            if ref
        }
        candidate = bookings[0].booking_ref
        if candidate and candidate not in taken:
            reservation.booking_ref = candidate
        else:
            reservation.booking_ref = _make_ref(Reservation)
        reservation.ticket_count = len(bookings)
        reservation.save(update_fields=['booking_ref', 'ticket_count'])


def reverse_backfill(apps, schema_editor):
    Reservation = apps.get_model('movies', 'Reservation')
    Reservation.objects.update(booking_ref=None, ticket_count=0)


class Migration(migrations.Migration):

    dependencies = [
        ('movies', '0017_reservation_booking_ref_reservation_ticket_count'),
    ]

    operations = [
        migrations.RunPython(
            backfill_reservation_booking_refs,
            reverse_backfill,
        ),
    ]
