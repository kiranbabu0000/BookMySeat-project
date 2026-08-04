from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    query = context['request'].GET.copy()
    for key, value in kwargs.items():
        query[key] = value
    return query.urlencode()


@register.filter
def get_item(container, key):
    if container is None:
        return None
    getter = getattr(container, 'get', None)
    if callable(getter):
        return getter(key)
    try:
        return container[key]
    except (KeyError, IndexError, TypeError):
        return None
