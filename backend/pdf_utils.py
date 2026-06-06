"""Extract text from an uploaded proposal PDF.

Used to feed the researcher's full proposal to the agent as context so it can
auto-extract search terms (the paper's drag-and-drop workflow). Tolerant of
malformed PDFs: returns whatever text it can, or an empty string.
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger("pdf_utils")


def extract_text(data: bytes, max_chars: int = 40000) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        log.error("pypdf not installed")
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception as exc:  # noqa: BLE001
                log.warning("page extract failed: %s", exc)
            if sum(len(p) for p in parts) >= max_chars:
                break
        text = "\n".join(parts).strip()
        return text[:max_chars]
    except Exception as exc:  # noqa: BLE001
        log.warning("pdf parse failed: %s", exc)
        return ""
