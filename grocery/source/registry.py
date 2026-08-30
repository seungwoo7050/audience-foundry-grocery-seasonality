"""Reviewed initial KAMIS retail series allowlist.

These ten normalized identity facts were checked against the official code workbook
and the bounded live canary on 2026-08-30. They are not a copy of an API response.
Adding a series requires a new reviewed evidence revision.
"""

import hashlib
import json

from grocery.source.kamis import (
    IdentityContractEvidence,
    build_identity_registry_from_reviewed_evidence,
)

OFFICIAL_DOCS_ZIP_SHA256 = "07417ea9eb882a33615721256ff8be3b131cdb10bbc9c7b40472bf049a7e0f88"
IDENTITY_EVIDENCE_REVISION = "15156063-codebook-live-canary-2026-08-30-v1"

ITEM_NAMES = {
    ("200", "212"): "양배추",
    ("200", "213"): "시금치",
    ("200", "214"): "상추",
    ("200", "215"): "얼갈이배추",
    ("400", "411"): "사과",
    ("400", "414"): "포도",
    ("400", "419"): "참다래",
    ("400", "420"): "파인애플",
    ("400", "430"): "아보카도",
}

VARIETY_NAMES = {
    ("200", "212", "00"): "양배추",
    ("200", "213", "00"): "시금치",
    ("200", "214", "01"): "적",
    ("200", "214", "02"): "청",
    ("200", "215", "00"): "얼갈이배추",
    ("400", "411", "06"): "쓰가루(아오리)",
    ("400", "414", "12"): "샤인머스켓",
    ("400", "419", "02"): "그린 뉴질랜드",
    ("400", "420", "02"): "수입",
    ("400", "430", "00"): "수입",
}

GRADE_NAMES = {
    ("200", "212", "00", "04"): "상품",
    ("200", "213", "00", "04"): "상품",
    ("200", "214", "01", "04"): "상품",
    ("200", "214", "02", "04"): "상품",
    ("200", "215", "00", "04"): "상품",
    ("400", "411", "06", "04"): "상품",
    ("400", "414", "12", "24"): "L과",
    ("400", "419", "02", "04"): "상품",
    ("400", "420", "02", "04"): "상품",
    ("400", "430", "00", "04"): "상품",
}

UNITS = {
    ("200", "212", "00", "04"): ("포기", "1"),
    ("200", "213", "00", "04"): ("g", "100"),
    ("200", "214", "01", "04"): ("g", "100"),
    ("200", "214", "02", "04"): ("g", "100"),
    ("200", "215", "00", "04"): ("kg", "1"),
    ("400", "411", "06", "04"): ("개", "10"),
    ("400", "414", "12", "24"): ("kg", "2"),
    ("400", "419", "02", "04"): ("개", "10"),
    ("400", "420", "02", "04"): ("개", "1"),
    ("400", "430", "00", "04"): ("개", "1"),
}


def _unit_contract_hash() -> str:
    rows = [
        [*series_key, unit, unit_size] for series_key, (unit, unit_size) in sorted(UNITS.items())
    ]
    canonical = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


INITIAL_RETAIL_IDENTITY_REGISTRY = build_identity_registry_from_reviewed_evidence(
    item_names=ITEM_NAMES,
    variety_names=VARIETY_NAMES,
    grade_names=GRADE_NAMES,
    units=UNITS,
    evidence=IdentityContractEvidence(
        codebook_sha256=OFFICIAL_DOCS_ZIP_SHA256,
        unit_contract_sha256=_unit_contract_hash(),
        coverage_evidence_revision=IDENTITY_EVIDENCE_REVISION,
    ),
)
