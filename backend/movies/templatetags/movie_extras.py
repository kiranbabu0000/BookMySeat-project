import re
from django import template
from django.template.defaultfilters import stringfilter
from django.utils import timezone

register = template.Library()

# A real YouTube video id: 11 chars of [A-Za-z0-9_-]. Kept slightly lenient
# (8-20) so legacy/odd ids are not rejected, but strict enough to block junk
# like empty matches, full watch URLs or HTML pasted into the field.
_YT_ID_RE = re.compile(r'^[A-Za-z0-9_-]{8,20}$')

_YT_PATTERNS = [
    # youtube.com/watch?v=ID (with or without extra params)
    r'(?:youtube(?:-nocookie)?\.com)/watch\?(?:[^#]*&)?v=([A-Za-z0-9_-]+)',
    # youtu.be/ID
    r'youtu\.be/([A-Za-z0-9_-]+)',
    # /embed/ID, /v/ID, /shorts/ID, /live/ID (youtube.com or nocookie)
    r'(?:youtube(?:-nocookie)?\.com)/(?:embed|v|shorts|live)/([A-Za-z0-9_-]+)',
    # old "feature=player_embedded&v=ID" share links
    r'youtube\.com/[^#]*[?&]v=([A-Za-z0-9_-]+)',
]


def _extract_youtube_id(url):
    """Return the YouTube video id inside ``url`` or '' when unparseable."""
    if not url:
        return ''
    for pattern in _YT_PATTERNS:
        match = re.search(pattern, url)
        if match and _YT_ID_RE.match(match.group(1)):
            return match.group(1)
    return ''


@register.filter
@stringfilter
def youtube_id(url):
    """Extract just the video id from any common YouTube URL shape."""
    return _extract_youtube_id(url)


@register.filter
@stringfilter
def youtube_embed(url):
    """Privacy-enhanced embed URL for a YouTube URL ('' when not embeddable).

    Uses youtube-nocookie.com (privacy-enhanced mode) and rel=0 so only
    same-channel related videos can appear. Every supported input shape —
    watch, youtu.be, embed, shorts, live, nocookie — normalises to the same
    canonical embed URL, which prevents the classic "Error 153 / Video player
    configuration error" caused by feeding raw watch URLs to an iframe.
    """
    video_id = _extract_youtube_id(url)
    if not video_id:
        return ''
    return f'https://www.youtube-nocookie.com/embed/{video_id}?rel=0'


@register.filter
@stringfilter
def youtube_watch_url(url):
    """Canonical https://www.youtube.com/watch?v=... link for fallbacks."""
    video_id = _extract_youtube_id(url)
    if not video_id:
        return url or ''
    return f'https://www.youtube.com/watch?v={video_id}'


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