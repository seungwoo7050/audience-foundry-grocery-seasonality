import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

from django.db import DatabaseError
from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase

from grocery import health


def payload(response: JsonResponse) -> dict[str, str]:
    return cast(dict[str, str], json.loads(response.content))


class HealthEndpointTests(SimpleTestCase):
    def setUp(self) -> None:
        self.requests = RequestFactory()

    def test_liveness_is_process_only_and_always_returns_bounded_200(self) -> None:
        request = self.requests.get("/health/live")
        with (
            patch.object(
                health,
                "_database_and_migrations_ready",
                side_effect=AssertionError("database used"),
            ),
            patch.object(
                health,
                "load_active_publication",
                side_effect=AssertionError("publication used"),
            ),
        ):
            response = health.live(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload(response), {"check": "LIVENESS", "status": "OK"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_readiness_checks_database_migration_plan_and_publication(self) -> None:
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        fake_connection = MagicMock()
        fake_connection.cursor.return_value = cursor_context
        executor = MagicMock()
        executor.loader.graph.leaf_nodes.return_value = [("grocery", "0008")]
        executor.migration_plan.return_value = []
        active = SimpleNamespace(freshness_state="stale")

        with (
            patch.object(health, "connection", fake_connection),
            patch.object(health, "MigrationExecutor", return_value=executor) as constructor,
            patch.object(health, "load_active_publication", return_value=active),
        ):
            response = health.ready(self.requests.get("/health/ready"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload(response), {"check": "READINESS", "status": "READY"})
        cursor.execute.assert_called_once_with("SELECT 1")
        executor.loader.check_consistent_history.assert_called_once_with(fake_connection)
        executor.migration_plan.assert_called_once_with([("grocery", "0008")])
        constructor.assert_called_once_with(fake_connection)

    def test_readiness_is_unavailable_for_migration_drift_or_missing_publication(self) -> None:
        unavailable_cases = (
            (False, SimpleNamespace(freshness_state="current")),
            (True, None),
        )
        for database_ready, active in unavailable_cases:
            with self.subTest(database_ready=database_ready, active=active):
                with (
                    patch.object(
                        health,
                        "_database_and_migrations_ready",
                        return_value=database_ready,
                    ),
                    patch.object(health, "load_active_publication", return_value=active),
                    patch.object(health, "log_event") as safe_log,
                ):
                    response = health.ready(self.requests.get("/health/ready"))

                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    payload(response),
                    {"check": "READINESS", "status": "UNAVAILABLE"},
                )
                safe_log.assert_called_once_with(
                    health._LOGGER,
                    "WARNING",
                    "health.readiness.unavailable",
                )

    def test_current_stale_and_unavailable_freshness_have_fixed_safe_shapes(self) -> None:
        cases = (
            (
                SimpleNamespace(freshness_state="current"),
                200,
                "AVAILABLE",
                "CURRENT",
            ),
            (
                SimpleNamespace(freshness_state="stale"),
                503,
                "AVAILABLE",
                "STALE",
            ),
            (None, 503, "UNAVAILABLE", "UNAVAILABLE"),
        )
        for active, expected_status, publication_state, freshness_state in cases:
            with self.subTest(freshness_state=freshness_state):
                with (
                    patch.object(health, "load_active_publication", return_value=active),
                    patch.object(health, "log_event"),
                ):
                    response = health.freshness(self.requests.get("/health/freshness"))

                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(
                    payload(response),
                    {
                        "check": "FRESHNESS",
                        "channel": "RECENT_RETAIL",
                        "publication_state": publication_state,
                        "freshness_state": freshness_state,
                    },
                )
                self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_database_or_internal_failure_is_redacted_from_response_and_log_call(self) -> None:
        malicious = "serviceKey=secret-value raw-row actor=42 hash=" + "a" * 64
        with (
            patch.object(
                health,
                "_database_and_migrations_ready",
                side_effect=DatabaseError(malicious),
            ),
            patch.object(health, "log_event") as safe_log,
        ):
            ready_response = health.ready(self.requests.get("/health/ready"))

        with (
            patch.object(
                health,
                "load_active_publication",
                side_effect=RuntimeError(malicious),
            ),
            patch.object(health, "log_event") as freshness_log,
        ):
            freshness_response = health.freshness(self.requests.get("/health/freshness"))

        for response in (ready_response, freshness_response):
            self.assertEqual(response.status_code, 503)
            body = response.content.decode("utf-8")
            self.assertNotIn("secret-value", body)
            self.assertNotIn("raw-row", body)
            self.assertNotIn("actor", body)
            self.assertNotIn("a" * 64, body)
        self.assertNotIn(malicious, repr(safe_log.call_args))
        self.assertNotIn(malicious, repr(freshness_log.call_args))

    def test_freshness_does_not_call_source_or_serialize_publication_evidence(self) -> None:
        active = SimpleNamespace(
            freshness_state="current",
            revision_id="private-revision-id",
            actor_id="private-actor-id",
            typed_fact_set_sha256="b" * 64,
        )
        with (
            patch.object(health, "load_active_publication", return_value=active),
            patch("grocery.source.client.KamisHttpClient.fetch_recent_prices") as source_fetch,
        ):
            response = health.freshness(self.requests.get("/health/freshness"))

        source_fetch.assert_not_called()
        body = response.content.decode("utf-8")
        self.assertNotIn("private-revision-id", body)
        self.assertNotIn("private-actor-id", body)
        self.assertNotIn("b" * 64, body)
        self.assertEqual(
            set(payload(response)),
            {
                "check",
                "channel",
                "publication_state",
                "freshness_state",
            },
        )

    def test_health_views_reject_unsafe_methods_before_any_probe(self) -> None:
        for view, route in (
            (health.live, "/health/live"),
            (health.ready, "/health/ready"),
            (health.freshness, "/health/freshness"),
        ):
            with self.subTest(route=route):
                with (
                    patch.object(health, "_database_and_migrations_ready") as database_probe,
                    patch.object(health, "load_active_publication") as publication_probe,
                ):
                    response = view(self.requests.post(route))
                self.assertEqual(response.status_code, 405)
                database_probe.assert_not_called()
                publication_probe.assert_not_called()
