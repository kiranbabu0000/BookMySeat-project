import re
from django import template
from django.template.defaultfilters import stringfilter
from django.utils import timezone

register = template.Library()


@register.filter
@stringfilter
def youtube_embed(url):
    patterns = [
        r'youtube\.com/watch\?v=([\w-]+)',
        r'youtu\.be/([\w-]+)',
        r'youtube\.com/embed/([\w-]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            return f'https://www.youtube.com/embed/{video_id}'
    return ''


@register.filter
def get_item(dictionary, key):
    try:
        return dictionary.get(int(key), 0)
    except (ValueError, TypeError):
        return dictionary.get(key, 0)


@register.filter
def split_items(value, sep=','):
    """Split a comma/pipe separated string into a trimmed list."""
    if not value:
        return []
    return [part.strip() for part in str(value).split(sep) if part.strip()]


@register.filter
def showtime_part(value):
    """Return the time-of-day bucket (morning/afternoon/evening/night) for a datetime.

    The bucket is derived from the theatre timezone (Asia/Kolkata) so the dot
    colour matches the locally displayed showtime, not the stored UTC value.
    """
    if not value:
        return 'night'
    try:
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        hour = value.hour
    except AttributeError:
        return 'night'
    if hour < 12:
        return 'morning'
    if hour < 17:
        return 'afternoon'
    if hour < 21:
        return 'evening'
    return 'night'