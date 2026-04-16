from datetime import date, datetime


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _display(obj, field_name):
    method = getattr(obj, f"get_{field_name}_display", None)
    if callable(method):
        try:
            value = _text(method())
            if value:
                return value
        except Exception:
            pass
    return _text(getattr(obj, field_name, ""))


def _first_text(obj, *field_names):
    for field_name in field_names:
        value = _text(getattr(obj, field_name, ""))
        if value:
            return value
    return ""


def _take(items, limit):
    if not items:
        return []
    try:
        return list(items[:limit])
    except Exception:
        pass

    out = []
    try:
        for item in items:
            out.append(item)
            if len(out) >= limit:
                break
    except Exception:
        return []
    return out


def _count(items):
    if not items:
        return 0
    try:
        return len(items)
    except Exception:
        pass
    counter = getattr(items, "count", None)
    if callable(counter):
        try:
            return int(counter())
        except Exception:
            pass
    return len(_take(items, 100))


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = _text(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except Exception:
        return None


def _truncate(value, limit=700):
    text = _text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _activity_label(activity):
    method = getattr(activity, "get_activity_type_display", None)
    if callable(method):
        try:
            value = _text(method())
            if value:
                return value
        except Exception:
            pass
    return _text(getattr(activity, "activity_type", "")) or "Activity"


def build_iconic_ai_brain(
    *,
    lead,
    opportunities=None,
    comments=None,
    tasks=None,
    activities=None,
    insights=None,
    today=None,
):
    """
    Build a deterministic, read-only lead summary.

    This helper intentionally avoids model imports, saves, email sends, queues,
    and external API calls. It only reads values from objects passed by the view.
    """
    today = today or date.today()
    opportunities_count = _count(opportunities)
    comments_count = _count(comments)
    tasks_count = _count(tasks)
    recent_activities = _take(activities, 3)
    latest_insights = _take(insights, 1)

    brand = _first_text(lead, "account_brand", "company_name") or "Unknown brand"
    contact = _first_text(lead, "contact_name", "name") or "Unknown contact"
    email = _text(getattr(lead, "email", ""))
    phone = _text(getattr(lead, "phone", ""))
    website = _first_text(lead, "website", "company_website")
    product = _display(lead, "product_interest") or _display(lead, "product_category")
    quantity = _text(getattr(lead, "order_quantity", ""))
    budget = _text(getattr(lead, "budget", ""))
    lead_status = _display(lead, "lead_status") or "Unknown status"
    priority = _display(lead, "priority") or "Unknown priority"
    lead_type = _display(lead, "lead_type") or _text(getattr(lead, "lead_type", ""))
    fit_score = _text(getattr(lead, "brand_fit_score", ""))
    qualification = _display(lead, "qualification_status")
    qualification_reason = _text(getattr(lead, "qualification_reason", ""))
    disqualification = _text(getattr(lead, "disqualification_reason", ""))
    recommended_next = _text(getattr(lead, "recommended_next_action", ""))
    recommended_channel = _text(getattr(lead, "recommended_channel", ""))
    last_outreach = _as_date(getattr(lead, "last_outreach_date", None))
    last_reply = _as_date(getattr(lead, "last_reply_date", None))
    next_followup = _as_date(
        getattr(lead, "next_follow_up_date", None) or getattr(lead, "next_followup", None)
    )

    lead_summary = [
        f"Brand: {brand}",
        f"Contact: {contact}",
        f"Pipeline: {lead_status} / {priority}",
    ]
    if lead_type:
        lead_summary.append(f"Type: {lead_type}")
    if product:
        lead_summary.append(f"Product: {product}")
    if quantity:
        lead_summary.append(f"Quantity: {quantity}")
    if budget:
        lead_summary.append(f"Budget: {budget}")
    if fit_score:
        summary = f"Fit score: {fit_score}"
        if qualification:
            summary += f" ({qualification})"
        lead_summary.append(summary)
    elif qualification:
        lead_summary.append(f"Qualification: {qualification}")
    if recommended_channel:
        lead_summary.append(f"Recommended channel: {recommended_channel}")

    missing_info = []
    if not email and not phone:
        missing_info.append("Email or phone")
    else:
        if not email:
            missing_info.append("Email")
        if not phone:
            missing_info.append("Phone")
    if not contact or contact == "Unknown contact":
        missing_info.append("Contact name")
    if not website:
        missing_info.append("Website")
    if not product:
        missing_info.append("Product interest")
    if not quantity:
        missing_info.append("Order quantity")
    if not next_followup:
        missing_info.append("Next follow-up date")
    if not missing_info:
        missing_info.append("No major missing info detected.")

    risk_flags = []
    if not email and not phone:
        risk_flags.append("No usable contact method is recorded.")
    if not product:
        risk_flags.append("Product interest is not clear yet.")
    if not quantity:
        risk_flags.append("Order quantity is missing.")
    if disqualification:
        risk_flags.append(f"Disqualification noted: {_truncate(disqualification, 180)}")
    if qualification_reason and qualification and qualification.lower() in {"bad fit", "not qualified"}:
        risk_flags.append(f"Qualification concern: {_truncate(qualification_reason, 180)}")
    if next_followup and next_followup < today:
        risk_flags.append(f"Follow-up is overdue since {next_followup.isoformat()}.")
    if last_outreach and not last_reply:
        risk_flags.append("Outreach is recorded but no reply is recorded yet.")
    try:
        if fit_score and int(fit_score) < 40:
            risk_flags.append("Fit score is below 40.")
    except Exception:
        pass
    if not risk_flags:
        risk_flags.append("No major risk flags detected from current CRM data.")

    if not email and not phone:
        suggested_next_step = "Add a valid email or phone before planning outreach."
    elif disqualification:
        suggested_next_step = "Review the disqualification reason before spending more sales time."
    elif next_followup and next_followup < today:
        suggested_next_step = "Complete the overdue follow-up and log the outcome."
    elif not product:
        suggested_next_step = "Confirm the product category or style the lead is asking about."
    elif not quantity:
        suggested_next_step = "Ask for target order quantity or expected monthly volume."
    elif recommended_next:
        suggested_next_step = recommended_next
    elif next_followup:
        suggested_next_step = f"Prepare for the next follow-up on {next_followup.isoformat()}."
    else:
        suggested_next_step = "Review recent outreach, then set a clear next follow-up date."

    recent_outreach_facts = [
        f"Last outreach: {last_outreach.isoformat() if last_outreach else 'Not recorded'}",
        f"Last reply: {last_reply.isoformat() if last_reply else 'Not recorded'}",
        f"Next follow-up: {next_followup.isoformat() if next_followup else 'Not set'}",
        f"Open tasks: {tasks_count}",
        f"Linked opportunities: {opportunities_count}",
        f"Notes in chatter: {comments_count}",
    ]
    for activity in recent_activities:
        created_at = _as_date(getattr(activity, "created_at", None))
        channel = _text(getattr(activity, "channel", ""))
        outcome = _text(getattr(activity, "outcome", ""))
        fact = _activity_label(activity)
        if channel:
            fact += f" via {channel}"
        if outcome:
            fact += f" ({outcome})"
        if created_at:
            fact += f" on {created_at.isoformat()}"
        recent_outreach_facts.append(fact)

    latest_existing_insight = "No existing AI insight is saved for this lead."
    if latest_insights:
        latest_existing_insight = (
            _truncate(getattr(latest_insights[0], "summary_text", ""), 700)
            or latest_existing_insight
        )

    return {
        "lead_summary": lead_summary,
        "missing_info": missing_info,
        "suggested_next_step": suggested_next_step,
        "risk_flags": risk_flags,
        "recent_outreach_facts": recent_outreach_facts,
        "latest_existing_insight": latest_existing_insight,
    }
