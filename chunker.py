"""
ROOT text chunking.

Strategy: most Obsidian notes are short enough to embed whole.
Split longer notes at heading boundaries.
"""

import re


MAX_CHUNK_CHARS = 4000  # ~1000 tokens, well within model limits


def chunk_note(content: str, title: str = "") -> list[dict]:
    """Split a note into chunks suitable for embedding.

    Returns list of {"idx": int, "text": str}.
    Short notes (<MAX_CHUNK_CHARS) stay as one chunk.
    Longer notes split at ## headings.
    """
    # Prepend title for context
    full_text = f"# {title}\n\n{content}" if title else content
    full_text = full_text.strip()

    if not full_text:
        return []

    if len(full_text) <= MAX_CHUNK_CHARS:
        return [{"idx": 0, "text": full_text}]

    # Split at headings (## or ###)
    sections = re.split(r"\n(?=#{1,3}\s)", full_text)
    chunks = []
    current_chunk = ""
    idx = 0

    for section in sections:
        if len(current_chunk) + len(section) > MAX_CHUNK_CHARS and current_chunk:
            chunks.append({"idx": idx, "text": current_chunk.strip()})
            idx += 1
            current_chunk = f"# {title}\n\n" if title else ""

        current_chunk += section + "\n"

    if current_chunk.strip():
        chunks.append({"idx": idx, "text": current_chunk.strip()})

    return chunks if chunks else [{"idx": 0, "text": full_text[:MAX_CHUNK_CHARS]}]
