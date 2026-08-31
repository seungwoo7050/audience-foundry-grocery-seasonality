"""Strict, non-reflecting validation for the public GET state."""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar

from django import forms
from django.core.exceptions import ValidationError
from django.http import QueryDict

QUERY_MAX_LENGTH = 80
SELECTION_LIMIT = 5
CATEGORY_CHOICES = (("", "전체"), ("vegetable", "채소류"), ("fruit", "과일류"))
PERIOD_CHOICES = (("week", "1주 비교"), ("month", "1개월 비교"), ("year", "1년 비교"))
DIRECTION_CHOICES = (
    ("all", "전체"),
    ("lower", "낮음"),
    ("equal", "같음"),
    ("higher", "높음"),
    ("unavailable", "비교값 없음"),
)
SORT_CHOICES = (
    ("name", "품목명 순"),
    ("change_asc", "변화율 낮은 순"),
    ("change_desc", "변화율 높은 순"),
)
RANGE_CHOICES = (("12", "12개월"), ("36", "36개월"), ("60", "60개월"))


class OfficialItemQueryField(forms.CharField):
    """Normalize a public item-name query without accepting hidden controls."""

    default_error_messages = {
        **forms.CharField.default_error_messages,
        "unsafe": "품목명은 한 줄로 입력하세요.",
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["strip"] = False
        super().__init__(*args, **kwargs)

    def to_python(self, value: object) -> str:
        raw_value = super().to_python(value)
        if raw_value is None:
            return ""
        if any(
            character in "\r\n" or unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
            for character in raw_value
        ):
            raise ValidationError(self.error_messages["unsafe"], code="unsafe")
        return raw_value.strip()


class CanonicalPageField(forms.RegexField):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("regex", r"^(?:[1-9]|[1-9][0-9]|100)$")
        kwargs.setdefault("required", False)
        kwargs.setdefault("initial", "1")
        kwargs.setdefault("error_messages", {"invalid": "페이지를 확인하세요."})
        super().__init__(*args, **kwargs)

    def clean(self, value: object) -> int:
        cleaned = super().clean(value)
        return int(cleaned or "1")


class CanonicalUUIDField(forms.RegexField):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault(
            "regex",
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        )
        kwargs.setdefault("required", False)
        kwargs.setdefault("error_messages", {"invalid": "지역을 확인하세요."})
        super().__init__(*args, **kwargs)

    def clean(self, value: object) -> uuid.UUID | None:
        cleaned = super().clean(value)
        return uuid.UUID(cleaned) if cleaned else None


class CanonicalDateField(forms.RegexField):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("regex", r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])$")
        kwargs.setdefault("required", False)
        kwargs.setdefault("error_messages", {"invalid": "조사일을 확인하세요."})
        super().__init__(*args, **kwargs)

    def clean(self, value: object) -> date | None:
        cleaned = super().clean(value)
        if not cleaned:
            return None
        try:
            parsed = date.fromisoformat(cleaned)
        except ValueError:
            raise ValidationError(self.error_messages["invalid"], code="invalid") from None
        if parsed.isoformat() != cleaned:
            raise ValidationError(self.error_messages["invalid"], code="invalid")
        return parsed


class StrictQueryForm(forms.Form):
    """Reject unknown and duplicate public parameters without echoing their values."""

    allowed_parameters: ClassVar[frozenset[str]] = frozenset()

    def clean(self) -> dict[str, object]:
        cleaned = super().clean() or {}
        data = self.data
        if isinstance(data, QueryDict):
            if set(data) - self.allowed_parameters:
                raise ValidationError("요청 항목을 확인하세요.", code="unknown_parameter")
            for name in self.allowed_parameters:
                if len(data.getlist(name)) > 1:
                    self.add_error(
                        name if name in self.fields else None,
                        ValidationError("한 번만 선택해 주세요.", code="duplicate_parameter"),
                    )
        return cleaned


class CatalogForm(StrictQueryForm):
    allowed_parameters = frozenset({"q", "category", "period", "direction", "sort", "page"})

    q = OfficialItemQueryField(
        label="공식 품목명",
        required=False,
        max_length=QUERY_MAX_LENGTH,
        error_messages={"max_length": "품목명은 80자 이하로 입력하세요."},
    )
    category = forms.ChoiceField(
        label="부류",
        required=False,
        choices=CATEGORY_CHOICES,
        error_messages={"invalid_choice": "부류 선택을 확인해 주세요."},
    )
    period = forms.ChoiceField(
        label="비교 기간",
        required=False,
        choices=PERIOD_CHOICES,
        initial="week",
        error_messages={"invalid_choice": "비교 기간을 확인하세요."},
    )
    direction = forms.ChoiceField(
        label="변화 방향",
        required=False,
        choices=DIRECTION_CHOICES,
        initial="all",
        error_messages={"invalid_choice": "변화 방향을 확인하세요."},
    )
    sort = forms.ChoiceField(
        label="표시 순서",
        required=False,
        choices=SORT_CHOICES,
        initial="name",
        error_messages={"invalid_choice": "표시 순서를 확인하세요."},
    )
    page = CanonicalPageField(label="페이지")

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        if "period" not in self.errors:
            cleaned["period"] = cleaned.get("period") or "week"
        if "direction" not in self.errors:
            cleaned["direction"] = cleaned.get("direction") or "all"
        if "sort" not in self.errors:
            cleaned["sort"] = cleaned.get("sort") or "name"
        if cleaned.get("q") and cleaned.get("page", 1) != 1:
            self.add_error("page", "검색 결과는 첫 페이지만 표시합니다.")
        return cleaned


class SearchForm(forms.Form):
    """Preserve the Phase 0 two-field form for internal callers and regression tests."""

    q = OfficialItemQueryField(
        label="공식 품목명",
        required=False,
        max_length=QUERY_MAX_LENGTH,
        error_messages={"max_length": "품목명은 80자 이하로 입력하세요."},
    )
    category = forms.ChoiceField(
        label="부류",
        required=False,
        choices=CATEGORY_CHOICES,
        error_messages={"invalid_choice": "부류 선택을 확인해 주세요."},
    )


class HistoryForm(StrictQueryForm):
    allowed_parameters = frozenset({"range", "region"})
    range = forms.ChoiceField(
        required=False,
        choices=RANGE_CHOICES,
        initial="36",
        error_messages={"invalid_choice": "표시 기간을 확인하세요."},
    )
    region = CanonicalUUIDField()

    def clean(self) -> dict[str, object]:
        cleaned = super().clean()
        cleaned["range"] = cleaned.get("range") or "36"
        return cleaned


class RegionsForm(StrictQueryForm):
    allowed_parameters = frozenset({"date"})
    date = CanonicalDateField()


class MarketsForm(StrictQueryForm):
    allowed_parameters = frozenset({"date", "page"})
    date = CanonicalDateField()
    page = CanonicalPageField(label="페이지")


@dataclass(frozen=True, slots=True)
class SelectionQuery:
    series_ids: tuple[uuid.UUID, ...]


def parse_selection_query(data: QueryDict | Mapping[str, object]) -> SelectionQuery:
    """Validate the sole repeatable public parameter and preserve first-seen order."""

    if not isinstance(data, QueryDict):
        query = QueryDict(mutable=True)
        for key, value in data.items():
            query.appendlist(key, str(value))
        data = query
    if set(data) - {"series"}:
        raise ValidationError("요청 항목을 확인하세요.", code="unknown_parameter")
    raw_values = data.getlist("series")
    if len(raw_values) > SELECTION_LIMIT:
        raise ValidationError("품목은 최대 다섯 개까지 선택할 수 있습니다.", code="selection_limit")
    parsed: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw_value in raw_values:
        try:
            value = uuid.UUID(raw_value)
        except (AttributeError, ValueError):
            raise ValidationError("선택한 품목을 확인하세요.", code="invalid_series") from None
        if str(value) != raw_value:
            raise ValidationError("선택한 품목을 확인하세요.", code="invalid_series")
        if value not in seen:
            seen.add(value)
            parsed.append(value)
    if len(parsed) > SELECTION_LIMIT:
        raise ValidationError("품목은 최대 다섯 개까지 선택할 수 있습니다.", code="selection_limit")
    return SelectionQuery(tuple(parsed))
