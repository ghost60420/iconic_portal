from __future__ import annotations


EDIT_GROUPS = {"Admin", "Marketing Manager"}
CREATE_GROUPS = {"Marketing Staff"}
VIEW_GROUPS = EDIT_GROUPS | CREATE_GROUPS | {"Read only Marketing"}


def intelligence_access(user) -> str:
    """Return edit, view, or deny without changing the existing global role model."""
    if not user or not getattr(user, "is_authenticated", False):
        return "deny"
    if user.is_superuser:
        return "edit"
    group_names = set(user.groups.values_list("name", flat=True))
    if group_names & EDIT_GROUPS:
        return "edit"
    if group_names & CREATE_GROUPS:
        return "create"
    if group_names & VIEW_GROUPS:
        return "view"
    return "deny"
