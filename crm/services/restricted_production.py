"""Opt-in restrictions for the approved external production account.

Uses an existing Django group and ProductionOrder assignment; no parallel role
or production database. Restrictions take precedence over additive role grants.
"""

RESTRICTED_PRODUCTION_GROUP = "Assigned Production Only"
RESTRICTED_PRODUCTION_USER_ID = 16


def is_restricted_production_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    # The approved scope is this existing account only. Avoid changing any
    # other employee's behavior or adding a group query to their requests.
    if user.pk != RESTRICTED_PRODUCTION_USER_ID:
        return False
    from crm.services.operations_permissions import operations_group_names

    return RESTRICTED_PRODUCTION_GROUP.casefold() in operations_group_names(user)


def restricted_request_allowed(request):
    """Default deny, including generic search, chatter, exports and mutations."""
    route = request.resolver_match.view_name
    methods = {
        "production_list": {"GET", "HEAD"},
        "production_detail": {"GET", "HEAD", "POST"},
        "production_stage_edit": {"GET", "POST"},
        "production_stage_click": {"POST"},
        "protected_media": {"GET", "HEAD"},
        "login": {"GET", "POST"},
        "logout": {"POST"},
        "password_change": {"GET", "POST"},
        "password_change_done": {"GET"},
    }
    if request.method not in methods.get(route, set()):
        return False
    if route == "production_detail" and request.method == "POST":
        return request.POST.get("action") in {"add_comment", "add_progress_photo"}
    return True
