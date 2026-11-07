"""Live verification of the Groq LLM configuration.

Makes one tiny chat completion (8 tokens) to prove the key, host and model are
usable. Never prints the API key.

Usage:
    cd backend
    python scripts/verify_groq.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.llm import GroqLLM  # noqa: E402


def main() -> int:
    print(f"Groq endpoint : {settings.groq_base_url}")
    print(f"Groq model    : {settings.groq_model}")

    llm = GroqLLM()
    if not llm.available:
        print("\n[FAIL] GROQ_API_KEY is not configured.")
        print("Create a key at https://console.groq.com/keys and put it in .env.")
        return 2

    try:
        client = llm._client()
        response = client.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=8,
            temperature=0,
        )
        text = (response.choices[0].message.content or "").strip()
        print(f"\n[OK] Groq responded using {response.model}. Output: {text[:40]!r}")
        return 0
    except Exception as exc:  # noqa: BLE001 - operator-facing preflight
        message = f"{type(exc).__name__}: {str(exc)[:500]}"
        print(f"\n[FAIL] {message}")
        lowered = message.lower()
        if "401" in lowered or "invalid api key" in lowered:
            print("Replace GROQ_API_KEY with a valid key from https://console.groq.com/keys.")
        elif "404" in lowered or "model" in lowered:
            print(
                "The model may not be available on this account. Set "
                "GROQ_MODEL=openai/gpt-oss-120b (or another current Groq model)."
            )
        elif "429" in lowered:
            print("Rate limited. Wait and retry, or check the Groq plan limits.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
