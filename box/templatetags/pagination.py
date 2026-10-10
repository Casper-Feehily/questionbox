from django import template

register = template.Library()


@register.simple_tag
def page_numbers(page):
    """Show up to five consecutive pages, centered on the current page."""
    total = page.paginator.num_pages
    start = max(1, min(page.number - 2, total - 4))
    return range(start, min(start + 4, total) + 1)
