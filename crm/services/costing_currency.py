from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANT = Decimal("0.01")


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
