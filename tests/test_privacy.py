from core import privacy


def test_card_detection_uses_luhn_and_iin():
    assert privacy.find_card_numbers("card 4111 1111 1111 1111 ok")
    assert not privacy.find_card_numbers("order 1234567890123456")       # fails Luhn/IIN
    assert not privacy.find_card_numbers("tracking 9400111899223344556677")  # 22 digits


def test_redact():
    out = privacy.redact("Card 4111-1111-1111-1111, SSN 123-45-6789, password: hunter2, PIN: 4321")
    assert "4111-1111" not in out and "123-45-6789" not in out and "hunter2" not in out and "4321" not in out
    assert "****1111" in out


def test_masks():
    assert privacy.mask_account("Account 9876543210") == "****3210"
    assert privacy.mask_address("Seattle", "98101") == "Seattle, 98101"
    assert privacy.mask_email("alex@example.com") == "a***@example.com"
