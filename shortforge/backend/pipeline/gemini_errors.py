"""Turn Gemini SDK exceptions into something a user can act on.

The SDK surfaces raw JSON envelopes ("Lightning dunning decision is deny for
project: projects/942233955595"), which say nothing about what to actually do.
Every call site pastes the exception into a job log, so the translation happens
here once.
"""

from __future__ import annotations

CONSOLE_BILLING = "https://console.cloud.google.com/billing"
AI_STUDIO_KEYS = "https://aistudio.google.com/apikey"


def explain(exc: Exception | str) -> str:
    """A short, actionable sentence describing a Gemini API failure."""
    raw = str(exc)
    low = raw.lower()

    # "Dunning" is Google's billing-collection state: the project is refused
    # because the Cloud billing account has an unpaid or failed payment. No
    # amount of retrying or key rotation on the same project will clear it.
    if "dunning" in low:
        return (
            "Google refused this project for billing reasons (an unpaid or failed "
            f"payment on the Cloud billing account). Fix the billing account at "
            f"{CONSOLE_BILLING}, or generate a key on a different project at "
            f"{AI_STUDIO_KEYS} and paste it in Settings."
        )
    if "api key not valid" in low or "api_key_invalid" in low:
        return (f"The Gemini API key is not valid. Generate one at {AI_STUDIO_KEYS} "
                f"and paste it in Settings.")
    if "permission_denied" in low or "403" in raw:
        return (f"Google denied this request (403). The key may be restricted, or the "
                f"Generative Language API may be disabled on the project. Check "
                f"{CONSOLE_BILLING} and the key's restrictions at {AI_STUDIO_KEYS}.")
    if "resource_exhausted" in low or "429" in raw or "quota" in low:
        return ("Gemini quota is exhausted for now. It resets on Google's schedule — "
                "retry later, or use a key on a paid project.")
    if "not found" in low and "model" in low:
        return ("That Gemini model name does not exist for this key. Try "
                "'gemini-2.5-flash' in Settings.")
    if "unavailable" in low or "503" in raw or "overloaded" in low:
        return "Gemini is temporarily overloaded. Retrying later usually works."
    if "deadline" in low or "timeout" in low:
        return "The Gemini call timed out. Retry, or use a faster model."
    return raw[:400]
