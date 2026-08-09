import secrets

from django.db import migrations, models


def backfill_booking_refs(apps, schema_editor):
    Booking = apps.get_model('movies', 'Booking')
    for booking in Booking.objects.all().order_by('id'):
        booking.booking_ref = 'BMS{:06d}'.format(booking.id)
        booking.save(update_fields=['booking_ref'])


class Migration(migrations.Migration):

    dependencies = [
        ('movies', '0007_booking_booking_ref_reservation_convenience_fee_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_booking_refs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='booking',
            name='booking_ref',
            field=models.CharField(max_length=20, unique=True, blank=True, editable=False),
        ),
    ]
