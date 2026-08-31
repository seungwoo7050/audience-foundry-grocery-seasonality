from django.db.models import PROTECT

from grocery.historical_publication_models import HistoricalRetailPublicationRevision


def test_historical_revision_owns_three_protected_review_boundaries() -> None:
    revision = HistoricalRetailPublicationRevision

    assert revision.FACT_HASH_VERSION == "historical-retail-bundle-v1"
    assert revision.COPY_REVISION == "ko-v4"
    for field_name in ("monthly_review", "regional_review", "market_review"):
        field = revision._meta.get_field(field_name)
        assert field.remote_field.on_delete is PROTECT
