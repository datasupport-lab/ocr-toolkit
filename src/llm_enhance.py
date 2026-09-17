"""
llm_enhance.py — OPTIONAL, BACKEND-CONTROLLED Gemini post-processing.

IMPORTANT
---------
This feature is controlled ONLY by the developer through environment variables.
It is deliberately NOT exposed to the frontend/interface. Users cannot turn it
on or off. If it is disabled or fails for any reason, the pipeline falls back
to the normal Tesseract + OpenCV result with NO change.

Enable it (developer only) by setting BOTH:
    GEMINI_ENABLED=true
    GEMINI_API_KEY=<your key>

Safety rules baked in
---------------------
  * If disabled  -> returns the original text unchanged.
  * If key missing -> returns the original text unchanged.
  * If the API call fails -> returns the original text unchanged (fail-proof).
  * The prompt instructs the model NOT to alter numbers, dates, or identifiers.

Because document content leaves the internal server when this is enabled,
keep it OFF unless your security/compliance team has approved it.
"""

from __future__ import annotations

import logging
import os

LOG = logging.getLogger("ocr.enhance")

GEMINI_ENABLED = os.getenv("GEMINI_ENABLED", "false").strip().lower() == "true"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()
GEMINI_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "30"))


def is_enabled() -> bool:
    """
    Gemini is only active when the developer enabled it AND a key is present.
    """

    return GEMINI_ENABLED and bool(GEMINI_API_KEY)


def enhance_text(raw_text: str, language_hint: str = "eng") -> tuple[str, str]:
    """
    Attempt to correct OCR text with Gemini.

    Returns:
        (text, engine)
        engine is "gemini" if enhancement succeeded, otherwise "tesseract".

    This function NEVER raises. On any problem it returns the original text.
    """

    if not is_enabled():
        return raw_text, "tesseract"

    if not raw_text or not raw_text.strip():
        return raw_text, "tesseract"

    try:
        import google.generativeai as genai

        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)

        prompt = (
            "You are correcting raw OCR output. "
            "Fix obvious character recognition errors only. "
            "Do NOT change numbers, dates, currency amounts, codes, or "
            "identifiers. Do NOT add or remove information. "
            "Preserve the original language "
            f"(hint: {language_hint}) and line structure. "
            "Return ONLY the corrected text.\n\n"
            "----- OCR TEXT -----\n"
            f"{raw_text}"
        )

        response = model.generate_content(
            prompt,
            request_options={"timeout": GEMINI_TIMEOUT},
        )

        corrected = (response.text or "").strip()

        if not corrected:
            return raw_text, "tesseract"

        return corrected, "gemini"

    except Exception:
        LOG.exception("Gemini enhancement failed; falling back to Tesseract.")
        return raw_text, "tesseract"
