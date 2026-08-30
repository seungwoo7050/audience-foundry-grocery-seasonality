import pytest
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, override_settings

from grocery.security import (
    CONTENT_SECURITY_POLICY,
    SECURITY_HEADERS,
    AdminExposureMiddleware,
    SecurityHeadersMiddleware,
)


@pytest.mark.parametrize("status_code", [200, 400, 403, 404, 500])
def test_security_headers_apply_to_success_and_error_responses(status_code: int) -> None:
    request = RequestFactory().get("/example")
    middleware = SecurityHeadersMiddleware(lambda unused_request: HttpResponse(status=status_code))

    response = middleware(request)

    assert response.status_code == status_code
    for header_name, header_value in SECURITY_HEADERS.items():
        assert response.headers[header_name] == header_value


def test_content_security_policy_allows_only_required_same_origin_assets() -> None:
    directives = set(CONTENT_SECURITY_POLICY.split("; "))

    assert directives == {
        "default-src 'self'",
        "script-src 'none'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "frame-src 'none'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
    }
    assert "http:" not in CONTENT_SECURITY_POLICY
    assert "https:" not in CONTENT_SECURITY_POLICY
    assert "*" not in CONTENT_SECURITY_POLICY
    assert "'unsafe-inline'" not in CONTENT_SECURITY_POLICY
    assert "'unsafe-eval'" not in CONTENT_SECURITY_POLICY


def test_privileged_browser_capabilities_and_cross_origin_access_are_denied() -> None:
    request = RequestFactory().get("/")
    response = SecurityHeadersMiddleware(lambda unused: HttpResponse())(request)

    assert response.headers["Permissions-Policy"] == (
        "camera=(), geolocation=(), microphone=(), payment=()"
    )
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/admin", "/admin/", "/admin/auth/user/"])
@override_settings(ADMIN_ENABLED=False)
def test_disabled_admin_returns_generic_404_without_calling_downstream(path: str) -> None:
    downstream_called = False

    def get_response(request: HttpRequest) -> HttpResponse:
        nonlocal downstream_called
        downstream_called = True
        return HttpResponse(status=200)

    response = AdminExposureMiddleware(get_response)(RequestFactory().get(path))

    assert response.status_code == 404
    assert isinstance(response, HttpResponse)
    assert response.content == b"Not Found"
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert downstream_called is False


@override_settings(ADMIN_ENABLED=True)
def test_enabled_admin_leaves_authentication_and_csrf_flow_downstream() -> None:
    downstream_response = HttpResponse("admin login", status=401)
    seen_requests: list[HttpRequest] = []

    def get_response(request: HttpRequest) -> HttpResponse:
        seen_requests.append(request)
        return downstream_response

    request = RequestFactory().post("/admin/login/", {"username": "reviewer"})
    response = AdminExposureMiddleware(get_response)(request)

    assert response is downstream_response
    assert response.status_code == 401
    assert seen_requests == [request]


@override_settings(ADMIN_ENABLED=False)
def test_admin_prefix_does_not_hide_unrelated_public_path() -> None:
    def downstream(unused_request: HttpRequest) -> HttpResponse:
        del unused_request
        return HttpResponse(status=204)

    response = AdminExposureMiddleware(downstream)(RequestFactory().get("/administrator/help"))

    assert response.status_code == 204


@override_settings(ADMIN_ENABLED=False)
def test_malicious_query_is_not_reflected_in_headers_or_admin_error() -> None:
    malicious_value = "https://evil.invalid/'><script>alert(1)</script>"
    request = RequestFactory().get("/admin/login/", {"next": malicious_value})
    middleware = SecurityHeadersMiddleware(
        AdminExposureMiddleware(lambda unused: HttpResponse(status=200))
    )

    response = middleware(request)
    serialized_headers = "\n".join(
        f"{header_name}: {header_value}" for header_name, header_value in response.headers.items()
    )
    assert isinstance(response, HttpResponse)
    body = response.content.decode("utf-8")

    assert response.status_code == 404
    assert malicious_value not in serialized_headers
    assert malicious_value not in body
    assert "evil.invalid" not in serialized_headers
    assert "evil.invalid" not in body
