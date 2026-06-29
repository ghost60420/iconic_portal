from time import perf_counter

from django.db import connection

from crm.services.platform_tools import descriptor_from_request, save_request_performance, track_recent_record


class CRMPlatformMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = perf_counter()
        before_queries = len(connection.queries)
        response = self.get_response(request)
        query_count = len(connection.queries) - before_queries if connection.queries_logged else None
        save_request_performance(request.path, (perf_counter() - started) * 1000, query_count)

        user = getattr(request, "user", None)
        if (
            request.method == "GET"
            and response.status_code == 200
            and user
            and getattr(user, "is_authenticated", False)
        ):
            try:
                track_recent_record(user, descriptor_from_request(request))
            except Exception:
                # Record history is optional and must never break a business page.
                pass
        return response
