def user_is_bd_only(user):
    if not user or not user.is_authenticated:
        return False

    # Superuser can see everything
    if user.is_superuser:
        return False

    # If user is in CA group, not BD only
    if user.groups.filter(name__in=["CA", "Canada"]).exists():
        return False

    # BD only user
    return user.groups.filter(name__in=["BD", "Bangladesh"]).exists()


def guess_add_side(request):
    side = (request.GET.get("side") or "").upper().strip()
    if side in ("BD", "CA"):
        return side

    ref = (request.META.get("HTTP_REFERER") or "").lower()
    if "/accounting/bd" in ref:
        return "BD"
    if "/accounting/ca" in ref:
        return "CA"

    return "CA"