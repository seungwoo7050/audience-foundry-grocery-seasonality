"""Fixed HTTP security boundaries for the server-rendered public surface."""

from collections.abc import Callable
from typing import Final

from django.conf import settings
from django.http import HttpRequest, HttpResponseNotFound
from django.http.response import HttpResponseBase

GetResponse = Callable[[HttpRequest], HttpResponseBase]

CONTENT_SECURITY_POLICY: Final = (
    "default-src 'self'; "
    "script-src 'none'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-src 'none'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'"
)

SECURITY_HEADERS: Final[dict[str, str]] = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class SecurityHeadersMiddleware:
    """Attach one input-independent header policy to every downstream response."""

    sync_capable = True
    async_capable = False

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        response = self.get_response(request)
        for header_name, header_value in SECURITY_HEADERS.items():
            response.headers[header_name] = header_value
        return response


class AdminExposureMiddleware:
    """Fail closed with a generic response when the Django admin is disabled."""

    sync_capable = True
    async_capable = False

    def __init__(self, get_response: GetResponse) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        path = request.path_info
        admin_path = path == "/admin" or path.startswith("/admin/")
        if admin_path and not getattr(settings, "ADMIN_ENABLED", False):
            return HttpResponseNotFound(
                "Not Found",
                content_type="text/plain; charset=utf-8",
            )
        return self.get_response(request)
