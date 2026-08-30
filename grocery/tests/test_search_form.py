import pytest

from grocery.forms import QUERY_MAX_LENGTH, SearchForm


@pytest.mark.parametrize("category", ["", "vegetable", "fruit"])
def test_accepts_korean_query_and_supported_category(category: str) -> None:
    form = SearchForm({"q": "  배추  ", "category": category})

    assert form.is_valid(), form.errors.as_data()
    assert form.cleaned_data == {"q": "배추", "category": category}


def test_accepts_query_at_exact_length_boundary() -> None:
    query = "가" * QUERY_MAX_LENGTH
    form = SearchForm({"q": query, "category": "vegetable"})

    assert form.is_valid(), form.errors.as_data()
    assert form.cleaned_data["q"] == query


def test_rejects_query_over_length_boundary_with_korean_error() -> None:
    form = SearchForm({"q": "가" * (QUERY_MAX_LENGTH + 1), "category": ""})

    assert not form.is_valid()
    assert list(form.errors["q"]) == ["검색어는 80자 이하여야 합니다."]


def test_unknown_category_keeps_normalized_safe_query() -> None:
    form = SearchForm({"q": "  사과  ", "category": "unknown"})

    assert not form.is_valid()
    assert form.cleaned_data["q"] == "사과"
    assert list(form.errors["category"]) == ["부류 선택을 확인해 주세요."]
    assert "unknown" not in str(form.errors)


@pytest.mark.parametrize(
    "query",
    [
        "배추\r",
        "배추\n사과",
        "배추\x00사과",
        "배추\t사과",
        "\u200b",
        "\u2028",
    ],
)
def test_rejects_control_and_hidden_separator_input(query: str) -> None:
    form = SearchForm({"q": query, "category": "fruit"})

    assert not form.is_valid()
    assert list(form.errors["q"]) == ["검색어에는 줄바꿈이나 제어 문자를 사용할 수 없습니다."]
    assert "q" not in form.cleaned_data
    assert form.cleaned_data["category"] == "fruit"


def test_whitespace_only_query_normalizes_to_optional_empty_value() -> None:
    form = SearchForm({"q": "   ", "category": ""})

    assert form.is_valid(), form.errors.as_data()
    assert form.cleaned_data["q"] == ""


def test_errors_do_not_echo_provider_or_secret_like_input() -> None:
    marker = "SERVICE_KEY_IS_NULL KAMIS_API_KEY=synthetic-marker"
    form = SearchForm({"q": f"{marker}\x00", "category": f"{marker}-category"})

    assert not form.is_valid()
    rendered_errors = str(form.errors)
    assert marker not in rendered_errors
    assert "synthetic-marker" not in rendered_errors
    assert list(form.errors["q"]) == ["검색어에는 줄바꿈이나 제어 문자를 사용할 수 없습니다."]
    assert list(form.errors["category"]) == ["부류 선택을 확인해 주세요."]
