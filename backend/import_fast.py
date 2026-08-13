import json
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bookmyseat.settings')
import django
django.setup()

from django.apps import apps
from django.core.management.color import no_style
from django.db import connection, transaction
from django.db.models import ManyToManyField

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db_backup.json')

model_map = {m._meta.label_lower: m for m in apps.get_models()}

with open(FIXTURE, encoding='utf-8') as f:
    data = json.load(f)

by_model = {}
for obj in data:
    by_model.setdefault(obj['model'], []).append(obj)

present_models = []
for label in by_model:
    model = model_map.get(label)
    if model is None:
        print('SKIP %s - model not found' % label)
        continue
    present_models.append(model)


def dependency_order(models):
    model_set = set(models)
    deps = {}
    for m in models:
        d = set()
        for f in m._meta.concrete_fields:
            if f.is_relation and (f.many_to_one or f.one_to_one):
                rel = f.related_model
                if rel is not None and rel is not m and rel in model_set:
                    d.add(rel)
        deps[m] = d
    order = []
    done = set()
    while len(order) < len(models):
        progressed = False
        for m in models:
            if m in done:
                continue
            if deps[m] <= done:
                order.append(m)
                done.add(m)
                progressed = True
        if not progressed:
            for m in models:
                if m not in done:
                    order.append(m)
                    done.add(m)
    return order


ordered = dependency_order(present_models)
pending_m2m = []
total = 0


def flush(model):
    global total
    label = model._meta.label_lower
    rows = by_model[label]
    instances = []
    m2m_rows = []
    for row in rows:
        fields = dict(row['fields'])
        m2m_data = {}
        for name in list(fields):
            field = model._meta.get_field(name)
            if isinstance(field, ManyToManyField):
                m2m_data[name] = fields.pop(name)
        inst = model()
        for name, value in fields.items():
            setattr(inst, model._meta.get_field(name).attname, value)
        if 'pk' in row and row['pk'] is not None:
            try:
                setattr(inst, model._meta.pk.attname, row['pk'])
            except Exception:
                pass
        instances.append(inst)
        m2m_rows.append(m2m_data)
    created = model.objects.bulk_create(instances, batch_size=500)
    for inst, m2m_data in zip(created, m2m_rows):
        for name, pks in m2m_data.items():
            if pks:
                pending_m2m.append((inst, name, pks))
    total += len(created)
    print('OK %s +%d' % (label, len(created)))


with transaction.atomic():
    for model in ordered:
        flush(model)

    print('Linking many-to-many relations...')
    for inst, name, pks in pending_m2m:
        getattr(inst, name).set(pks)

print('Resetting sequences...')
sequence_sql = connection.ops.sequence_reset_sql(no_style(), ordered)
with connection.cursor() as cursor:
    for line in sequence_sql:
        cursor.execute(line)

print('DONE. Total objects imported: %d' % total)
