from django.http import Http404
from .views import custom_404_view


class Custom404Middleware:
    """
    Middleware that intercepts 404 responses or Http404 exceptions,
    ensuring our custom severed-ticket 404 page is always shown even with DEBUG=True.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code == 404 and not getattr(request, '_custom_404_rendered', False):
            # Only intercept unrouted 404s (where no view was resolved)
            if getattr(request, 'resolver_match', None) is None:
                content_type = response.get('Content-Type', '')
                if 'text/html' in content_type:
                    return custom_404_view(request)
        return response

    def process_exception(self, request, exception):
        if isinstance(exception, Http404):
            return custom_404_view(request, exception=exception)
        return None
