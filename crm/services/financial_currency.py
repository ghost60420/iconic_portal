from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib.contenttypes.models import ContentType

from crm.models import CurrencyReviewItem, HistoricalExchangeRate


MONEY = Decimal("0.01")
RATE = Decimal("0.0000000001")
SUPPORTED = {"CAD", "USD", "BDT"}


class CurrencySnapshotError(ValueError):
    pass


class MissingExchangeRate(CurrencySnapshotError):
    def __init__(self, message, missing_pairs=()):
        super().__init__(message)
        self.missing_pairs = tuple(missing_pairs)


@dataclass(frozen=True)
class CurrencySnapshot:
    native_amount: Decimal
    currency: str
    rate_to_cad: Decimal
    rate_to_bdt: Decimal
    amount_cad: Decimal
    amount_bdt: Decimal
    rate_path: tuple[str, ...] = ()


def decimal_value(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CurrencySnapshotError("Amount and rate values must be valid decimals.") from exc


def money(value) -> Decimal:
    return decimal_value(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _positive_rate(value, label) -> Decimal:
    rate = decimal_value(value)
    if rate <= 0:
        raise CurrencySnapshotError(f"{label} must be greater than zero.")
    return rate.quantize(RATE, rounding=ROUND_HALF_UP)


def _approved_rates(rate_date):
    rows = HistoricalExchangeRate.objects.filter(rate_date=rate_date, is_approved=True).values_list(
        "source_currency", "target_currency", "rate"
    )
    result = {}
    for source, target, rate in rows:
        result[(source, target)] = decimal_value(rate)
        result[(target, source)] = Decimal("1") / decimal_value(rate)
    return result


def _record_review(source_record, *, transaction_date, currency, amount, missing_pairs, actor=None):
    if source_record is None or not getattr(source_record, "pk", None):
        return None
    content_type = ContentType.objects.get_for_model(source_record, for_concrete_model=False)
    required = missing_pairs[0][1] if missing_pairs else "CAD"
    reason = "Missing approved historical exchange rate: " + ", ".join(
        f"{source}->{target}" for source, target in missing_pairs
    )
    review, _created = CurrencyReviewItem.objects.get_or_create(
        content_type=content_type,
        object_id=source_record.pk,
        transaction_date=transaction_date,
        source_currency=currency,
        required_currency=required,
        state=CurrencyReviewItem.STATE_OPEN,
        defaults={
            "native_amount": amount,
            "reason": reason,
            "created_by": actor if actor and getattr(actor, "is_authenticated", False) else None,
        },
    )
    return review


def resolve_currency_snapshot(
    *,
    native_amount,
    currency,
    transaction_date,
    rate_to_cad=None,
    rate_to_bdt=None,
    source_record=None,
    actor=None,
    create_review=True,
) -> CurrencySnapshot:
    amount = money(native_amount)
    currency = (currency or "").upper().strip()
    if amount <= 0:
        raise CurrencySnapshotError("Transaction amount must be greater than zero.")
    if currency not in SUPPORTED:
        raise CurrencySnapshotError(f"Unsupported currency '{currency}'.")
    if not transaction_date:
        raise CurrencySnapshotError("A transaction date is required for a currency snapshot.")

    supplied_cad = rate_to_cad not in (None, "", 0, Decimal("0"))
    supplied_bdt = rate_to_bdt not in (None, "", 0, Decimal("0"))
    rates = None
    path = []

    if currency == "CAD":
        cad_rate = Decimal("1")
    elif supplied_cad:
        cad_rate = _positive_rate(rate_to_cad, "rate_to_cad")
        path.append(f"SUPPLIED:{currency}->CAD")
    else:
        rates = _approved_rates(transaction_date)
        cad_rate = rates.get((currency, "CAD"))

    if currency == "BDT":
        bdt_rate = Decimal("1")
    elif supplied_bdt:
        bdt_rate = _positive_rate(rate_to_bdt, "rate_to_bdt")
        path.append(f"SUPPLIED:{currency}->BDT")
    else:
        rates = rates if rates is not None else _approved_rates(transaction_date)
        bdt_rate = rates.get((currency, "BDT"))

    if cad_rate is None and currency == "BDT" and bdt_rate:
        rates = rates if rates is not None else _approved_rates(transaction_date)
        cad_rate = rates.get(("BDT", "CAD"))
    if bdt_rate is None and currency == "CAD" and cad_rate:
        rates = rates if rates is not None else _approved_rates(transaction_date)
        bdt_rate = rates.get(("CAD", "BDT"))

    if currency == "USD" and cad_rate is not None and bdt_rate is None:
        rates = rates if rates is not None else _approved_rates(transaction_date)
        cad_to_bdt = rates.get(("CAD", "BDT"))
        if cad_to_bdt:
            bdt_rate = cad_rate * cad_to_bdt
            path.append("DERIVED:USD->CAD->BDT")
    if currency == "USD" and bdt_rate is not None and cad_rate is None:
        rates = rates if rates is not None else _approved_rates(transaction_date)
        bdt_to_cad = rates.get(("BDT", "CAD"))
        if bdt_to_cad:
            cad_rate = bdt_rate * bdt_to_cad
            path.append("DERIVED:USD->BDT->CAD")

    missing = []
    if cad_rate is None:
        missing.append((currency, "CAD"))
    if bdt_rate is None:
        missing.append((currency, "BDT"))
    if missing:
        if create_review:
            _record_review(
                source_record,
                transaction_date=transaction_date,
                currency=currency,
                amount=amount,
                missing_pairs=missing,
                actor=actor,
            )
        raise MissingExchangeRate(
            "Approved historical conversion evidence is missing; transaction remains unposted.",
            missing_pairs=missing,
        )

    cad_rate = _positive_rate(cad_rate, "rate_to_cad")
    bdt_rate = _positive_rate(bdt_rate, "rate_to_bdt")
    if not path:
        path = [f"DATED:{currency}->CAD", f"DATED:{currency}->BDT"]
    return CurrencySnapshot(
        native_amount=amount,
        currency=currency,
        rate_to_cad=cad_rate,
        rate_to_bdt=bdt_rate,
        amount_cad=money(amount * cad_rate),
        amount_bdt=money(amount * bdt_rate),
        rate_path=tuple(path),
    )
