# Costing Field Dictionary

## Header fields
- Opportunity: The linked opportunity.
- Customer: Auto linked from opportunity if available.
- Style name / code: Style identifiers.
- Product type: Product category for reporting.
- Factory location: Production base.
- Order quantity: Total units in the order.
- Currency: Reporting currency for the sheet.
- Exchange rate: Stored rate used for reference.
- Fabric finance %: Percent applied to fabric base cost.
- Trim finance %: Percent applied to trims base cost.
- Commission %: Percent added to FOB for final offer.
- Target margin %: Margin to compute FOB when manual FOB is empty.
- Manual FOB per piece: Overrides target margin when set.

## Line item fields
- Category: Fabric, sewing trims, packaging trims, other.
- Item name: Required item label.
- UOM: Unit price basis (piece, kg, meter, yard, roll, cone, pack, order).
- Unit price: Price per UOM in BDT.
- Freight: Freight per UOM in BDT.
- Consumption: Quantity used per piece.
- Wastage %: Percentage added to consumption.
- Denominator: Used for rolls/cones/packs to convert to per piece.
- Placement/color/GSM/cut width: Optional descriptive fields.
- Remarks: Notes for the item.

## SMV fields
- Machine SMV: Sewing time per piece.
- Finishing SMV: Finishing time per piece.
- CPM: Cost per minute.
- Efficiency (costing): Used to adjust labor cost in costing.
- Efficiency (planned): Planning reference only.

## Outputs
- Total cost per piece
- FOB per piece
- Profit and margin
- Order totals
