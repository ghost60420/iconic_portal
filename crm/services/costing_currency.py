from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANT = Decimal("0.01")
SUPPORTED_COSTING_CURRENCIES = {"BDT", "CAD", "USD"}
CURRENCY_DISPLAY_ORDER = ("CAD", "USD", "BDT")


def _to_decimal(value):
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except Exception:
        return Decimal("0")


def _format_decimal(value):
    rounded = _to_decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    return f"{rounded:.2f}"


def format_money(value, symbol):
    return f"{symbol} {_format_decimal(value)}"


def normalize_costing_currency(currency):
    code = (currency or "BDT").upper().strip()
    if code in SUPPORTED_COSTING_CURRENCIES:
        return code
    return "BDT"


def format_costing_money(value, currency):
    return f"{normalize_costing_currency(currency)} {_format_decimal(value)}"


def normalize_finance_currency(currency):
    code = (currency or "").upper().strip()
    if code in {"CAD", "USD", "BDT"}:
        return code
    return code or ""


def format_finance_money(value, currency):
    code = normalize_finance_currency(currency)
    rounded = _to_decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    amount = f"{rounded:,.2f}"
    if code == "CAD":
        return f"CAD ${amount}"
    if code == "USD":
        return f"USD ${amount}"
    if code == "BDT":
        return f"\u09F3{amount} BDT"
    if code:
        return f"{code} {amount}"
    return amount


def format_compact_finance_money(value, currency):
    """Format a dashboard value without changing the underlying numeric value."""
    code = normalize_finance_currency(currency)
    amount = _to_decimal(value)
    absolute = abs(amount)
    divisor = Decimal("1")
    suffix = ""
    for threshold, candidate in (
        (Decimal("1000000000"), "B"),
        (Decimal("1000000"), "M"),
        (Decimal("1000"), "K"),
    ):
        if absolute >= threshold:
            divisor = threshold
            suffix = candidate
            break

    scaled = absolute / divisor
    if suffix:
        decimals = 2 if scaled < 10 else 1 if scaled < 100 else 0
        rendered = f"{scaled:.{decimals}f}"
    else:
        rendered = f"{scaled:,.2f}"

    sign = "-" if amount < 0 else ""
    if code == "CAD":
        return f"CAD {sign}${rendered}{suffix}"
    if code == "USD":
        return f"USD {sign}${rendered}{suffix}"
    if code == "BDT":
        return f"{sign}\u09F3{rendered}{suffix}"
    if code:
        return f"{code} {sign}{rendered}{suffix}"
    return f"{sign}{rendered}{suffix}"


def currency_summary_rows(totals_by_currency, value_keys=("amount",)):
    """Return deterministic rows while preserving native-currency separation."""
    totals_by_currency = totals_by_currency or {}
    ordered = [code for code in CURRENCY_DISPLAY_ORDER if code in totals_by_currency]
    ordered.extend(sorted(code for code in totals_by_currency if code not in CURRENCY_DISPLAY_ORDER))
    rows = []
    for code in ordered:
        values = totals_by_currency[code]
        row = {"currency": code}
        for key in value_keys:
            row[key] = _to_decimal(values.get(key))
        rows.append(row)
    return rows


def format_bdt(value):
    return format_money(value, "\u09F3")


def format_cad(value):
    return format_money(value, "$")


def cad_from_bdt(bdt_value, exchange_rate_bdt_per_cad):
    rate = _to_decimal(exchange_rate_bdt_per_cad)
    if rate <= 0:
        return None
    return _to_decimal(bdt_value) / rate


def format_cad_from_bdt(bdt_value, exchange_rate_bdt_per_cad):
    cad_value = cad_from_bdt(bdt_value, exchange_rate_bdt_per_cad)
    if cad_value is None:
        return None
    return format_cad(cad_value)
