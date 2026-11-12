import pytest

from fde.normalize import AmountParseError, parse_amount


@pytest.mark.parametrize("raw,expected", [
    ("1000", 1000), ("1,234,567", 1234567), ("-500", -500), ("-1,200", -1200), ("0", 0),
    (" 42 ", 42), ("123456789012345678", 123456789012345678),  # float 정밀도 한계를 넘는 정수도 정확
])
def test_parse_valid(raw, expected):
    v = parse_amount(raw)
    assert v == expected and isinstance(v, int)


@pytest.mark.parametrize("raw", [None, "", "  ", "-"])
def test_blank_and_dash_are_none_not_zero(raw):
    assert parse_amount(raw) is None


@pytest.mark.parametrize("raw", ["12.5", "(100)", "abc", "1e5", "--1"])
def test_invalid_raises(raw):
    with pytest.raises(AmountParseError):
        parse_amount(raw)
