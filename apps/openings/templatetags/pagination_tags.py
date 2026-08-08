# apps/openings/templatetags/pagination_tags.py

from django import template

register = template.Library()


@register.simple_tag
def pagination_query(request, key, value):
    """Return a query string like current GET params but with `key` set to `value`."""
    qs = request.GET.copy()
    qs[key] = value
    return qs.urlencode()
