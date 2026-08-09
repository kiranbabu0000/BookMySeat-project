"""Site-wide context for the public city selector.

Exposes the available cities (with active shows) and the currently selected
city so every template can render the navbar location picker. The selection is
persisted client-side (localStorage + ``bms_city`` cookie); the cookie lets
server-rendered pages default their filtering to the chosen city.
"""
from .discovery import available_cities

CITY_COOKIE = 'bms_city'


def bms_cities(request):
    cities = available_cities()
    city = (request.GET.get('city') or request.COOKIES.get(CITY_COOKIE) or '').strip()
    if city not in cities:
        city = ''
    return {
        'all_cities': cities,
        'current_city': city,
        'city_cookie': CITY_COOKIE,
    }
