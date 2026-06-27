from decimal import Decimal


OPERATIONAL_STATUS_PLANNING = "planning"
OPERATIONAL_STATUS_PATTERN = "pattern"
OPERATIONAL_STATUS_SAMPLE_DEVELOPMENT = "sample_development"
OPERATIONAL_STATUS_SAMPLE_SENT = "sample_sent"
OPERATIONAL_STATUS_APPROVED = "approved"
OPERATIONAL_STATUS_FABRIC_SOURCING = "fabric_sourcing"
OPERATIONAL_STATUS_CUTTING = "cutting"
OPERATIONAL_STATUS_PRINTING = "printing"
OPERATIONAL_STATUS_SEWING = "sewing"
OPERATIONAL_STATUS_FINISHING = "finishing"
OPERATIONAL_STATUS_QC = "qc"
OPERATIONAL_STATUS_PACKING = "packing"
OPERATIONAL_STATUS_READY_TO_SHIP = "ready_to_ship"
OPERATIONAL_STATUS_SHIPPED = "shipped"
OPERATIONAL_STATUS_ON_HOLD = "on_hold"
OPERATIONAL_STATUS_CANCELLED = "cancelled"

OPERATIONAL_ACTIVE_STATUSES = {
    OPERATIONAL_STATUS_PLANNING,
    OPERATIONAL_STATUS_PATTERN,
    OPERATIONAL_STATUS_SAMPLE_DEVELOPMENT,
    OPERATIONAL_STATUS_SAMPLE_SENT,
    OPERATIONAL_STATUS_APPROVED,
    OPERATIONAL_STATUS_FABRIC_SOURCING,
    OPERATIONAL_STATUS_CUTTING,
    OPERATIONAL_STATUS_PRINTING,
    OPERATIONAL_STATUS_SEWING,
    OPERATIONAL_STATUS_FINISHING,
    OPERATIONAL_STATUS_QC,
    OPERATIONAL_STATUS_PACKING,
    OPERATIONAL_STATUS_READY_TO_SHIP,
    OPERATIONAL_STATUS_ON_HOLD,
}

OPERATIONAL_FINISHED_STATUSES = {
    OPERATIONAL_STATUS_SHIPPED,
    OPERATIONAL_STATUS_CANCELLED,
}

OPERATIONAL_STATUS_LABELS = {
    OPERATIONAL_STATUS_PLANNING: "Not Started",
    OPERATIONAL_STATUS_PATTERN: "Pattern",
    OPERATIONAL_STATUS_SAMPLE_DEVELOPMENT: "Sample",
    OPERATIONAL_STATUS_SAMPLE_SENT: "Sample Sent",
    OPERATIONAL_STATUS_APPROVED: "Approved",
    OPERATIONAL_STATUS_FABRIC_SOURCING: "Fabric Sourcing",
    OPERATIONAL_STATUS_CUTTING: "Cutting",
    OPERATIONAL_STATUS_PRINTING: "Print / Embroidery",
    OPERATIONAL_STATUS_SEWING: "Sewing",
    OPERATIONAL_STATUS_FINISHING: "Finishing",
    OPERATIONAL_STATUS_QC: "Quality Check",
    OPERATIONAL_STATUS_PACKING: "Packing",
    OPERATIONAL_STATUS_READY_TO_SHIP: "Ready To Ship",
    OPERATIONAL_STATUS_SHIPPED: "Shipped",
    OPERATIONAL_STATUS_ON_HOLD: "On Hold",
    OPERATIONAL_STATUS_CANCELLED: "Cancelled",
}

OPERATIONAL_STATUS_VALUES = set(OPERATIONAL_STATUS_LABELS.keys())

# A real shipment booking means the order has left production control for
# reporting purposes. Sample shipments are handled first so they remain in the
# sample approval workflow instead of becoming a completed bulk shipment.
SHIPMENT_SENT_STATUSES = {"booked", "shipped", "out_for_delivery", "delivered"}
SHIPMENT_READY_STATUSES = {"planned"}
STAGE_ACTIVE_STATUSES = {"in_progress", "hold", "delay"}


def _decimal(value):
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _related_list(order, related_name):
    try:
        return list(getattr(order, related_name).all())
    except Exception:
        return []


def _stage_lookup(stages):
    return {stage.stage_key: stage for stage in stages}


def _stage_done(stage_lookup, *keys):
    return any(stage_lookup.get(key) and stage_lookup[key].status == "done" for key in keys)


def _stage_active(stage_lookup, *keys):
    return any(
        stage_lookup.get(key) and stage_lookup[key].status in STAGE_ACTIVE_STATUSES
        for key in keys
    )


def _needs_print(order):
    text = " ".join(
        [
            getattr(order, "title", "") or "",
            getattr(order, "style_name", "") or "",
            getattr(order, "notes", "") or "",
            getattr(order, "accessories_note", "") or "",
            getattr(order, "extra_order_note", "") or "",
        ]
    ).lower()
    return any(token in text for token in ["print", "embroidery", "screen", "sublimation", "puff"])


def _sample_shipment_sent(shipments):
    return any(
        getattr(shipment, "shipment_type", "") == "sample"
        and getattr(shipment, "status", "") in SHIPMENT_SENT_STATUSES
        for shipment in shipments
    )


def derive_production_operational_status(order):
    """
    Read-only operational status derived from existing stages and shipments.

    Audit mapping notes:
    - Shipment shipped/out_for_delivery/delivered is the only final shipped signal.
    - Legacy ProductionOrder.status is used only for cancelled compatibility.
    - Completed packing/finishing means production is ready to ship, not shipped.
    - Sample shipments on sampling orders map to sample_sent, not final shipped.
    - ProductionStage has no fabric_sourcing or printing stage today; those are
      inferred from existing order fabric fields and print-related order notes.
    - No fields are written here; this is dashboard/reporting logic only.
    """
    stages = _related_list(order, "stages")
    shipments = _related_list(order, "shipments")
    stage_lookup = _stage_lookup(stages)
    order_type = getattr(order, "production_order_type", "") or "bulk"
    legacy_status = getattr(order, "status", "") or ""

    if legacy_status in {"closed_lost", "cancelled"}:
        return OPERATIONAL_STATUS_CANCELLED

    if shipments and all(shipment.status == "cancelled" for shipment in shipments):
        return OPERATIONAL_STATUS_CANCELLED

    if order_type == "sampling" and _sample_shipment_sent(shipments):
        return OPERATIONAL_STATUS_SAMPLE_SENT

    if any(shipment.status in SHIPMENT_SENT_STATUSES for shipment in shipments):
        return OPERATIONAL_STATUS_SHIPPED

    if any(shipment.status in SHIPMENT_READY_STATUSES for shipment in shipments):
        return OPERATIONAL_STATUS_READY_TO_SHIP

    if _stage_done(stage_lookup, "shipping", "packing", "finishing"):
        return OPERATIONAL_STATUS_READY_TO_SHIP

    if _stage_active(stage_lookup, "shipping"):
        return OPERATIONAL_STATUS_READY_TO_SHIP

    if _stage_active(stage_lookup, "packing", "finishing", "ironing"):
        return OPERATIONAL_STATUS_PACKING

    if _stage_active(stage_lookup, "qc"):
        return OPERATIONAL_STATUS_QC

    if _stage_active(stage_lookup, "sewing"):
        return OPERATIONAL_STATUS_SEWING

    if _needs_print(order) and _stage_done(stage_lookup, "cutting") and not _stage_done(stage_lookup, "sewing"):
        return OPERATIONAL_STATUS_PRINTING

    if _stage_active(stage_lookup, "cutting"):
        return OPERATIONAL_STATUS_CUTTING

    if _stage_done(stage_lookup, "qc"):
        return OPERATIONAL_STATUS_PACKING

    if _stage_done(stage_lookup, "sewing"):
        return OPERATIONAL_STATUS_QC

    if _stage_done(stage_lookup, "cutting"):
        return OPERATIONAL_STATUS_SEWING

    required_kg = _decimal(getattr(order, "fabric_required_kg", None))
    received_kg = _decimal(getattr(order, "fabric_received_kg", None))
    if required_kg and received_kg < required_kg:
        return OPERATIONAL_STATUS_FABRIC_SOURCING

    if _stage_active(stage_lookup, "development", "sampling"):
        return OPERATIONAL_STATUS_SAMPLE_DEVELOPMENT

    if _stage_done(stage_lookup, "development", "sampling"):
        return OPERATIONAL_STATUS_APPROVED

    if order_type == "sampling":
        return OPERATIONAL_STATUS_SAMPLE_DEVELOPMENT

    return OPERATIONAL_STATUS_PLANNING


def get_production_operational_status(order):
    """
    Return the stored workflow status when available, with derived fallback.

    The fallback keeps older environments and unsaved/incomplete objects safe.
    It does not write to ProductionOrder.status or shipment/stage records.
    """
    stored_status = getattr(order, "operational_status", None)
    if stored_status in OPERATIONAL_STATUS_VALUES:
        return stored_status
    return derive_production_operational_status(order)


def sync_operational_status(order, explicit_status=None):
    """
    Persist the production workflow status from one central place.

    Use explicit_status for user-recorded workflow events such as sample sent,
    sample approved, or cancelled. Otherwise the status is derived from saved
    ProductionStage and Shipment activity. This function intentionally does not
    write to legacy ProductionOrder.status, stages, or shipments.
    """
    if order is None:
        return None

    target_status = explicit_status if explicit_status in OPERATIONAL_STATUS_VALUES else derive_production_operational_status(order)
    current_status = getattr(order, "operational_status", None)

    if current_status != target_status:
        order.operational_status = target_status
        if getattr(order, "pk", None) and hasattr(order, "save"):
            update_fields = ["operational_status"]
            if hasattr(order, "updated_at"):
                update_fields.append("updated_at")
            order.save(update_fields=update_fields)

    return target_status
