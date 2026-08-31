from django.utils import timezone

from grocery.models import FetchAttempt, SourceArtifact, SourceConfiguration, build_source_artifact
from grocery.tests.test_acquisition_models import create_fetch_attempt, create_page_receipt


def create_scoped_artifact(
    source: SourceConfiguration,
    scope_sha256: str,
    *,
    row_count: int = 1,
) -> SourceArtifact:
    attempt = create_fetch_attempt(source, request_scope_sha256=scope_sha256)
    create_page_receipt(
        attempt,
        declared_total_count=row_count,
        received_row_count=row_count,
        body_byte_length=10,
        body_sha256=scope_sha256,
    )
    attempt.state = FetchAttempt.State.SUCCEEDED
    attempt.completed_at = timezone.now()
    attempt.received_page_count = 1
    attempt.received_row_count = row_count
    attempt.received_byte_count = 10
    attempt.save()
    artifact, _created = build_source_artifact(attempt.id)
    return artifact
