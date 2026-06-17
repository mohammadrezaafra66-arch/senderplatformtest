from core_engine.services.utils import is_valid_phone, normalize_phone_number, sum_numbers


def test_normalize_phone_number_removes_spaces_and_dashes():
    assert normalize_phone_number("0912-345 6789") == "09123456789"


def test_normalize_phone_number_removes_plus_and_parentheses():
    assert normalize_phone_number("+98 (912) 345-6789") == "989123456789"


def test_is_valid_phone_accepts_ten_digit_number():
    assert is_valid_phone("09123456789") is True


def test_is_valid_phone_rejects_too_short():
    assert is_valid_phone("123") is False


def test_is_valid_phone_rejects_too_long():
    assert is_valid_phone("12345678901234567890") is False


def test_sum_numbers():
    assert sum_numbers(3, 4) == 7
