import json

data = json.load(open('db_backup.json', 'r', encoding='utf-8'))

# Movie 34 details
movie34 = [d for d in data if d['model'] == 'movies.movie' and d['pk'] == 34]
print('=== MOVIE 34 ===')
if movie34:
    print(json.dumps(movie34[0]['fields'], indent=2, default=str)[:1000])

# Shows for movie 34
shows = [d for d in data if d['model'] == 'admin_panel.show' and d['fields'].get('movie') == 34]
print(f'\n=== SHOWS for movie 34: {len(shows)} ===')
for s in shows[:5]:
    f = s['fields']
    print(f"  pk={s['pk']} theatre={f.get('theatre')} screen={f.get('screen')} date={f.get('date')} time={f.get('time')} status={f.get('status')} theater={f.get('theater')}")

# Theaters for movie 34
theaters = [d for d in data if d['model'] == 'movies.theater' and d['fields'].get('movie') == 34]
print(f'\n=== THEATERS for movie 34: {len(theaters)} ===')
for t in theaters[:10]:
    f = t['fields']
    name = f.get('name', '?')[:30] if f.get('name') else '?'
    print(f"  pk={t['pk']} name={name} time={f.get('time')} status={f.get('status')}")

# Check all models
models = set(d['model'] for d in data)
print(f'\n=== ALL MODELS ===')
for m in sorted(models):
    count = sum(1 for d in data if d['model'] == m)
    print(f'  {m}: {count}')

# Check if Show 34 has a linked theater
shows_for_34 = [d for d in data if d['model'] == 'admin_panel.show' and d['pk'] == 34]
if shows_for_34:
    print(f'\n=== SHOW PK 34 (if exists) ===')
    print(json.dumps(shows_for_34[0], indent=2, default=str)[:500])

# Check Theater objects with admin_show link
print('\n=== Show records that link to Theaters ===')
all_shows = [d for d in data if d['model'] == 'admin_panel.show']
linked = sum(1 for s in all_shows if s['fields'].get('theater'))
unlinked = sum(1 for s in all_shows if not s['fields'].get('theater'))
print(f'  Linked: {linked}, Unlinked: {unlinked}')

# Check Seats for theaters of movie 34
theater_pks = [t['pk'] for t in theaters]
seats = [d for d in data if d['model'] == 'movies.seat' and d['fields'].get('theater') in theater_pks]
print(f'\n=== SEATS for movie 34 theaters: {len(seats)} ===')
if seats:
    print(f"  First 5: {[(s['pk'], s['fields'].get('seat_number'), s['fields'].get('theater')) for s in seats[:5]]}")
