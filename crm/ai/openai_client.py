# crm/ai/openai_client.py

import json
import time
from django.conf import settings
from openai import OpenAI

from crm.utils.activity_log import log_activity

_client = None
_client_key = None


def _get_api_key():
    return (getattr(settings, "OPENAI_API_KEY", "") or "").strip()


def _get_model_name():
    return (getattr(settings, "OPENAI_MODEL", "") or "gpt-4.1-mini").strip()


def get_client():
    """
    Returns a cached OpenAI client.
    If the API key changes, rebuild the client.
    """
    global _client, _client_key

    api_key = _get_api_key()
    if not api_key:
        return None

    if _client is None or _client_key != api_key:
        _client = OpenAI(api_key=api_key)
        _client_key = api_key

    return _client


def _safe_json(meta):
    if not meta:
        return ""
    try:
        return json.dumps(meta, default=str)[:5000]
    except Exception:
        return ""


def _extract_text(resp):
    """
    Tries common response shapes and returns text.
    """
    if resp is None:
        return ""

    text = (getattr(resp, "output_text", "") or "").strip()
    if text:
        return text

    # Fallback if output_text is not present
    try:
        output = getattr(resp, "output", None)
        if not output:
            return ""
        parts = []
        for item in output:
            content = getattr(item, "content", None)
            if not content:
                continue
            for c in content:
                t = getattr(c, "text", None)
                if t:
                    parts.append(t)
        return "\n".join(parts).strip()
    except Exception:
        return ""


def ask_openai(*, user=None, prompt_text="", meta=None, feature="openai_answer"):
    """
    Returns plain text answer.
    Logs success and error into AISystemLog using crm.utils.activity_log.log_activity
    """
    prompt_text = (prompt_text or "").strip()
    if not prompt_text:
        raise ValueError("Prompt is empty")

    api_key = _get_api_key()
    if not api_key:
        log_activity(
            user=user,
            feature=feature,
            provider="openai",
            model_name=_get_model_name(),
            level="error",
            message="OPENAI_API_KEY is missing",
        )
        raise ValueError("OPENAI_API_KEY is missing in settings")

    model = _get_model_name()
    start = time.time()

    client = get_client()
    if client is None:
        log_activity(
            user=user,
            feature=feature,
            provider="openai",
            model_name=model,
            level="error",
            message="OpenAI client could not be created",
        )
        raise ValueError("OpenAI client could not be created")

    meta_text = _safe_json(meta)

    try:
        resp = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are Iconic CRM assistant. "
                        "Be short, clear, and helpful. "
                        "Use bullet points when needed. "
                        "Do not invent numbers. If data is missing, say so."
                    ),
                },
                {"role": "user", "content": prompt_text},
            ],
        )

        text = _extract_text(resp)
        latency_ms = int((time.time() - start) * 1000)

        log_activity(
            user=user,
            feature=feature,
            provider="openai",
            model_name=model,
            level="info",
            message="OpenAI answer success",
            error_detail=meta_text,
            latency_ms=latency_ms,
        )

        return text or "No answer was returned."

    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)

        log_activity(
            user=user,
            feature=feature,
            provider="openai",
            model_name=model,
            level="error",
            message="OpenAI answer failed",
            error_type=e.__class__.__name__,
            error_detail=(str(e)[:2000] + ("\nMETA: " + meta_text if meta_text else ""))[:5000],
            latency_ms=latency_ms,
        )
        raise