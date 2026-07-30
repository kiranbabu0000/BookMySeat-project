from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User

from .models import Movie


class MovieRestoreVisibilityTests(TestCase):
    def test_restoring_archived_movie_reenables_public_visibility(self):
        image = SimpleUploadedFile(
            'poster.jpg',
            BytesIO(b'fake-image-data').getvalue(),
            content_type='image/jpeg'
        )
        movie = Movie.objects.create(
            name='Restore Test Movie',
            image=image,
            rating=7.5,
            cast='Actor',
            duration=120,
            status='archived',
            show_on_homepage=False,
            is_deleted=True,
        )

        user = User.objects.create_superuser('admin', 'admin@example.com', 'password123')
        self.client.force_login(user)
        session = self.client.session
        session['is_admin_authenticated'] = True
        session.save()

        response = self.client.post(reverse('admin_movie_restore', args=[movie.pk]))

        movie.refresh_from_db()
        self.assertFalse(movie.is_deleted)
        self.assertTrue(movie.show_on_homepage)
        self.assertNotIn(movie.status, ['archived', 'hidden'])
        self.assertRedirects(response, reverse('admin_movie_list'))
