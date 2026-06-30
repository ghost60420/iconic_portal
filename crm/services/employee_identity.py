"""Canonical employee identity and legacy owner resolution.

This cross-cutting service is separate from ``employee_profiles`` because that
module imports permission helpers. Ownership permissions also need identity
resolution, so keeping this dependency-free avoids a circular import while
providing one reusable resolver for lists, dashboards, reports, and search.
"""

from django.core.cache import cache
from django.db.models import Q

from crm.models import EmployeeProfile
from crm.models_employee import EMPLOYEE_IDENTITY_CACHE_KEY


IDENTITY_CACHE_KEY = EMPLOYEE_IDENTITY_CACHE_KEY
IDENTITY_CACHE_SECONDS = 300


def normalize_employee_identity(value):
    return " ".join(str(value or "").split()).casefold()


def _profile_payload(profile):
    user = profile.user
    full_name = " ".join((user.get_full_name() or "").split())
    display_name = " ".join((profile.display_name or "").split())
    canonical_name = full_name or display_name or user.get_username()
    aliases = [" ".join(str(value or "").split()) for value in (profile.aliases or [])]
    aliases = [value for value in aliases if value]
    return {
        "profile_id": profile.pk,
        "user_id": profile.user_id,
        "employee_id": profile.employee_id or "",
        "canonical_name": canonical_name,
        "display_name": display_name,
        "full_name": full_name,
        "username": user.get_username(),
        "aliases": aliases,
    }


def build_employee_identity_index(profiles):
    by_user_id = {}
    by_profile_id = {}
    token_candidates = {}
    owner_values = []
    seen_owner_values = set()

    def register(payload, value, priority):
        token = normalize_employee_identity(value)
        if not token:
            return
        candidate = (priority, payload["user_id"], payload)
        current = token_candidates.get(token)
        if current is None or candidate[:2] < current[:2]:
            token_candidates[token] = candidate
        if token not in seen_owner_values:
            seen_owner_values.add(token)
            owner_values.append(value)

    for profile in profiles:
        payload = _profile_payload(profile)
        by_user_id[payload["user_id"]] = payload
        by_profile_id[payload["profile_id"]] = payload
        register(payload, payload["employee_id"], 1)
        register(payload, payload["display_name"], 2)
        register(payload, payload["full_name"], 3)
        register(payload, payload["username"], 3)
        for alias in payload["aliases"]:
            register(payload, alias, 4)

    return {
        "by_user_id": by_user_id,
        "by_profile_id": by_profile_id,
        "by_token": {token: candidate[2] for token, candidate in token_candidates.items()},
        "owner_values": owner_values,
    }


def get_employee_identity_index(*, force_refresh=False):
    if not force_refresh:
        cached = cache.get(IDENTITY_CACHE_KEY)
        if cached is not None:
            return cached
    profiles = list(
        EmployeeProfile.objects.select_related("user").only(
            "id",
            "user_id",
            "employee_id",
            "display_name",
            "aliases",
            "user__username",
            "user__first_name",
            "user__last_name",
        )
    )
    index = build_employee_identity_index(profiles)
    cache.set(IDENTITY_CACHE_KEY, index, IDENTITY_CACHE_SECONDS)
    return index


def clear_employee_identity_cache():
    cache.delete(IDENTITY_CACHE_KEY)


def resolve_employee_identity(*, user_id=None, profile_id=None, assigned_user=None, owner_text="", index=None):
    index = index or get_employee_identity_index()
    direct_user_id = user_id or getattr(assigned_user, "pk", None)
    if direct_user_id and direct_user_id in index["by_user_id"]:
        return index["by_user_id"][direct_user_id]
    if profile_id and profile_id in index["by_profile_id"]:
        return index["by_profile_id"][profile_id]
    if assigned_user is not None:
        assigned_name = " ".join((assigned_user.get_full_name() or "").split()) or assigned_user.get_username()
        return {
            "profile_id": None,
            "user_id": direct_user_id,
            "employee_id": "",
            "canonical_name": assigned_name or f"User {direct_user_id}",
            "display_name": assigned_name,
            "full_name": assigned_name,
            "username": assigned_user.get_username(),
            "aliases": [],
        }
    if direct_user_id:
        return {
            "profile_id": None,
            "user_id": direct_user_id,
            "employee_id": "",
            "canonical_name": f"User {direct_user_id}",
            "display_name": "",
            "full_name": "",
            "username": "",
            "aliases": [],
        }
    token = normalize_employee_identity(owner_text)
    if token and token in index["by_token"]:
        return index["by_token"][token]
    legacy_name = " ".join(str(owner_text or "").split())
    return {
        "profile_id": None,
        "user_id": None,
        "employee_id": "",
        "canonical_name": legacy_name or "Unassigned",
        "display_name": legacy_name,
        "full_name": "",
        "username": "",
        "aliases": [],
    }


def resolve_lead_owner(lead, *, index=None):
    return resolve_employee_identity(
        user_id=getattr(lead, "assigned_to_id", None),
        assigned_user=getattr(lead, "assigned_to", None),
        owner_text=getattr(lead, "owner", ""),
        index=index,
    )


def employee_owner_values(user, *, index=None):
    index = index or get_employee_identity_index()
    payload = index["by_user_id"].get(getattr(user, "pk", user))
    if not payload:
        return []
    values = [
        payload["employee_id"],
        payload["display_name"],
        payload["full_name"],
        payload["username"],
        *payload["aliases"],
    ]
    return [value for value in dict.fromkeys(values) if value]


def employee_lead_ownership_q(user, prefix="", *, index=None):
    user_id = getattr(user, "pk", user)
    query = Q(**{f"{prefix}assigned_to_id": user_id})
    legacy = Q()
    for value in employee_owner_values(user, index=index):
        legacy |= Q(**{f"{prefix}owner__iexact": value})
    if legacy:
        query |= Q(**{f"{prefix}assigned_to__isnull": True}) & legacy
    return query


def known_employee_owner_q(prefix="", *, index=None):
    index = index or get_employee_identity_index()
    values = index.get("owner_values") or []
    query = Q()
    for value in values:
        query |= Q(**{f"{prefix}owner__iexact": value})
    return query


def employee_profile_ids_matching(query, *, index=None):
    token = normalize_employee_identity(query)
    if not token:
        return []
    index = index or get_employee_identity_index()
    profile_ids = {
        payload["profile_id"]
        for identity_token, payload in index["by_token"].items()
        if token in identity_token
    }
    return sorted(profile_id for profile_id in profile_ids if profile_id)


def alias_conflicts(aliases, *, exclude_profile_id=None):
    index = get_employee_identity_index(force_refresh=True)
    conflicts = []
    for alias in aliases:
        payload = index["by_token"].get(normalize_employee_identity(alias))
        if payload and payload["profile_id"] != exclude_profile_id:
            conflicts.append((alias, payload["canonical_name"]))
    return conflicts
