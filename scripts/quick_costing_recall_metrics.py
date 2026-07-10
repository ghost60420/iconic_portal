import json

from django.db import connection


QUICK_COSTING_TOTALS_SQL = """
WITH base AS (
    SELECT COALESCE(currency, '') AS currency,
           COALESCE(quantity, 0) AS quantity,
           COALESCE(selling_price_per_piece, 0) AS selling_price,
           COALESCE(shipping_cost, 0) AS shipping_cost,
           COALESCE(other_expenses, 0) AS other_expenses,
           COALESCE(material_cost, 0) AS material_cost,
           COALESCE(production_cost, 0) AS production_cost,
           COALESCE(pricing_type, '') AS pricing_type,
           COALESCE(sewing_charge_per_piece_bdt, 0) AS sewing_charge,
           COALESCE(sewing_cost_per_piece_bdt, 0) AS sewing_cost,
           COALESCE(extra_local_cost_bdt, 0) AS extra_local_cost,
           COALESCE(commission_type, '') AS commission_type,
           COALESCE(commission_value, 0) AS commission_value,
           COALESCE(commission_percent, -1) AS commission_percent,
           COALESCE(commission_per_piece, 0) AS commission_per_piece,
           CASE WHEN COALESCE(pricing_type, '') IN ('cmt', 'cmt_sewing') AND COALESCE(currency, '') = 'BDT'
                THEN COALESCE(sewing_charge_per_piece_bdt, 0) * COALESCE(quantity, 0)
                ELSE COALESCE(selling_price_per_piece, 0) * COALESCE(quantity, 0)
           END AS revenue,
           CASE WHEN COALESCE(pricing_type, '') IN ('cmt', 'cmt_sewing') AND COALESCE(currency, '') = 'BDT'
                THEN (COALESCE(sewing_charge_per_piece_bdt, 0) * COALESCE(quantity, 0))
                     - COALESCE(extra_local_cost_bdt, 0)
                     - (COALESCE(sewing_cost_per_piece_bdt, 0) * COALESCE(quantity, 0))
                ELSE (COALESCE(selling_price_per_piece, 0) * COALESCE(quantity, 0))
                     - COALESCE(shipping_cost, 0)
                     - COALESCE(other_expenses, 0)
                     - COALESCE(material_cost, 0)
                     - COALESCE(production_cost, 0)
           END AS gross_profit
    FROM crm_quickcosting
), totals AS (
    SELECT currency,
           revenue,
           gross_profit,
           CASE WHEN commission_type = 'fixed' AND commission_value > 0 THEN commission_value
                WHEN commission_type = 'percentage' AND commission_value > 0 AND gross_profit > 0 THEN ROUND(gross_profit * commission_value / 100.0, 2)
                WHEN commission_type IN ('', 'none') AND commission_percent >= 0 THEN ROUND(revenue * commission_percent / 100.0, 2)
                WHEN commission_type IN ('', 'none') THEN commission_per_piece * quantity
                ELSE 0
           END AS commission
    FROM base
)
SELECT currency,
       SUM(revenue) AS revenue,
       SUM(gross_profit) AS gross_profit,
       SUM(commission) AS commission,
       SUM(gross_profit - commission) AS net_profit
FROM totals
GROUP BY currency
ORDER BY currency
"""


def rows(sql):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, [str(value) for value in row])) for row in cursor.fetchall()]


snapshot = {
    "counts": {
        "quick_costings": rows("SELECT COUNT(*) AS count FROM crm_quickcosting")[0]["count"],
        "invoices": rows("SELECT COUNT(*) AS count FROM crm_invoice")[0]["count"],
        "production_orders": rows("SELECT COUNT(*) AS count FROM crm_productionorder")[0]["count"],
        "shipments": rows("SELECT COUNT(*) AS count FROM crm_shipment")[0]["count"],
        "payments": rows("SELECT COUNT(*) AS count FROM crm_invoicepayment")[0]["count"],
        "accounting_entries": rows("SELECT COUNT(*) AS count FROM crm_accountingentry")[0]["count"],
    },
    "invoice_totals": rows(
        "SELECT currency, SUM(subtotal) AS subtotal, SUM(shipping_amount) AS shipping_amount, "
        "SUM(tax_amount) AS tax_amount, SUM(total_amount) AS total_amount, "
        "SUM(paid_amount) AS paid_amount FROM crm_invoice GROUP BY currency ORDER BY currency"
    ),
    "payment_totals": rows(
        "SELECT currency, SUM(amount) AS amount, SUM(amount_bdt) AS amount_bdt, "
        "SUM(amount_cad) AS amount_cad FROM crm_invoicepayment GROUP BY currency ORDER BY currency"
    ),
    "accounting_totals": rows(
        "SELECT currency, SUM(amount_original) AS amount_original, SUM(amount_bdt) AS amount_bdt, "
        "SUM(amount_cad) AS amount_cad FROM crm_accountingentry GROUP BY currency ORDER BY currency"
    ),
    "quick_costing_totals": rows(QUICK_COSTING_TOTALS_SQL),
}
print(json.dumps(snapshot, sort_keys=True))
