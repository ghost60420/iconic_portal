"""Compatibility imports for the canonical sales attribution service.

New dashboard code must import these builders from ``sales_attribution``.
This module intentionally contains no queries or KPI calculations.
"""

from crm.services.sales_attribution import (
    build_employee_sales_statistics,
    build_sales_kpis as build_salesperson_profile,
    build_team_sales_kpis as build_team_performance,
)


__all__ = (
    "build_employee_sales_statistics",
    "build_salesperson_profile",
    "build_team_performance",
)
