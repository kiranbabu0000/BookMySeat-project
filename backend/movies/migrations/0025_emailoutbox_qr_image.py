"""Add EmailOutbox.qr_image (inline QR PNG for cid:qr_ticket).

Gmail and most webmail clients block ``data:`` URIs inside ``<img>`` tags, so
the ticket QR was invisible in delivered emails. The QR is now stored as a PNG
blob and attached inline with a Content-ID so it renders reliably.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('movies', '0024_emailoutbox'),
    ]

    operations = [
        migrations.AddField(
            model_name='emailoutbox',
            name='qr_image',
            field=models.BinaryField(
                blank=True,
                help_text='Ticket QR PNG (image/png) embedded inline as cid:qr_ticket',
                null=True,
            ),
        ),
    ]
