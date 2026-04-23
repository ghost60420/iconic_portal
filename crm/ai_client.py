import os
try:
    from openai import OpenAI
except Exception:
    OpenAI = None


def get_openai_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is missing. Add it to your .env file.")
    if OpenAI is None:
        raise ValueError("OpenAI library is not installed.")
    return OpenAI(api_key=api_key)
