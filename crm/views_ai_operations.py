from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from crm.services.ai_operations_assistant import build_ai_operations_context


@login_required
def ai_operations_assistant(request):
    question = (request.GET.get("question") or "").strip()[:500]
    context = build_ai_operations_context(request.user, question=question)
    if not context["access_flags"].get("can_view_page"):
        return render(
            request,
            "crm/access_denied.html",
            {"required_permission": "AI Operations Assistant"},
            status=403,
        )
    return render(request, "crm/ai_operations_assistant.html", context)
