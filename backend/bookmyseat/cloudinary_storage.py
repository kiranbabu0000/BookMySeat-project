"""Persistent media storage for production (Cloudinary).

Render's web-service filesystem is ephemeral: every restart, redeploy or
spin-down rebuilds the container from the git repository and wipes anything
uploaded at runtime. Django's default FileSystemStorage therefore loses
admin-uploaded posters/banners even though PostgreSQL keeps the path.

This module provides a Django Storage backend backed by the official
Cloudinary SDK so uploads become permanent URLs. It is activated purely via
configuration — when CLOUDINARY_URL is set, bookmyseat/settings.py points
STORAGES["default"] at CloudinaryMediaStorage; without it (local
development) Django keeps using FileSystemStorage and this module is never
imported. No model changes or migrations are required because FileField
resolves its storage from STORAGES at runtime.

Design notes:
- The database value stays a normal relative media path with extension,
  e.g. ``movies/posters/foo.png`` — identical in shape to what
  FileSystemStorage stored before, so existing rows keep working.
- On upload the extension is stripped to build the Cloudinary public_id
  (``movies/posters/foo``) and the original format is passed explicitly.
- Collisions never overwrite: a random suffix is appended (same behaviour
  as FileSystemStorage.get_alternative_name), which also doubles as cache
  busting when an admin replaces an image under a similar name.
"""

import os
import urllib.request
from datetime import datetime, timezone as dt_timezone

from django.core.exceptions import SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class CloudinaryMediaStorage(Storage):
    """Django Storage API implementation on top of the cloudinary SDK."""

    resource_type = 'image'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import cloudinary

        # The SDK auto-reads CLOUDINARY_URL at import; configure explicitly
        # so HTTPS delivery URLs are guaranteed even if something else
        # imported/configured cloudinary first.
        if os.environ.get('CLOUDINARY_URL'):
            cloudinary.config(secure=True)

    # ------------------------------------------------------------------
    # Name helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _split_ext(name):
        base, ext = os.path.splitext(name)
        return base.replace('\\', '/'), ext.lstrip('.').lower()

    @classmethod
    def _public_id(cls, name):
        return cls._split_ext(name)[0]

    @staticmethod
    def _validate_name(name):
        if '..' in name.split('/') or name.startswith('/'):
            raise SuspiciousFileOperation(f'Unsafe media name: {name!r}')
        return name

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------
    def _open(self, name, mode='rb'):
        """Download the asset binary over its signed-off delivery URL."""
        with urllib.request.urlopen(self.url(name)) as response:  # noqa: S310
            data = response.read()
        return ContentFile(data, name=name)

    def _save(self, name, content):
        import cloudinary.uploader

        self._validate_name(name)
        content.seek(0)
        base, ext = self._split_ext(name)
        final_base = self._available_public_id(base)
        options = {
            'resource_type': self.resource_type,
            'overwrite': False,
            'invalidate': True,
        }
        if ext:
            options['format'] = ext
        cloudinary.uploader.upload(content, public_id=final_base, **options)
        # Store the original-style name (with extension) so values look
        # exactly like FileSystemStorage ones did before.
        return f'{final_base}.{ext}' if ext else final_base

    def get_available_name(self, name, max_length=None):
        base, ext = self._split_ext(self._validate_name(name))
        final = f'{base}.{ext}' if ext else base
        if max_length and len(final) > max_length:
            raise SuspiciousFileOperation(
                f'Storage name too long for {name!r} (max {max_length}).'
            )
        return final

    def _available_public_id(self, base):
        """First non-existing public id — uploads never overwrite each other."""
        candidate = base
        while self._resource_exists(candidate):
            from django.utils.crypto import get_random_string
            candidate = f'{base}_{get_random_string(7)}'
        return candidate

    def exists(self, name):
        try:
            self._validate_name(name)
        except SuspiciousFileOperation:
            return False
        return self._resource_exists(self._public_id(name))

    def _resource_exists(self, public_id):
        import cloudinary.api

        try:
            cloudinary.api.resource(public_id, resource_type=self.resource_type)
            return True
        except cloudinary.api.NotFound:
            return False
        except Exception:
            self._logger().warning(
                'Cloudinary existence check failed for %s; assuming missing.',
                public_id, exc_info=True,
            )
            return False

    def delete(self, name):
        import cloudinary.uploader

        public_id = self._public_id(self._validate_name(name))
        try:
            cloudinary.uploader.destroy(
                public_id, resource_type=self.resource_type, invalidate=True,
            )
        except Exception:
            self._logger().warning(
                'Cloudinary delete failed for %s', public_id, exc_info=True,
            )

    def url(self, name):
        if not name:
            return ''
        import cloudinary.utils

        self._validate_name(name)
        source, _options = cloudinary.utils.cloudinary_url(
            name,
            type='upload',
            resource_type=self.resource_type,
            secure=True,
        )
        return source

    def size(self, name):
        return int(self._info(name).get('bytes', 0))

    def get_modified_time(self, name):
        created = self._info(name).get('created_at')
        if not created:
            return None
        parsed = datetime.fromisoformat(created.replace('Z', '+00:00'))
        return parsed.astimezone(dt_timezone.utc)

    def accessed_time(self, name):
        return self.get_modified_time(name)

    def created_time(self, name):
        return self.get_modified_time(name)

    def _info(self, name):
        import cloudinary.api

        return cloudinary.api.resource(
            self._public_id(self._validate_name(name)),
            resource_type=self.resource_type,
        )

    # ------------------------------------------------------------------
    # Local-filesystem-only API stays honest
    # ------------------------------------------------------------------
    @property
    def base_location(self):
        raise NotImplementedError(
            'CloudinaryMediaStorage has no local filesystem location.'
        )

    def path(self, name):
        raise NotImplementedError(
            'CloudinaryMediaStorage serves remote URLs only; use .url().'
        )

    def listdir(self, path):
        raise NotImplementedError('Directory listing is not supported.')

    def deconstruct(self):
        return (
            'bookmyseat.cloudinary_storage.CloudinaryMediaStorage',
            (), {},
        )

    @staticmethod
    def _logger():
        import logging

        return logging.getLogger('bookmyseat.media')
