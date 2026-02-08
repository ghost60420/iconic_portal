import re


def normalize_phone(raw: str, default_country: str = "1") -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("1") and len(digits) == 11:
        return digits
    if len(digits) == 10:
        return default_country + digits
    return digits
