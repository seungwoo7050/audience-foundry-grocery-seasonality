import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.db.models.deletion import PROTECT, ProtectedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from grocery.models import (
    PublicationActivation,
    PublicationChannel,
    PublicationRevision,
    ReviewDecision,
    seal_recent_publication,
    transition_recent_publication,
)
from grocery.tests.test_publication_revision_models import (
    create_approved_generation,
    publication_fields,
)
from grocery.tests.test_review_decision_models import record, review_arguments, source_for_parse

EVIDENCE_SHA256 = "8" * 64


def create_publisher(*, active: bool = True, permitted: bool = True) -> Any:
    user_model = get_user_model()
    publisher = user_model.objects.create_user(
        username=f"publisher-{uuid.uuid4()}",
        password=None,
        is_active=active,
    )
    if permitted:
        permission = Permission.objects.get(
            codename="publish_publication",
            content_type__app_label="grocery",
        )
        publisher.user_permissions.add(permission)
        publisher = user_model.objects.get(pk=publisher.pk)
    return publisher


def transition(
    *,
    publisher: Any,
    operation: str,
    target: PublicationRevision | None,
    expected_current: PublicationRevision | None,
    expected_version: int,
    operation_id: uuid.UUID | None = None,
    reason_code: str = "PUBLICATION_APPROVED",
) -> tuple[PublicationActivation, bool]:
    return transition_recent_publication(
        operation_id=operation_id or uuid.uuid4(),
        actor=publisher,
        operation=operation,
        target_revision_id=None if target is None else target.id,
        expected_current_revision_id=(None if expected_current is None else expected_current.id),
        expected_version=expected_version,
        reason_code=reason_code,
        acceptance_evidence_sha256=EVIDENCE_SHA256,
    )


class PublicationActivationTests(TestCase):
    def test_activate_v1_v2_then_append_rollback_to_previously_current_v1(self) -> None:
        decision, _, _ = create_approved_generation()
        v1 = seal_recent_publication(decision.id, "ko-v1")
        v2 = seal_recent_publication(decision.id, "ko-v2")
        publisher = create_publisher()

        activated_v1, created_v1 = transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=v1,
            expected_current=None,
            expected_version=0,
        )
        activated_v2, created_v2 = transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=v2,
            expected_current=v1,
            expected_version=1,
        )
        rollback, created_rollback = transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ROLLBACK,
            target=v1,
            expected_current=v2,
            expected_version=2,
        )

        self.assertTrue(created_v1 and created_v2 and created_rollback)
        self.assertEqual(activated_v1.sequence, 1)
        self.assertIsNone(activated_v1.previous_revision_id)
        self.assertEqual(activated_v1.target_revision_id, v1.id)
        self.assertEqual(activated_v2.sequence, 2)
        self.assertEqual(activated_v2.previous_revision_id, v1.id)
        self.assertEqual(rollback.sequence, 3)
        self.assertEqual(rollback.previous_revision_id, v2.id)
        self.assertEqual(rollback.target_revision_id, v1.id)
        self.assertEqual(
            list(
                PublicationActivation.objects.order_by("sequence").values_list(
                    "operation", flat=True
                )
            ),
            ["ACTIVATE", "ACTIVATE", "ROLLBACK"],
        )
        channel = PublicationChannel.objects.get(pk="RECENT_RETAIL")
        self.assertEqual(channel.version, 3)
        self.assertEqual(channel.current_revision_id, v1.id)

    def test_withdraw_appends_null_target_and_retains_last_revision(self) -> None:
        decision, _, _ = create_approved_generation()
        v1 = seal_recent_publication(decision.id, "ko-v1")
        publisher = create_publisher()
        transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=v1,
            expected_current=None,
            expected_version=0,
        )

        withdrawal, created = transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.WITHDRAW,
            target=None,
            expected_current=v1,
            expected_version=1,
            reason_code="PUBLICATION_WITHDRAWN",
        )

        self.assertTrue(created)
        self.assertEqual(withdrawal.previous_revision_id, v1.id)
        self.assertIsNone(withdrawal.target_revision_id)
        channel = PublicationChannel.objects.get(pk="RECENT_RETAIL")
        self.assertEqual(channel.version, 2)
        self.assertIsNone(channel.current_revision_id)

    def test_exact_uuid_replay_survives_later_transition_and_conflict_is_rejected(self) -> None:
        decision, _, _ = create_approved_generation()
        v1 = seal_recent_publication(decision.id, "ko-v1")
        v2 = seal_recent_publication(decision.id, "ko-v2")
        publisher = create_publisher()
        operation_id = uuid.uuid4()
        first, created = transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=v1,
            expected_current=None,
            expected_version=0,
            operation_id=operation_id,
        )
        transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=v2,
            expected_current=v1,
            expected_version=1,
        )

        replay, replay_created = transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=v1,
            expected_current=None,
            expected_version=0,
            operation_id=operation_id,
        )
        self.assertFalse(replay_created)
        self.assertEqual(replay.id, first.id)
        with self.assertRaisesMessage(ValidationError, "conflicts"):
            transition(
                publisher=publisher,
                operation=PublicationActivation.Operation.ACTIVATE,
                target=v1,
                expected_current=None,
                expected_version=0,
                operation_id=operation_id,
                reason_code="DIFFERENT_EVIDENCE",
            )
        self.assertTrue(created)
        self.assertEqual(PublicationActivation.objects.count(), 2)

    def test_requires_authenticated_active_publisher_with_dedicated_permission(self) -> None:
        decision, _, _ = create_approved_generation()
        revision = seal_recent_publication(decision.id, "ko-v1")
        denied_publishers = (
            create_publisher(permitted=False),
            create_publisher(active=False),
        )
        for publisher in denied_publishers:
            with self.subTest(publisher=publisher.pk):
                with self.assertRaises(PermissionDenied):
                    transition(
                        publisher=publisher,
                        operation=PublicationActivation.Operation.ACTIVATE,
                        target=revision,
                        expected_current=None,
                        expected_version=0,
                    )

        class AnonymousActor:
            pk = None
            is_authenticated = False
            is_active = False

            def has_perm(self, _permission: str) -> bool:
                return False

        with self.assertRaises(PermissionDenied):
            transition(
                publisher=AnonymousActor(),
                operation=PublicationActivation.Operation.ACTIVATE,
                target=revision,
                expected_current=None,
                expected_version=0,
            )
        self.assertFalse(PublicationChannel.objects.exists())
        self.assertFalse(PublicationActivation.objects.exists())

    def test_rejects_unsealed_revoked_noop_and_never_current_rollback_targets(self) -> None:
        decision, snapshots, reviewer = create_approved_generation()
        sealed = seal_recent_publication(decision.id, "ko-v1")
        other = seal_recent_publication(decision.id, "ko-v2")
        unsealed = PublicationRevision.objects.create(
            **publication_fields(
                decision,
                snapshots,
                public_copy_revision="ko-unsealed",
            )
        )
        publisher = create_publisher()

        with self.assertRaisesMessage(ValidationError, "sealed approved"):
            transition(
                publisher=publisher,
                operation=PublicationActivation.Operation.ACTIVATE,
                target=unsealed,
                expected_current=None,
                expected_version=0,
            )

        transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=other,
            expected_current=None,
            expected_version=0,
        )
        with self.assertRaisesMessage(ValidationError, "previously current"):
            transition(
                publisher=publisher,
                operation=PublicationActivation.Operation.ROLLBACK,
                target=sealed,
                expected_current=other,
                expected_version=1,
            )
        with self.assertRaisesMessage(ValidationError, "different target"):
            transition(
                publisher=publisher,
                operation=PublicationActivation.Operation.ACTIVATE,
                target=other,
                expected_current=other,
                expected_version=1,
            )

        source = source_for_parse(decision.parse_run)
        record(
            **review_arguments(
                reviewer=reviewer,
                source_configuration=source,
                parse_run=decision.parse_run,
                decision=ReviewDecision.Decision.REJECT,
                reason_code="APPROVAL_REVOKED",
                approved_mode="",
                approved_coverage_identity="",
                approved_coverage_evidence_revision="",
                supersedes_id=decision.id,
            )
        )
        with self.assertRaisesMessage(ValidationError, "sealed approved"):
            transition(
                publisher=publisher,
                operation=PublicationActivation.Operation.ACTIVATE,
                target=sealed,
                expected_current=other,
                expected_version=1,
            )

        self.assertEqual(PublicationActivation.objects.count(), 1)

    def test_stale_expected_pointer_or_version_rejects_without_event(self) -> None:
        decision, _, _ = create_approved_generation()
        v1 = seal_recent_publication(decision.id, "ko-v1")
        v2 = seal_recent_publication(decision.id, "ko-v2")
        publisher = create_publisher()
        transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=v1,
            expected_current=None,
            expected_version=0,
        )

        stale_cases = ((None, 0), (v1, 0), (None, 1))
        for expected_current, expected_version in stale_cases:
            with self.subTest(
                expected_current=expected_current,
                expected_version=expected_version,
            ):
                with self.assertRaisesMessage(ValidationError, "stale"):
                    transition(
                        publisher=publisher,
                        operation=PublicationActivation.Operation.ACTIVATE,
                        target=v2,
                        expected_current=expected_current,
                        expected_version=expected_version,
                    )
        channel = PublicationChannel.objects.get()
        self.assertEqual((channel.current_revision_id, channel.version), (v1.id, 1))
        self.assertEqual(PublicationActivation.objects.count(), 1)

    def test_injected_pointer_failure_rolls_back_event_and_pointer_together(self) -> None:
        decision, _, _ = create_approved_generation()
        v1 = seal_recent_publication(decision.id, "ko-v1")
        v2 = seal_recent_publication(decision.id, "ko-v2")
        publisher = create_publisher()
        transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=v1,
            expected_current=None,
            expected_version=0,
        )

        with patch(
            "grocery.models._update_recent_publication_pointer",
            side_effect=RuntimeError("injected"),
        ):
            with self.assertRaisesMessage(RuntimeError, "injected"):
                transition(
                    publisher=publisher,
                    operation=PublicationActivation.Operation.ACTIVATE,
                    target=v2,
                    expected_current=v1,
                    expected_version=1,
                )

        channel = PublicationChannel.objects.get()
        self.assertEqual((channel.current_revision_id, channel.version), (v1.id, 1))
        self.assertEqual(PublicationActivation.objects.count(), 1)

    def test_orm_and_direct_sql_guards_reject_out_of_service_mutation(self) -> None:
        decision, _, _ = create_approved_generation()
        v1 = seal_recent_publication(decision.id, "ko-v1")
        v2 = seal_recent_publication(decision.id, "ko-v2")
        publisher = create_publisher()

        with self.assertRaisesMessage(DatabaseError, "transition capability"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO grocery_publicationchannel "
                    "(channel, current_revision_id, version) VALUES (%s, NULL, 0)",
                    ["RECENT_RETAIL"],
                )

        activation, _ = transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=v1,
            expected_current=None,
            expected_version=0,
        )
        channel = PublicationChannel.objects.get()
        with self.assertRaises(ValidationError):
            channel.save()
        with self.assertRaises(ValidationError):
            channel.delete()
        with self.assertRaises(ValidationError):
            activation.save()
        with self.assertRaises(ValidationError):
            activation.delete()

        sql_mutations: tuple[tuple[str, list[Any]], ...] = (
            (
                "UPDATE grocery_publicationchannel SET version = version + 1 WHERE channel = %s",
                ["RECENT_RETAIL"],
            ),
            (
                "DELETE FROM grocery_publicationchannel WHERE channel = %s",
                ["RECENT_RETAIL"],
            ),
            (
                "UPDATE grocery_publicationactivation SET reason_code = %s WHERE id = %s",
                ["MUTATED", activation.id],
            ),
            ("DELETE FROM grocery_publicationactivation WHERE id = %s", [activation.id]),
        )
        for statement, parameters in sql_mutations:
            with self.subTest(statement=statement):
                with self.assertRaises(DatabaseError), transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(statement, parameters)

        direct_id = uuid.uuid4()
        with self.assertRaisesMessage(DatabaseError, "matching transition"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO grocery_publicationactivation "
                    "(id, operation, sequence, reason_code, acceptance_evidence_sha256, "
                    "created_at, previous_revision_id, publisher_id, target_revision_id, "
                    "channel_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        direct_id,
                        "ACTIVATE",
                        2,
                        "DIRECT_INSERT",
                        EVIDENCE_SHA256,
                        timezone.now(),
                        v1.id,
                        publisher.pk,
                        v2.id,
                        "RECENT_RETAIL",
                    ],
                )
        self.assertEqual(PublicationActivation.objects.count(), 1)

    def test_direct_sql_inconsistent_event_cannot_commit_even_with_matching_token(self) -> None:
        decision, _, _ = create_approved_generation()
        v1 = seal_recent_publication(decision.id, "ko-v1")
        v2 = seal_recent_publication(decision.id, "ko-v2")
        publisher = create_publisher()
        transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=v1,
            expected_current=None,
            expected_version=0,
        )
        direct_id = uuid.uuid4()

        with self.assertRaisesMessage(DatabaseError, "inconsistent"), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('grocery.publication_transition_id', %s, true)",
                    [str(direct_id)],
                )
                cursor.execute(
                    "INSERT INTO grocery_publicationactivation "
                    "(id, operation, sequence, reason_code, acceptance_evidence_sha256, "
                    "created_at, previous_revision_id, publisher_id, target_revision_id, "
                    "channel_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        direct_id,
                        "ACTIVATE",
                        2,
                        "DIRECT_INSERT",
                        EVIDENCE_SHA256,
                        timezone.now(),
                        v1.id,
                        publisher.pk,
                        v2.id,
                        "RECENT_RETAIL",
                    ],
                )
                cursor.execute(
                    "SET CONSTRAINTS grocery_publicationactivation_state_consistent IMMEDIATE"
                )

        channel = PublicationChannel.objects.get()
        self.assertEqual((channel.current_revision_id, channel.version), (v1.id, 1))
        self.assertFalse(PublicationActivation.objects.filter(pk=direct_id).exists())

    def test_direct_sql_rejects_previous_target_and_rollback_history_mismatches(self) -> None:
        decision, _, _ = create_approved_generation()
        v1 = seal_recent_publication(decision.id, "ko-v1")
        v2 = seal_recent_publication(decision.id, "ko-v2")
        publisher = create_publisher()
        transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=v1,
            expected_current=None,
            expected_version=0,
        )

        invalid_rows: tuple[tuple[str, uuid.UUID | None, uuid.UUID | None, str], ...] = (
            ("ACTIVATE", None, v2.id, "current pointer"),
            ("ROLLBACK", v1.id, v2.id, "previously current"),
            ("WITHDRAW", v1.id, v2.id, "withdrawal"),
        )
        for operation, previous_id, target_id, expected_error in invalid_rows:
            with self.subTest(operation=operation, previous_id=previous_id, target_id=target_id):
                direct_id = uuid.uuid4()
                with self.assertRaisesMessage(DatabaseError, expected_error), transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT set_config('grocery.publication_transition_id', %s, true)",
                            [str(direct_id)],
                        )
                        cursor.execute(
                            "INSERT INTO grocery_publicationactivation "
                            "(id, operation, sequence, reason_code, "
                            "acceptance_evidence_sha256, created_at, previous_revision_id, "
                            "publisher_id, target_revision_id, channel_id) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            [
                                direct_id,
                                operation,
                                2,
                                "DIRECT_INSERT",
                                EVIDENCE_SHA256,
                                timezone.now(),
                                previous_id,
                                publisher.pk,
                                target_id,
                                "RECENT_RETAIL",
                            ],
                        )

        self.assertEqual(PublicationActivation.objects.count(), 1)
        channel = PublicationChannel.objects.get()
        self.assertEqual((channel.current_revision_id, channel.version), (v1.id, 1))

    def test_activation_schema_is_fixed_typed_and_foreign_keys_are_protected(self) -> None:
        channel_fields = {field.name for field in PublicationChannel._meta.fields}
        activation_fields = {field.name for field in PublicationActivation._meta.fields}
        self.assertFalse(
            {"payload", "data", "actor_name", "actor_email", "username", "STATIC_MONTHLY"}
            & (channel_fields | activation_fields)
        )
        self.assertEqual(
            list(PublicationActivation.Operation), ["ACTIVATE", "ROLLBACK", "WITHDRAW"]
        )
        for field_name in (
            "channel",
            "previous_revision",
            "target_revision",
            "publisher",
        ):
            self.assertEqual(
                cast(
                    Any,
                    PublicationActivation._meta.get_field(field_name).remote_field,
                ).on_delete,
                PROTECT,
            )
        self.assertEqual(
            cast(
                Any,
                PublicationChannel._meta.get_field("current_revision").remote_field,
            ).on_delete,
            PROTECT,
        )

        decision, _, _ = create_approved_generation()
        revision = seal_recent_publication(decision.id, "ko-v1")
        publisher = create_publisher()
        transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=revision,
            expected_current=None,
            expected_version=0,
        )
        with self.assertRaises(ProtectedError):
            get_user_model().objects.filter(pk=publisher.pk).delete()


class ConcurrentPublicationActivationTests(TransactionTestCase):
    reset_sequences = True

    def test_two_same_cas_transitions_serialize_to_one_success_and_one_stale(self) -> None:
        decision, _, _ = create_approved_generation()
        v1 = seal_recent_publication(decision.id, "ko-v1")
        v2 = seal_recent_publication(decision.id, "ko-v2")
        v3 = seal_recent_publication(decision.id, "ko-v3")
        publisher = create_publisher()
        transition(
            publisher=publisher,
            operation=PublicationActivation.Operation.ACTIVATE,
            target=v1,
            expected_current=None,
            expected_version=0,
        )
        barrier = threading.Barrier(3)

        def attempt(target_id: uuid.UUID) -> str:
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                thread_publisher = get_user_model().objects.get(pk=publisher.pk)
                try:
                    transition_recent_publication(
                        operation_id=uuid.uuid4(),
                        actor=thread_publisher,
                        operation=PublicationActivation.Operation.ACTIVATE,
                        target_revision_id=target_id,
                        expected_current_revision_id=v1.id,
                        expected_version=1,
                        reason_code="CONCURRENT_ACTIVATION",
                        acceptance_evidence_sha256=EVIDENCE_SHA256,
                    )
                except ValidationError:
                    return "stale"
                return "created"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (executor.submit(attempt, v2.id), executor.submit(attempt, v3.id))
            barrier.wait(timeout=10)
            outcomes = sorted(future.result(timeout=20) for future in futures)

        self.assertEqual(outcomes, ["created", "stale"])
        channel = PublicationChannel.objects.get(pk="RECENT_RETAIL")
        self.assertEqual(channel.version, 2)
        self.assertIn(channel.current_revision_id, (v2.id, v3.id))
        self.assertEqual(PublicationActivation.objects.count(), 2)
