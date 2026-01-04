import os
from openai import OpenAI


def get_openai_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is missing. Add it to your .env file.")
    return OpenAI(api_key=api_key)