"""
ROOT pattern discovery tools.

Cluster embeddings to find themes, connections, and gaps.
"""

import struct
from collections import defaultdict

from db import RootDB
from embeddings import Embedder


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_connections(
    note_path: str,
    db: RootDB,
    embedder: Embedder,
    limit: int = 10,
) -> list[dict]:
    """Find notes connected to the given note that are in DIFFERENT folders.

    This surfaces unexpected cross-domain connections.
    """
    note = db.get_note_by_path(note_path)
    if not note:
        return []

    query_embedding = embedder.embed(note["content"][:2000])
    raw_results = db.search(query_embedding, limit=limit * 5)

    # Filter: different folder, not the same note
    connections = []
    seen = set()
    for r in raw_results:
        if r["path"] == note_path or r["note_id"] in seen:
            continue
        if r["folder"] == note.get("folder"):
            continue
        seen.add(r["note_id"])

        snippet = r["chunk_text"][:300] + "..." if len(r["chunk_text"]) > 300 else r["chunk_text"]
        connections.append({
            "title": r["title"],
            "path": r["path"],
            "folder": r["folder"],
            "snippet": snippet,
            "distance": round(r["distance"], 4),
            "why": f"Semantically related to '{note['title']}' but in a different domain ({r['folder']})",
        })

        if len(connections) >= limit:
            break

    return connections


def discover_themes(
    db: RootDB,
    embedder: Embedder,
    scope: str = "all",
    num_themes: int = 8,
) -> list[dict]:
    """Discover recurring themes by finding dense clusters of similar notes.

    Uses a simple greedy clustering: pick the note with most neighbors,
    label it as a theme, remove those notes, repeat.

    Args:
        scope: "all", or a folder name to scope to
        num_themes: number of themes to discover
    """
    stats = db.get_stats()
    if stats["total_notes"] == 0:
        return []

    # Get a representative sample of notes for theme discovery
    conn = db.conn
    if scope == "all":
        notes = conn.execute(
            "SELECT id, path, title, folder, content FROM notes ORDER BY RANDOM() LIMIT 500"
        ).fetchall()
    else:
        notes = conn.execute(
            "SELECT id, path, title, folder, content FROM notes WHERE folder = ? ORDER BY RANDOM() LIMIT 500",
            (scope,),
        ).fetchall()

    if len(notes) < 5:
        return [{"theme": "Too few notes to discover themes", "notes": []}]

    # Embed note titles + first 200 chars for fast clustering
    texts = [f"{n['title']}. {n['content'][:200]}" for n in notes]
    embeddings = embedder.embed_batch(texts)

    # Greedy clustering: find dense neighborhoods
    themes = []
    used = set()

    for _ in range(num_themes):
        best_center = -1
        best_neighbors = []

        for i, emb in enumerate(embeddings):
            if i in used:
                continue

            # Find neighbors within cosine similarity > 0.5
            neighbors = []
            for j, other_emb in enumerate(embeddings):
                if j == i or j in used:
                    continue
                sim = _cosine_similarity(emb, other_emb)
                if sim > 0.5:
                    neighbors.append((j, sim))

            if len(neighbors) > len(best_neighbors):
                best_center = i
                best_neighbors = neighbors

        if best_center == -1 or not best_neighbors:
            break

        # Sort neighbors by similarity
        best_neighbors.sort(key=lambda x: x[1], reverse=True)
        theme_notes = [best_center] + [n[0] for n in best_neighbors[:5]]

        # Mark as used
        for idx in theme_notes:
            used.add(idx)

        theme_titles = [notes[idx]["title"] for idx in theme_notes]
        theme_folders = list(set(notes[idx]["folder"] for idx in theme_notes))

        themes.append({
            "theme_label": notes[best_center]["title"],
            "note_count": len(theme_notes),
            "representative_notes": theme_titles[:6],
            "spans_folders": theme_folders,
            "cross_domain": len(theme_folders) > 1,
        })

    return themes


def find_gaps(
    topic: str,
    db: RootDB,
    embedder: Embedder,
    limit: int = 5,
) -> list[dict]:
    """Find potential knowledge gaps around a topic.

    Looks for notes that mention the topic tangentially but don't go deep,
    or clusters near the topic with low note density.
    """
    query_embedding = embedder.embed(topic)
    results = db.search(query_embedding, limit=30)

    if not results:
        return [{"gap": f"No notes found about '{topic}'. This is a complete blind spot."}]

    # Group by folder
    folder_counts = defaultdict(int)
    for r in results:
        folder_counts[r["folder"]] += 1

    # Find folders with few mentions (peripheral awareness)
    all_folders = set(r["folder"] for r in results)
    gaps = []

    # Notes that are semantically distant but still in results (weak connections)
    if len(results) > 5:
        weak_connections = results[10:]  # Further away = weaker connection
        for r in weak_connections[:limit]:
            gaps.append({
                "type": "weak_connection",
                "title": r["title"],
                "folder": r["folder"],
                "distance": round(r["distance"], 4),
                "insight": f"'{r['title']}' is tangentially related to '{topic}' but in {r['folder']}. Worth exploring deeper?",
            })

    # Folders that are surprisingly absent
    stats = db.get_stats()
    major_folders = [f for f, c in stats["top_folders"].items() if c > 20]
    missing_folders = set(major_folders) - all_folders
    for folder in list(missing_folders)[:3]:
        gaps.append({
            "type": "missing_domain",
            "folder": folder,
            "insight": f"'{topic}' has no connections to your {folder} notes. Is that expected?",
        })

    return gaps
