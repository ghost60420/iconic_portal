from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANT = Decimal("0.01")
SUPPORTED_COSTING_CURRENCIES = {"BDT", "CAD", "USD"}
CURRENCY_DISPLAY_ORDER = ("CAD", "USD", "BDT")


class CurrencyConversionError(ValueError):
    """Raised when a requested currency conversion has no valid stored rate."""


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
    return f"{rounded:,.2f}"


def format_money(value, symbol):
    return f"{symbol}{_format_decimal(value)}"


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
        return f"\u09F3{amount}"
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
    return f"CAD ${_format_decimal(value)}"


def convert_currency(
    value,
    source_currency,
    target_currency,
    *,
    bdt_per_cad=None,
    cad_per_usd=None,
    bdt_per_usd=None,
    stored_rate_to_cad=None,
    stored_rate_to_bdt=None,
    quantize=MONEY_QUANT,
):
    """Convert money with explicit, direction-safe rate semantics.

    ``bdt_per_cad`` is BDT for one CAD. ``cad_per_usd`` is CAD for one
    USD. ``bdt_per_usd`` is BDT for one USD. The stored-rate arguments are
    compatibility inputs for AccountingEntry and InvoicePayment. For BDT/CAD,
    every accepted rate has one meaning only: BDT per one CAD.
    """
    amount = _to_decimal(value)
    source = normalize_finance_currency(source_currency)
    target = normalize_finance_currency(target_currency)
    if source not in SUPPORTED_COSTING_CURRENCIES or target not in SUPPORTED_COSTING_CURRENCIES:
        raise CurrencyConversionError(f"Unsupported currency conversion: {source or '?'} to {target or '?'}.")
    if source == target:
        result = amount
    elif {source, target} == {"BDT", "CAD"}:
        rate = _to_decimal(bdt_per_cad)
        stored_cad = _to_decimal(stored_rate_to_cad)
        stored_bdt = _to_decimal(stored_rate_to_bdt)
        if rate <= 0:
            if source == "BDT" and stored_cad > 0:
                rate = stored_cad
            elif source == "CAD" and stored_bdt > 0:
                rate = stored_bdt
        if rate <= 1:
            raise CurrencyConversionError("A valid BDT-per-CAD exchange rate greater than one is required.")
        result = amount / rate if source == "BDT" else amount * rate
    elif {source, target} == {"USD", "CAD"}:
        rate = _to_decimal(cad_per_usd)
        if rate <= 0:
            rate = _to_decimal(stored_rate_to_cad)
        if rate <= 0:
            raise CurrencyConversionError("A positive CAD-per-USD exchange rate is required.")
        result = amount * rate if source == "USD" else amount / rate
    elif {source, target} == {"USD", "BDT"}:
        direct_rate = _to_decimal(bdt_per_usd)
        if direct_rate <= 0:
            direct_rate = _to_decimal(stored_rate_to_bdt)
        if direct_rate > 0:
            result = amount * direct_rate if source == "USD" else amount / direct_rate
        else:
            bdt_cad_rate = _to_decimal(bdt_per_cad)
            usd_cad_rate = _to_decimal(cad_per_usd)
            if usd_cad_rate <= 0:
                usd_cad_rate = _to_decimal(stored_rate_to_cad)
            if bdt_cad_rate <= 0 or usd_cad_rate <= 0:
                raise CurrencyConversionError(
                    "A positive BDT-per-USD rate or both BDT-per-CAD and CAD-per-USD rates are required."
                )
            result = (
                amount * usd_cad_rate * bdt_cad_rate
                if source == "USD"
                else amount / bdt_cad_rate / usd_cad_rate
            )
    else:  # pragma: no cover - guarded by the supported currency set
        raise CurrencyConversionError(f"Unsupported currency conversion: {source} to {target}.")

    if quantize is None:
        return result
    return result.quantize(quantize, rounding=ROUND_HALF_UP)


def format_cad_from_bdt(bdt_value, exchange_rate_bdt_per_cad):
    return format_cad(
        convert_currency(
            bdt_value,
            "BDT",
            "CAD",
            bdt_per_cad=exchange_rate_bdt_per_cad,
        )
    )
