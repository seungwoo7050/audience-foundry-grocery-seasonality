from typing import Any, cast

from django.conf import settings
from django.test import Client, SimpleTestCase
from django.urls import reverse


class LoggingSettingsTests(SimpleTestCase):
    def test_request_id_middleware_is_active(self) -> None:
        response = Client().get(reverse("admin:login"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Request-ID", response.headers)

    def test_query_bearing_framework_access_logs_are_disabled(self) -> None:
        logging_config = cast(dict[str, Any], settings.LOGGING)
        loggers = logging_config["loggers"]

        self.assertEqual(loggers["django.request"]["handlers"], ["null"])
        self.assertEqual(loggers["django.server"]["handlers"], ["null"])
        self.assertFalse(loggers["django.request"]["propagate"])
        self.assertFalse(loggers["django.server"]["propagate"])

    def test_audit_logger_uses_only_structured_allowlisted_output(self) -> None:
        logging_config = cast(dict[str, Any], settings.LOGGING)
        logger = logging_config["loggers"]["grocery.audit"]
        handler = logging_config["handlers"]["structured_console"]

        self.assertEqual(logger["handlers"], ["structured_console"])
        self.assertEqual(handler["filters"], ["observability_allowlist"])
        self.assertEqual(handler["formatter"], "structured_json")
