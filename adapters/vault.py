"""
ROOT Obsidian vault adapter.

Reads markdown notes from the vault, extracts metadata,
and yields them for indexing.
"""

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Iterator


def _extract_title(content: str, filename: str) -> str:
    """Extract title from frontmatter, first heading, or filename."""
    # Try YAML frontmatter
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).split("\n"):
            if line.startswith("title:"):
                return line.split(":", 1)[1].strip().strip("\"'")

    # Try first heading
    heading_match = re.match(r"^#\s+(.+)", content.lstrip(), re.MULTILINE)
    if heading_match:
        return heading_match.group(1).strip()

    # Fall back to filename
    return filename.replace(".md", "").replace("-", " ").replace("_", " ")


def _extract_date(content: str, file_path: Path) -> str | None:
    """Extract date from frontmatter or filename."""
    # Try frontmatter
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).split("\n"):
            if line.startswith("date:") or line.startswith("created:"):
                date_str = line.split(":", 1)[1].strip().strip("\"'")
                return date_str

    # Try date in filename (YYYY-MM-DD)
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", file_path.name)
    if date_match:
        return date_match.group(1)

    return None


def _content_hash(content: str) -> str:
    """SHA-256 hash of content for change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _get_folder(path: Path, vault_root: Path) -> str:
    """Get the top-level folder relative to vault root."""
    try:
        relative = path.relative_to(vault_root)
        parts = relative.parts
        return parts[0] if len(parts) > 1 else "(root)"
    except ValueError:
        return "(unknown)"


def scan_vault(
    vault_path: str | Path,
    exclude_folders: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> Iterator[dict]:
    """Walk the vault and yield note dicts for indexing.

    Yields: {"path": str, "title": str, "content": str,
             "content_hash": str, "folder": str, "created_at": str|None}
    """
    vault_root = Path(vault_path)
    exclude_folders = set(exclude_folders or [])
    exclude_patterns = exclude_patterns or []

    if not vault_root.exists():
        raise FileNotFoundError(f"Vault not found: {vault_root}")

    for md_file in sorted(vault_root.rglob("*.md")):
        # Skip excluded folders
        relative_parts = md_file.relative_to(vault_root).parts
        if any(part in exclude_folders for part in relative_parts):
            continue

        # Skip excluded patterns
        if any(md_file.match(pat) for pat in exclude_patterns):
            continue

        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            continue

        if not content.strip():
            continue

        title = _extract_title(content, md_file.name)
        folder = _get_folder(md_file, vault_root)

        yield {
            "path": str(md_file.relative_to(vault_root)),
            "title": title,
            "content": content,
            "content_hash": _content_hash(content),
            "folder": folder,
            "created_at": _extract_date(content, md_file),
        }
