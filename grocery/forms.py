import unicodedata
from typing import Any

from django import forms
from django.core.exceptions import ValidationError

QUERY_MAX_LENGTH = 80
CATEGORY_CHOICES = (
    ("", "전체"),
    ("vegetable", "채소류"),
    ("fruit", "과일류"),
)


class OfficialItemQueryField(forms.CharField):
    """Normalize a public item-name query without accepting hidden controls."""

    default_error_messages = {
        **forms.CharField.default_error_messages,
        "unsafe": "검색어에는 줄바꿈이나 제어 문자를 사용할 수 없습니다.",
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Inspect the original value before trimming so an edge CR/LF cannot be
        # normalized away and accepted as an ordinary query.
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


class SearchForm(forms.Form):
    """Validate the bounded public catalog GET query."""

    q = OfficialItemQueryField(
        label="공식 품목명",
        required=False,
        max_length=QUERY_MAX_LENGTH,
        error_messages={
            "max_length": "검색어는 80자 이하여야 합니다.",
        },
    )
    category = forms.ChoiceField(
        label="부류",
        required=False,
        choices=CATEGORY_CHOICES,
        error_messages={
            "invalid_choice": "부류 선택을 확인해 주세요.",
        },
    )
