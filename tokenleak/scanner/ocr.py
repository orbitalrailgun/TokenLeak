"""OCR analysis of images via a vision-capable LLM.

Images are sent to a separately configured OCR model (TOKENLEAK_OCR_MODEL)
using the standard OpenAI multimodal message format. The model returns either
a description of sensitive content found, or "CLEAN" if nothing is found.

Supports:
  - Standalone image files (.png, .jpg, .jpeg, .gif, .webp)
  - Images embedded in Jupyter notebook cell outputs (image/png, image/jpeg, etc.)
"""

from __future__ import annotations

import base64
import json
from typing import Optional

from tokenleak.logging_setup import get_logger

log = get_logger()

SUPPORTED_MIME_TYPES: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
})

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
})

_EXT_TO_MIME: dict[str, str] = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
}

_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB — most vision APIs reject larger payloads

_SECURITY_PROMPT = """\
This image is from a git repository. Analyze it for security-sensitive information:
- API keys, tokens, secrets, passwords, private keys, certificates
- PII: names, emails, phone numbers, SSNs, addresses
- Internal URLs, IP addresses, hostnames, database connection strings
- Screenshots of terminals, dashboards, or config panels showing credentials
- Any other corporate-confidential or sensitive data

If you find sensitive information, describe it specifically (type and what is visible).
If the image contains no sensitive information, reply with exactly: CLEAN"""


def mime_for_extension(ext: str) -> Optional[str]:
    return _EXT_TO_MIME.get(ext.lower())


def analyze_image(
    client,
    model: str,
    image_bytes: bytes,
    mime_type: str = "image/png",
    context: str = "",
) -> tuple[Optional[str], int]:
    """Send a single image to a vision model for security analysis.

    Returns (finding_text, tokens_used).
    finding_text is None when the image is clean or the call fails.
    """
    if mime_type not in SUPPORTED_MIME_TYPES:
        log.debug("OCR: unsupported MIME type %s, skipping", mime_type)
        return None, 0
    if not image_bytes:
        return None, 0
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        log.warning("OCR: image too large (%d bytes), skipping %s", len(image_bytes), context)
        return None, 0

    b64 = base64.b64encode(image_bytes).decode()
    prompt = f"Context: {context}\n\n{_SECURITY_PROMPT}" if context else _SECURITY_PROMPT

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
            max_tokens=800,
        )
        tokens = response.usage.total_tokens if response.usage else 0
        result = (response.choices[0].message.content or "").strip()
        log.debug("OCR response for %s: %s (tokens=%d)", context or "image", result[:80], tokens)
        if not result or result.upper() == "CLEAN":
            return None, tokens
        return result, tokens
    except Exception as exc:
        log.warning("OCR analysis failed (model=%s, context=%s): %s", model, context, exc)
        return None, 0


def extract_notebook_images(notebook_json: str) -> list[tuple[int, str, bytes]]:
    """Extract embedded images from a Jupyter notebook JSON string.

    Returns [(cell_index, mime_type, image_bytes), ...] for all image outputs
    across all cells. Skips cells or outputs that cannot be decoded.
    """
    try:
        nb = json.loads(notebook_json)
    except (json.JSONDecodeError, ValueError):
        return []

    images: list[tuple[int, str, bytes]] = []
    for cell_idx, cell in enumerate(nb.get("cells", [])):
        for output in cell.get("outputs", []):
            data = output.get("data", {})
            for mime in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                b64_data = data.get(mime)
                if not b64_data:
                    continue
                if isinstance(b64_data, list):
                    b64_data = "".join(b64_data)
                try:
                    img_bytes = base64.b64decode(b64_data)
                    images.append((cell_idx, mime, img_bytes))
                except Exception:
                    pass
    return images
