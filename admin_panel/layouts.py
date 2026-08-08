"""Deterministic per-screen seat layout definitions.

Each layout is a plain-dict "spec" consumed by:

  * ``admin_panel.services.create_seats_for_theater`` to materialise Seat rows
  * ``movies.services.seat_data_for_show`` / templates to render the seat map
  * the customer-facing JS to mirror aisle gaps, couple pairing and exits

Spec shape produced by :func:`build_layout_spec`::

    {
        "size": "medium",
        "variant": "straight",
        "rows": 12,
        "cols_per_section": 12,     # seats per side (left/right of the aisle)
        "total_cols": 24,           # widest row (max seats across)
        "screen_cols": 14,          # CSS width hint for the screen element
        "tier_gap_row": null,       # optional row index with an extra visual gap
        "sections": [               # front -> back price bands
            {"name": "Classic", "start_row": 0, "end_row": 3, "best_view": False},
            {"name": "Premium", ...},
            {"name": "Economy", ...}  # always the last ECONOMY_ROW_COUNT rows
        ],
        "couple_rows": [10],        # row indexes that contain couple seats
        "couple_pairs": [["J11", "J12"], ...],
        "wheelchair_seats": ["E1", "E24"],
        "exits": [{"side": "left", "row_idx": 11}, {"side": "right", "row_idx": 11}],
        "seats": [                  # fully resolved seats, front to back, left to right
            {"num": "A1", "row": "A", "r": 0, "c": 0, "side": "left",
             "gap_before": False, "type": "standard",
             "category": "Classic", "best_view": False}
        ]
    }
"""
SCREEN_SIZES = {
    'small': {'rows': 8, 'cols_per_section': 10},
    'medium': {'rows': 12, 'cols_per_section': 12},
    'large': {'rows': 14, 'cols_per_section': 14},
    'imax': {'rows': 18, 'cols_per_section': 16},
    'premium': {'rows': 14, 'cols_per_section': 13},
}

# The rows nearest the screen (bottom of the map) are always Economy.
ECONOMY_ROW_COUNT = 2

# Default front -> back price bands. Row A (top of the map) is the furthest
# from the screen; the last ECONOMY_ROW_COUNT rows sit directly in front of
# the screen and are always priced as Economy.
SECTION_PLAN = [
    ('Classic', 0.30, False),
    ('Premium', 0.25, True),
    ('Platinum', 0.25, True),
    ('Recliner', 0.20, True),
]

# Band emphasis overrides per variant (geometry variants reuse SECTION_PLAN).
VARIANT_BAND_PLANS = {
    'premium_front': [
        ('Premium', 0.30, True),
        ('Platinum', 0.30, True),
        ('Recliner', 0.40, True),
    ],
    'vip': [
        ('Classic', 0.25, False),
        ('VIP', 0.30, True),
        ('Platinum', 0.25, True),
        ('Recliner', 0.20, True),
    ],
    'lounge': [
        ('Classic', 0.25, False),
        ('Premium', 0.25, True),
        ('Platinum', 0.25, True),
        ('Lounge', 0.25, True),
    ],
}

LAYOUT_VARIANTS = [
    'straight', 'centre_aisle', 'split_block', 'curved',
    'premium_front', 'vip', 'balcony', 'recliner', 'lounge',
]


def _row_label(index):
    """0 -> A, 1 -> B, ..., 25 -> Z, 26 -> AA, ..."""
    if index < 26:
        return chr(ord('A') + index)
    return _row_label(index // 26 - 1) + chr(ord('A') + index % 26)


def section_bands(rows, plan=None):
    """Return front -> back sections with inclusive start/end row indexes.

    The last :data:`ECONOMY_ROW_COUNT` rows (nearest the screen) are always
    pinned to an ``Economy`` band; the given plan fills the rows above them.
    """
    plan = plan or SECTION_PLAN
    economy_start = max(0, rows - ECONOMY_ROW_COUNT)
    usable = economy_start
    if usable <= 0:
        return [{'name': 'Economy', 'start_row': 0, 'end_row': rows - 1, 'best_view': False}]

    bands = []
    start = 0
    for name, ratio, best_view in plan:
        if start >= usable:
            break
        count = max(1, round(usable * ratio))
        end = min(start + count - 1, usable - 1)
        bands.append({'name': name, 'start_row': start, 'end_row': end, 'best_view': best_view})
        start = end + 1
    if bands and bands[-1]['end_row'] < usable - 1:
        bands[-1]['end_row'] = usable - 1

    bands.append({
        'name': 'Economy',
        'start_row': economy_start,
        'end_row': rows - 1,
        'best_view': False,
    })
    return bands


def band_for_row(bands, r):
    for band in bands:
        if band['start_row'] <= r <= band['end_row']:
            return band['name']
    return bands[-1]['name'] if bands else ''


def _band_is_best(bands, r):
    for band in bands:
        if band['start_row'] <= r <= band['end_row']:
            return band['best_view']
    return False


def _row_geometry(variant, rows, cols, r):
    """Return (left_cols, right_cols) for a row under a geometry variant."""
    if variant == 'curved':
        progress = r / max(1, rows - 1)
        shrink = int(round((1 - progress) * 2))
        section_cols = max(2, cols - shrink)
        return section_cols, section_cols
    return cols, cols


def _default_couple_row(bands):
    """Row index used for couple seats on standard geometry variants."""
    recliner = [b for b in bands if b['name'] == 'Recliner']
    if recliner:
        return recliner[-1]['end_row']
    vip = [b for b in bands if b['name'] == 'VIP']
    if vip:
        return vip[-1]['end_row']
    lounge = [b for b in bands if b['name'] == 'Lounge']
    if lounge:
        return lounge[-1]['end_row']
    return bands[-2]['end_row'] if len(bands) > 1 else 0


def _couple_pairs_for(variant, rows, cols, bands, geometry):
    """Return list of [seat_number, seat_number] couple/recliner pairs."""
    centre = []
    r = _default_couple_row(bands)
    left, right = geometry[r]
    total = left + right
    centre = [
        [f'{_row_label(r)}{left}', f'{_row_label(r)}{left + 1}'],
        [f'{_row_label(r)}{left + 2}', f'{_row_label(r)}{left + 3}'],
    ]

    if variant == 'recliner':
        recliner_bands = [b for b in bands if b['name'] == 'Recliner']
        pairs = []
        for band in recliner_bands:
            for r2 in range(band['start_row'], band['end_row'] + 1):
                l2, _ = geometry[r2]
                pairs.append([f'{_row_label(r2)}{l2}', f'{_row_label(r2)}{l2 + 1}'])
                pairs.append([f'{_row_label(r2)}{l2 + 2}', f'{_row_label(r2)}{l2 + 3}'])
        return pairs

    if variant == 'lounge':
        lounge_bands = [b for b in bands if b['name'] == 'Lounge']
        if lounge_bands:
            r2 = lounge_bands[-1]['end_row']
            l2, _ = geometry[r2]
            total2 = l2 * 2
            return [
                [f'{_row_label(r2)}1', f'{_row_label(r2)}2'],
                [f'{_row_label(r2)}{total2 - 1}', f'{_row_label(r2)}{total2}'],
            ]

    if variant == 'vip':
        vip_bands = [b for b in bands if b['name'] == 'VIP']
        if vip_bands:
            r2 = vip_bands[-1]['end_row']
            l2, _ = geometry[r2]
            return [
                [f'{_row_label(r2)}{l2}', f'{_row_label(r2)}{l2 + 1}'],
                [f'{_row_label(r2)}{l2 + 2}', f'{_row_label(r2)}{l2 + 3}'],
            ]

    return centre


def variant_for(name, forced=None):
    """Deterministic layout variant for a screen name (stable across seeds/regens)."""
    if forced:
        return forced
    variants = LAYOUT_VARIANTS
    h = sum(ord(ch) for ch in (name or 'Screen'))
    return variants[h % len(variants)]


def build_layout_spec(size, variant='straight'):
    """Build a fully resolved layout spec for a screen size + variant."""
    size = size if size in SCREEN_SIZES else 'small'
    variant = variant if variant in LAYOUT_VARIANTS else 'straight'
    rows = SCREEN_SIZES[size]['rows']
    cols = SCREEN_SIZES[size]['cols_per_section']
    plan = VARIANT_BAND_PLANS.get(variant, SECTION_PLAN)
    bands = section_bands(rows, plan)

    geometry = [_row_geometry(variant, rows, cols, r) for r in range(rows)]
    max_total = max(left + right for left, right in geometry)

    couple_pairs = _couple_pairs_for(variant, rows, cols, bands, geometry)
    couple_seats = {num for pair in couple_pairs for num in pair}
    couple_rows = []
    for pair in couple_pairs:
        row_label = pair[0].rstrip('0123456789') or 'A'
        for idx in range(rows):
            if _row_label(idx) == row_label:
                if idx not in couple_rows:
                    couple_rows.append(idx)
                break
    couple_rows.sort()

    wheelchair_row = bands[0]['start_row']

    seats = []
    for r in range(rows):
        band_name = band_for_row(bands, r)
        best = _band_is_best(bands, r)
        left, right = geometry[r]
        total = left + right
        for c in range(total):
            side = 'left' if c < left else 'right'
            gap_before = c == left
            num = f'{_row_label(r)}{c + 1}'
            seat_type = 'standard'
            if num in couple_seats:
                seat_type = 'couple'
            if wheelchair_row is not None and r == wheelchair_row and c in (0, total - 1):
                seat_type = 'wheelchair'
            seats.append({
                'num': num,
                'row': _row_label(r),
                'r': r,
                'c': c,
                'side': side,
                'gap_before': gap_before,
                'type': seat_type,
                'category': band_name,
                'best_view': best,
            })

    wheelchair_seats = [
        s['num'] for s in seats if s['type'] == 'wheelchair'
    ]
    exits = [
        {'side': 'left', 'row_idx': rows - 1},
        {'side': 'right', 'row_idx': rows - 1},
    ]
    return {
        'size': size,
        'variant': variant,
        'rows': rows,
        'cols_per_section': cols,
        'total_cols': max_total,
        'screen_cols': cols,
        'tier_gap_row': (rows // 2) if variant == 'balcony' else None,
        'sections': bands,
        'couple_rows': couple_rows,
        'couple_pairs': couple_pairs,
        'wheelchair_seats': wheelchair_seats,
        'exits': exits,
        'seats': seats,
    }


def capacity_of(spec):
    """Total seat count for a spec (excluding nothing; every seat is sellable)."""
    return len(spec['seats'])


def preview_rows(spec):
    """Return a compact per-row cell plan for rendering an admin preview.

    Each row is a dict with ``label`` and ``cells`` where every cell is one of
    'seat', 'couple', 'wheelchair' or 'aisle'.
    """
    rows = []
    for r in range(spec.get('rows', 0)):
        cells = []
        for seat in spec['seats']:
            if seat['r'] != r:
                continue
            if seat['gap_before']:
                cells.append('aisle')
            cells.append({
                'couple': 'couple',
                'wheelchair': 'wheelchair',
            }.get(seat['type'], 'seat'))
        rows.append({'label': spec['seats'][r]['row'] if spec['seats'] else '', 'cells': cells})
    return rows
