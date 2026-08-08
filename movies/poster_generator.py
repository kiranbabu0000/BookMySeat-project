"""Generate simple PNG posters/banners for seeded demo movies.

Uses Pillow and system fonts so the demo catalogue looks polished even though
no artwork ships with the repository. Files are written under MEDIA_ROOT and
the returned paths are safe to store on the Movie image fields.
"""
import os
import re

from django.conf import settings

_FONT_DIR = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')


def _font_path(variant='regular'):
    names = {
        'regular': ['arial.ttf', 'segoeui.ttf', 'verdana.ttf'],
        'bold': ['arialbd.ttf', 'segoeuib.ttf', 'verdanab.ttf'],
        'italic': ['ariali.ttf', 'segoeui.ttf'],
    }
    for name in names.get(variant, names['regular']):
        path = os.path.join(_FONT_DIR, name)
        if os.path.exists(path):
            return path
    return None


def _font(size, variant='regular'):
    from PIL import ImageFont
    path = _font_path(variant)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _gradient(size, top, bottom):
    from PIL import Image, ImageDraw
    width, height = size
    img = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(height - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    return img


def _wrap(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ''
    for word in words:
        trial = (current + ' ' + word).strip()
        if draw.textbbox((0, 0), trial, font=font)[2] <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _center_text(draw, text, y, font, fill, width, max_width):
    lines = _wrap(draw, text, font, max_width)
    line_height = int(font.size * 1.2)
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (width - (bbox[2] - bbox[0])) / 2
        draw.text((x, y + i * line_height), line, font=font, fill=fill)
    return y + len(lines) * line_height


def _slug(name):
    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    return slug or 'movie'


def generate_poster(name, tagline, top_color, bottom_color, accent,
                    width=600, height=900):
    """Render a poster and return the relative media path."""
    from PIL import Image, ImageDraw
    slug = _slug(name)
    rel_dir = os.path.join('movies', 'posters')
    out_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
    os.makedirs(out_dir, exist_ok=True)
    rel_path = os.path.join(rel_dir, f'{slug}.png')
    full_path = os.path.join(settings.MEDIA_ROOT, rel_path)
    if os.path.exists(full_path):
        return rel_path.replace('\\', '/')

    img = _gradient((width, height), top_color, bottom_color)
    draw = ImageDraw.Draw(img)

    # Decorative ring above the title.
    ring_r = int(width * 0.30)
    ring_x = width // 2
    ring_y = int(height * 0.30)
    draw.ellipse(
        [ring_x - ring_r, ring_y - ring_r, ring_x + ring_r, ring_y + ring_r],
        outline=accent, width=6,
    )
    draw.ellipse(
        [ring_x - ring_r + 26, ring_y - ring_r + 26, ring_x + ring_r - 26, ring_y + ring_r - 26],
        outline=accent, width=2,
    )

    title_font = _font(int(width * 0.068), 'bold')
    title_y = _center_text(draw, name, int(height * 0.52), title_font,
                           (255, 255, 255), width, int(width * 0.86))

    tag_font = _font(int(width * 0.040), 'italic')
    _center_text(draw, tagline, title_y + int(height * 0.06), tag_font,
                 tuple(int(v) for v in accent), width, int(width * 0.80))

    chip_font = _font(int(width * 0.036), 'bold')
    chip = 'NOW SHOWING'
    chip_bbox = draw.textbbox((0, 0), chip, font=chip_font)
    chip_w = chip_bbox[2] - chip_bbox[0] + 40
    chip_h = 52
    chip_x = (width - chip_w) / 2
    chip_y = height - int(height * 0.10)
    draw.rounded_rectangle([chip_x, chip_y, chip_x + chip_w, chip_y + chip_h],
                           radius=chip_h / 2, fill=accent)
    chip_bbox = draw.textbbox((0, 0), chip, font=chip_font)
    draw.text((chip_x + (chip_w - (chip_bbox[2] - chip_bbox[0])) / 2,
               chip_y + (chip_h - (chip_bbox[3] - chip_bbox[1])) / 2 - chip_bbox[1]),
              chip, font=chip_font, fill=(255, 255, 255))

    img.save(full_path)
    return rel_path.replace('\\', '/')


def generate_banner(name, tagline, top_color, bottom_color, accent,
                    width=1200, height=500):
    """Render a 16:6.7 banner and return the relative media path."""
    from PIL import Image, ImageDraw
    slug = _slug(name)
    rel_dir = os.path.join('movies', 'banners')
    out_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
    os.makedirs(out_dir, exist_ok=True)
    rel_path = os.path.join(rel_dir, f'{slug}.png')
    full_path = os.path.join(settings.MEDIA_ROOT, rel_path)
    if os.path.exists(full_path):
        return rel_path.replace('\\', '/')

    img = _gradient((width, height), top_color, bottom_color)
    draw = ImageDraw.Draw(img)

    ring_r = int(height * 0.38)
    draw.ellipse(
        [width - int(width * 0.16) - ring_r, height // 2 - ring_r,
         width - int(width * 0.16) + ring_r, height // 2 + ring_r],
        outline=accent, width=8,
    )

    title_font = _font(int(width * 0.05), 'bold')
    title_y = _center_text(draw, name, int(height * 0.18), title_font,
                           (255, 255, 255), int(width * 0.80), int(width * 0.60))
    tag_font = _font(int(width * 0.028), 'italic')
    _center_text(draw, tagline, title_y + int(height * 0.12), tag_font,
                 tuple(int(v) for v in accent), int(width * 0.80), int(width * 0.58))

    img.save(full_path)
    return rel_path.replace('\\', '/')


def generate_thumbnail(name, top_color, bottom_color, accent, size=(300, 450)):
    """Render a small poster-style thumbnail."""
    from PIL import Image, ImageDraw
    slug = _slug(name)
    rel_dir = os.path.join('movies', 'thumbnails')
    out_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
    os.makedirs(out_dir, exist_ok=True)
    rel_path = os.path.join(rel_dir, f'{slug}.png')
    full_path = os.path.join(settings.MEDIA_ROOT, rel_path)
    if os.path.exists(full_path):
        return rel_path.replace('\\', '/')

    width, height = size
    img = _gradient((width, height), top_color, bottom_color)
    draw = ImageDraw.Draw(img)
    ring_r = int(width * 0.28)
    draw.ellipse(
        [width // 2 - ring_r, int(height * 0.30) - ring_r,
         width // 2 + ring_r, int(height * 0.30) + ring_r],
        outline=accent, width=4,
    )
    title_font = _font(int(width * 0.08), 'bold')
    _center_text(draw, name, int(height * 0.55), title_font,
                 (255, 255, 255), width, int(width * 0.86))
    img.save(full_path)
    return rel_path.replace('\\', '/')
