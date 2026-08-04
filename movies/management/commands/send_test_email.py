from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send a test email using the configured SMTP settings."

    def add_arguments(self, parser):
        parser.add_argument(
            "email",
            nargs="?",
            default=None,
            help="Recipient email address. Defaults to EMAIL_HOST_USER.",
        )

    def handle(self, *args, **options):
        to = options["email"] or settings.EMAIL_HOST_USER
        if not to:
            raise CommandError("No recipient given and EMAIL_HOST_USER is not set.")
        send_mail(
            subject="BookMySeat test email",
            message=(
                "This is a test email from BookMySeat. "
                "Your SMTP settings are working correctly!"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to],
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS(f"Test email sent to {to}"))
