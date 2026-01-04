from datetime import date
from django.utils import timezone
from django.db import transaction

from .models import AccountingMonthClose


def auto_close_past_months(side: str = "ALL") -> int:
    """
    Creates closed records for all past months up to last month.
    Returns how many records were created.
    """
    today = timezone.localdate()
    year = today.year
    month = today.month

    # last month
    if month == 1:
        end_year = year - 1
        end_month = 12
    else:
        end_year = year
        end_month = month - 1

    created_count = 0

    with transaction.atomic():
        y = 2000  # change this start year if you want
        m = 1

        while (y < end_year) or (y == end_year and m <= end_month):
            obj, created = AccountingMonthClose.objects.get_or_create(
                year=y,
                month=m,
                side=side,
                defaults={"is_closed": True, "note": "Auto closed"},
            )
            if created:
                created_count += 1

            m += 1
            if m == 13:
                m = 1
                y += 1

    return created_count