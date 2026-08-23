"""Media persistence tests.

Covers the production fix for posters/banners disappearing on Render:
storage backend selection, the Cloudinary storage contract, upload
round-trips through the real admin MovieForm, missing-image fallback
logging and the media audit/migration commands.
"""
import io
import os
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.storage import FileSystemStorage, storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from bookmyseat.cloudinary_storage import CloudinaryMediaStorage
from movies.models import Movie

CLOUD_STORAGES = {
    'default': {'BACKEND': 'bookmyseat.cloudinary_storage.CloudinaryMediaStorage'},
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}


def _png(name='poster.png'):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (4, 6), (200, 30, 30)).save(buf, 'PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def _movie_data():
    return {
        'name': 'Persist Test',
        'category': 'live_concert',
        'cast': 'Someone Famous',
        'rating': '8.1',
        'status': 'now_showing',
        'show_on_homepage': 'on',
    }


class StorageSelectionTests(TestCase):
    def test_development_default_is_filesystem(self):
        """Without CLOUDINARY_URL, uploads stay on the local filesystem."""
        self.assertIsInstance(storages['default'], FileSystemStorage)

    def test_cloudinary_selected_when_configured(self):
        """With the production STORAGES mapping, default storage is Cloudinary."""
        with override_settings(STORAGES=CLOUD_STORAGES):
            self.assertIsInstance(storages['default'], CloudinaryMediaStorage)

    def test_movie_fields_use_active_default_storage(self):
        with override_settings(STORAGES=CLOUD_STORAGES):
            field = Movie._meta.get_field('image')
            self.assertIsInstance(field.storage, CloudinaryMediaStorage)


@mock.patch.dict(os.environ, {'CLOUDINARY_URL': 'cloudinary://k:s@demo'})
class CloudinaryStorageBehaviourTests(TestCase):
    """Unit tests for the storage class with the Cloudinary SDK mocked.

    Every SDK touchpoint is patched in setUp so no test can ever reach the
    network.
    """

    DEMO_URL = (
        'https://res.cloudinary.com/demo/image/upload/movies/posters/p.png'
    )

    def setUp(self):
        import cloudinary.api

        upload = mock.patch(
            'cloudinary.uploader.upload',
            return_value={'public_id': 'movies/x', 'version': 1},
        )
        api_resource = mock.patch(
            'cloudinary.api.resource',
            side_effect=cloudinary.api.NotFound('gone'),
        )
        cloudinary_url = mock.patch(
            'cloudinary.utils.cloudinary_url',
            return_value=(self.DEMO_URL, {}),
        )
        self.mock_upload = upload.start()
        self.mock_api_resource = api_resource.start()
        self.mock_cloudinary_url = cloudinary_url.start()
        self.addCleanup(upload.stop)
        self.addCleanup(api_resource.stop)
        self.addCleanup(cloudinary_url.stop)
        self.storage = CloudinaryMediaStorage()

    def _resource_taken_once(self):
        import cloudinary.api
        self.mock_api_resource.side_effect = [
            {'public_id': 'x'}, cloudinary.api.NotFound('gone'),
        ]

    def test_save_keeps_relative_path_shape_with_extension(self):
        name = self.storage.save('movies/posters/p.png', io.BytesIO(b'x'))
        self.mock_upload.assert_called_once()
        kwargs = self.mock_upload.call_args.kwargs
        self.assertEqual(kwargs['public_id'], 'movies/posters/p')
        self.assertEqual(kwargs['format'], 'png')
        self.assertFalse(kwargs['overwrite'])
        self.assertEqual(name, 'movies/posters/p.png')

    def test_save_never_overwrites_existing_resource(self):
        # First existence probe says the id is taken -> suffix must be added.
        self._resource_taken_once()
        name = self.storage.save('movies/posters/p.png', io.BytesIO(b'x'))
        base = name.rsplit('.', 1)[0]
        self.assertTrue(base.startswith('movies/posters/p_'))
        self.assertNotEqual(name, 'movies/posters/p.png')

    def test_url_builds_secure_cloudinary_url(self):
        url = self.storage.url('movies/posters/p.png')
        self.assertEqual(url, self.DEMO_URL)

    @override_settings(STORAGES=CLOUD_STORAGES)
    def test_model_upload_produces_permanent_url(self):
        import cloudinary
        # setUp already mocks upload/api; configure the real URL builder.
        cloudinary.config(cloud_name='demo', api_key='k',
                          api_secret='s', secure=True)
        movie = Movie.objects.create(
            image=_png(), rating=7, cast='c', name='Cloud Movie',
        )
        self.mock_upload.assert_called_once()
        self.assertTrue(movie.image.name.startswith('movies/'))
        # Real URL builder output — permanent HTTPS delivery URL.
        self.assertTrue(movie.image.url.startswith(
            'https://res.cloudinary.com/demo/image/upload/'))
        self.assertTrue(movie.image.url.endswith('.png'))
        # Row stores a path-like reference, not a binary blob.
        self.assertFalse(movie.image.name.startswith('http'))

    def test_delete_calls_destroy_with_invalidate(self):
        destroy = mock.patch('cloudinary.uploader.destroy')
        with destroy as d:
            self.storage.delete('movies/banners/b.png')
        d.assert_called_once()
        args, kwargs = d.call_args
        self.assertEqual(args[0], 'movies/banners/b')
        self.assertTrue(kwargs.get('invalidate'))

    def test_unsafe_names_rejected(self):
        with self.assertRaises(Exception):
            self.storage.url('../secret.png')

    def test_deconstruct_is_stable(self):
        path, args, kwargs = self.storage.deconstruct()
        self.assertEqual(path, 'bookmyseat.cloudinary_storage.CloudinaryMediaStorage')
        self.assertEqual(list(args), [])
        self.assertEqual(kwargs, {})


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='bms-media-'))
class AdminUploadRoundTripTests(TestCase):
    """End-to-end upload through the exact form used by the admin panel."""

    def _form(self, **extra):
        from admin_panel.forms import MovieForm
        data = {**_movie_data(), **extra}
        files = {'image': _png()}
        if extra.get('with_banner'):
            files['banner'] = _png('banner.png')
            del extra['with_banner']
        return MovieForm(data=data, files=files)

    def test_poster_upload_saves_and_serves_url(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        movie = form.save()
        self.assertTrue(movie.image.name.endswith('.png'))
        stored = os.path.join(movie.image.storage.location, movie.image.name)
        self.assertTrue(os.path.isfile(stored))
        self.assertEqual(movie.image.url, f'/media/{movie.image.name}')

    def test_banner_upload_and_replacement_cache_busts(self):
        from admin_panel.forms import MovieForm

        form = self._form(with_banner=True)
        self.assertTrue(form.is_valid(), form.errors)
        movie = form.save()
        old_name = movie.banner.name

        replacement = MovieForm(data=_movie_data(), files={'banner': _png()},
                                instance=movie)
        self.assertTrue(replacement.is_valid(), replacement.errors)
        replacement.save()
        movie.refresh_from_db()

        # Django keeps the replaced file on disk (by design), but the row and
        # the URL must point at a NEW unique name so no browser/CDN cache can
        # serve the stale image.
        self.assertNotEqual(movie.banner.name, old_name)
        self.assertNotEqual(movie.banner.url, f'/media/{old_name}')
        self.assertTrue(os.path.isfile(os.path.join(
            movie.banner.storage.location, movie.banner.name)))


class MissingImageFallbackTests(TestCase):
    def test_missing_image_beacon_is_logged(self):
        import logging
        url = reverse('log_missing_image')
        with self.assertLogs('bookmyseat.media', level=logging.WARNING) as logs:
            response = self.client.post(
                url, {'url': '/media/movies/posters/gone.png'}, format='json',
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertTrue(any('gone.png' in line for line in logs.output))

    def test_get_not_allowed(self):
        response = self.client.get(reverse('log_missing_image'))
        self.assertEqual(response.status_code, 405)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='bms-media-'))
class CheckMediaCommandTests(TestCase):
    def test_reports_missing_without_touching_rows(self):
        movie = Movie.objects.create(
            image=_png('lost.png'), rating=7, cast='c', name='Lost Poster',
        )
        # Simulate Render's ephemeral wipe: row exists, file does not.
        os.remove(os.path.join(movie.image.storage.location, movie.image.name))

        out = io.StringIO()
        call_command('check_media', stdout=out)
        text = out.getvalue()

        self.assertIn('MISSING', text)
        self.assertIn('lost.png', text)
        movie.refresh_from_db()
        # Row untouched — re-upload through admin remains possible.
        self.assertTrue(Movie.objects.filter(pk=movie.pk, image=movie.image).exists())

    def test_migrate_command_refuses_filesystem_storage(self):
        with self.assertRaises(CommandError):
            call_command('migrate_media_to_cloudinary', '--commit')


class ProductionRecordsPreservedTests(TestCase):
    """Guard: enabling cloud storage requires zero schema migrations."""

    def test_no_pending_model_migrations(self):
        from django.apps import apps
        from django.db.migrations.autodetector import MigrationAutodetector
        from django.db.migrations.loader import MigrationLoader
        from django.db.migrations.state import ProjectState

        loader = MigrationLoader(None, ignore_no_migrations=True)
        autodetector = MigrationAutodetector(
            loader.project_state(), ProjectState.from_apps(apps),
        )
        changes = autodetector.changes(graph=loader.graph)
        self.assertEqual(
            list(changes.keys()), [],
            'Model drift detected — audit before touching production.',
        )
