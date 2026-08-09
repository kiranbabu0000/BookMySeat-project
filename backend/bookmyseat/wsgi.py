"""
WSGI config for bookmyseat project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/wsgi/
"""

import os
import sys
from pathlib import Path

# Ensure the backend/ folder (parent of this project package) is importable
# when this module is loaded by a serverless runtime whose working directory
# is the repository root (e.g. Vercel's @vercel/python builder).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bookmyseat.settings')

application = get_wsgi_application()
app = application