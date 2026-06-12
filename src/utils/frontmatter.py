"""
Unified frontmatter utilities for YAML frontmatter parsing.

Consolidates regex and string-split logic previously duplicated across:
- src/utils/vault_cache.py (_read_note_quality_from_content, _strip_front_matter)
- src/services/refiner_service.py (quality_score regex in read_note)
- src/services/drafts_service.py (inline --- splitting in write_draft / publish_draft)
- src/services/feedback_service.py (parse_front_matter)
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("vidbrain.frontmatter")

_QUALITY_PATTERN = re.compile(r"^quality_score:\s*(\d+)", re.MULTILINE)
_KEY_VALUE_PATTERN = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*)")


def read_quality_score(content: str) -> int:
    """Extract ``quality_score`` from YAML frontmatter.

    Args:
        content: Full markdown content (may or may not include frontmatter).

    Returns:
        Integer quality score (0-10), or 0 if the field is missing or unparseable.
    """
    try:
        match = _QUALITY_PATTERN.search(content)
        if match:
            return int(match.group(1))
    except Exception:
        pass
    return 0


def strip_frontmatter(content: str, max_body_len: int = 500) -> str:
    """Remove YAML frontmatter delimiters and return a condensed body preview.

    Args:
        content: Full markdown content (may or may not include frontmatter).
        max_body_len: Maximum number of characters of the body to return.

    Returns:
        Body text, stripped of leading/trailing whitespace and truncated to
        ``max_body_len`` characters.  If there is no frontmatter, the entire
        content is returned (after stripping and truncation).
    """
    if content.startswith("---"):
        parts = content.split("---", 2)
        body = parts[2] if len(parts) >= 3 else content
    else:
        body = content
    return body.strip()[:max_body_len]


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content.

    Handles both ``---``-delimited frontmatter blocks and content without
    any frontmatter.  Boolean values (``true`` / ``false``) are coerced to
    Python ``bool``.

    Args:
        content: Full markdown content.

    Returns:
        A ``(metadata_dict, body_text)`` tuple.  ``metadata_dict`` is empty if
        no valid frontmatter was found.  ``body_text`` preserves the original
        whitespace after the closing ``---`` delimiter.
    """
    result: dict[str, Any] = {}
    body = content

    if content.startswith("---"):
        second_delim = content.find("---", 3)
        if second_delim != -1:
            fm_block = content[3:second_delim]
            body = content[second_delim + 3 :]

            for line in fm_block.splitlines():
                line = line.strip()
                if not line:
                    continue
                m = _KEY_VALUE_PATTERN.match(line)
                if m:
                    key = m.group(1)
                    value = m.group(2).strip()
                    if value.lower() == "true":
                        result[key] = True
                    elif value.lower() == "false":
                        result[key] = False
                    else:
                        result[key] = value

    return result, body
