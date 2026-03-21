"""
ROOT search tools.

Semantic search across all indexed content.
Recency-boosted: recent notes rank higher when semantic similarity is close.
"""

from datetime import datetime, timezone

from db import RootDB
from embeddings import Embedder

# Recency decay: how much to penalize older notes.
# 0.002 per day means a 30-day-old note gets ~6% penalty, 180-day-old ~36%.
# Only matters when semantic distances are close.
_RECENCY_DECAY_PER_DAY = 0.002


def _days_old(created_at: str | None) -> float:
    """Calculate days since note creation.

    Returns 0 for missing dates (no penalty for undated reference content).
    This means undated notes compete purely on semantic relevance.
    """
    if not created_at:
        return 0.0  # No penalty for undated content (READMEs, reference docs)
    try:
        date_str = created_at.replace("Z", "+00:00")
        # Handle date-only strings (YYYY-MM-DD) by adding time
        if "T" not in date_str and len(date_str) == 10:
            date_str += "T00:00:00+00:00"
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0.0, delta.total_seconds() / 86400)
    except (ValueError, TypeError):
        return 0.0


def semantic_search(
    query: str,
    db: RootDB,
    embedder: Embedder,
    limit: int = 10,
    source_type: str | None = None,
    recency_boost: bool = True,
) -> list[dict]:
    """Search for notes semantically similar to the query.

    When recency_boost is True, applies a mild decay to older notes so that
    recent content surfaces first when semantic distances are comparable.

    Returns list of results with title, path, folder, relevance snippet, and distance.
    """
    query_embedding = embedder.embed(query)
    # Fetch extra results for filtering and reranking
    fetch_limit = limit * 4 if (source_type or recency_boost) else limit
    raw_results = db.search(query_embedding, limit=fetch_limit)

    if source_type:
        raw_results = [r for r in raw_results if r["source_type"] == source_type]

    # Deduplicate by note (keep best chunk per note)
    seen_notes: set[int] = set()
    deduped = []
    for r in raw_results:
        if r["note_id"] in seen_notes:
            continue
        seen_notes.add(r["note_id"])

        snippet = r["chunk_text"][:500] + "..." if len(r["chunk_text"]) > 500 else r["chunk_text"]
        days = _days_old(r.get("created_at"))

        # Recency-adjusted score: lower is better (like distance)
        raw_distance = r["distance"]
        adjusted = raw_distance * (1.0 + _RECENCY_DECAY_PER_DAY * days) if recency_boost else raw_distance

        deduped.append({
            "title": r["title"],
            "path": r["path"],
            "folder": r["folder"],
            "source_type": r["source_type"],
            "snippet": snippet,
            "distance": round(raw_distance, 4),
            "adjusted_distance": round(adjusted, 4),
            "days_old": round(days, 1),
        })

    # Sort by adjusted distance when boosting, raw distance otherwise
    sort_key = "adjusted_distance" if recency_boost else "distance"
    deduped.sort(key=lambda r: r[sort_key])

    return deduped[:limit]


def search_by_folder(
    query: str,
    folder: str,
    db: RootDB,
    embedder: Embedder,
    limit: int = 10,
) -> list[dict]:
    """Search within a specific vault folder."""
    all_results = semantic_search(query, db, embedder, limit=limit * 3)
    folder_lower = folder.lower()
    return [
        r for r in all_results
        if r["folder"].lower() == folder_lower
    ][:limit]
