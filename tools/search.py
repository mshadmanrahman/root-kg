"""
ROOT search tools.

Semantic search across all indexed content.
"""

from db import RootDB
from embeddings import Embedder


def semantic_search(
    query: str,
    db: RootDB,
    embedder: Embedder,
    limit: int = 10,
    source_type: str | None = None,
) -> list[dict]:
    """Search for notes semantically similar to the query.

    Returns list of results with title, path, folder, relevance snippet, and distance.
    """
    query_embedding = embedder.embed(query)
    # Get more results than needed so we can filter
    raw_results = db.search(query_embedding, limit=limit * 3 if source_type else limit)

    if source_type:
        raw_results = [r for r in raw_results if r["source_type"] == source_type]

    # Deduplicate by note (keep best chunk per note)
    seen_notes = set()
    results = []
    for r in raw_results:
        if r["note_id"] in seen_notes:
            continue
        seen_notes.add(r["note_id"])

        # Truncate chunk text for display
        snippet = r["chunk_text"][:500] + "..." if len(r["chunk_text"]) > 500 else r["chunk_text"]

        results.append({
            "title": r["title"],
            "path": r["path"],
            "folder": r["folder"],
            "source_type": r["source_type"],
            "snippet": snippet,
            "distance": round(r["distance"], 4),
        })

        if len(results) >= limit:
            break

    return results


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
