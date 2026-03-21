"""
ROOT cross-source correlation tools.

Find information about people, projects, and open loops
across all indexed sources (vault, meetings, email, Slack).
"""

from collections import defaultdict

from db import RootDB
from embeddings import Embedder
from tools.search import semantic_search


def about_person(
    person: str,
    db: RootDB,
    embedder: Embedder,
    limit: int = 5,
) -> str:
    """Everything ROOT knows about a person across all sources.

    Uses entity graph first (precise: notes that actually mention the person),
    then falls back to semantic search if the person isn't in the graph.
    """
    source_labels = {
        "vault": "Obsidian Vault Notes",
        "granola": "Meeting Transcripts",
        "gmail": "Email Threads",
        "slack": "Slack Messages",
    }

    # Step 1: Try entity graph lookup (precise, no false positives)
    entities = db.search_entities(person, entity_type="person")
    # Prefer exact match, then prefix match, then fuzzy
    entity = _best_entity_match(entities, person)

    if entity:
        return _about_from_graph(entity, db, limit, source_labels)

    # Step 2: Fallback to semantic search (for names not yet extracted)
    return _about_from_search(person, db, embedder, limit, source_labels)


def _best_entity_match(entities: list[dict], query: str) -> dict | None:
    """Pick the best matching entity from search results."""
    if not entities:
        return None

    q_lower = query.lower().strip()

    # Exact match (case-insensitive)
    for e in entities:
        if e["name"].lower().strip() == q_lower:
            return e

    # Starts with query (e.g., query="Sebastian" matches "Sebastian Wallmark")
    for e in entities:
        if e["name"].lower().startswith(q_lower):
            return e

    # Highest mention count among remaining matches
    return max(entities, key=lambda e: e["mention_count"])


def _about_from_graph(
    entity: dict,
    db: RootDB,
    limit: int,
    source_labels: dict,
) -> str:
    """Build about profile from entity graph: linked notes + relations."""
    lines = [f"# Everything about: {entity['name']}\n"]
    lines.append(
        f"**Entity type:** {entity['entity_type']}  |  "
        f"**Mentions:** {entity['mention_count']}  |  "
        f"**First seen:** {entity.get('first_seen_at', 'unknown')}  |  "
        f"**Last seen:** {entity.get('last_seen_at', 'unknown')}\n"
    )

    # Get notes that actually mention this entity
    notes = db.get_notes_for_entity(entity["id"])
    lines.append(f"Found in {len(notes)} notes.\n")

    # Group by source type
    by_source = defaultdict(list)
    for n in notes:
        by_source[n["source_type"]].append(n)

    for source_type in ["vault", "granola", "gmail", "slack"]:
        items = by_source.get(source_type, [])
        if not items:
            continue
        label = source_labels.get(source_type, source_type)
        lines.append(f"## {label} ({len(items)} notes)\n")
        for n in items[:limit]:
            lines.append(f"- **{n['title']}** | {n['folder']} | {n.get('created_at', '')}")
        if len(items) > limit:
            lines.append(f"- ... and {len(items) - limit} more")
        lines.append("")

    # Get relations involving this entity
    relations = db.get_entity_relations(entity["id"])
    if relations:
        lines.append(f"## Relations ({len(relations)})\n")
        for rel in relations[:limit * 2]:
            other_name = (
                rel["entity_b_name"]
                if rel["entity_a_name"] == entity["name"]
                else rel["entity_a_name"]
            )
            other_type = (
                rel["entity_b_type"]
                if rel["entity_a_name"] == entity["name"]
                else rel["entity_a_type"]
            )
            direction = (
                f"{entity['name']} --[{rel['relation_type']}]--> {other_name}"
                if rel["entity_a_name"] == entity["name"]
                else f"{other_name} --[{rel['relation_type']}]--> {entity['name']}"
            )
            lines.append(f"- {direction} ({other_type}, {rel['confidence']}%)")
            if rel.get("context"):
                lines.append(f"  > {rel['context'][:120]}")
            lines.append(f"  Source: {rel.get('source_title', 'unknown')}")
        if len(relations) > limit * 2:
            lines.append(f"- ... and {len(relations) - limit * 2} more relations")

    return "\n".join(lines)


def _about_from_search(
    person: str,
    db: RootDB,
    embedder: Embedder,
    limit: int,
    source_labels: dict,
) -> str:
    """Fallback: semantic search when entity isn't in graph yet."""
    queries = [
        f"{person}",
        f"meeting with {person}",
        f"{person} action item decision",
    ]

    all_results = []
    seen_paths = set()

    for query in queries:
        results = semantic_search(query, db, embedder, limit=limit * 2)
        for r in results:
            if r["path"] not in seen_paths:
                seen_paths.add(r["path"])
                all_results.append(r)

    by_source = defaultdict(list)
    for r in all_results:
        by_source[r["source_type"]].append(r)

    lines = [f"# Everything about: {person}\n"]
    lines.append(f"*Note: '{person}' not found in entity graph. Using semantic search (less precise).*\n")
    lines.append(f"Found {len(all_results)} relevant items across {len(by_source)} sources.\n")

    for source_type in ["vault", "granola", "gmail", "slack"]:
        items = by_source.get(source_type, [])
        if not items:
            continue
        label = source_labels.get(source_type, source_type)
        lines.append(f"## {label} ({len(items)} items)\n")
        for r in items[:limit]:
            lines.append(f"### {r['title']}")
            lines.append(f"**Path:** {r['path']}  |  **Folder:** {r['folder']}")
            lines.append(f"\n{r['snippet']}\n")

    if not all_results:
        lines.append(f"No information found about '{person}' in any indexed source.")

    return "\n".join(lines)


def open_loops(
    db: RootDB,
    embedder: Embedder,
    scope: str = "all",
    limit: int = 10,
) -> str:
    """Find open loops: things discussed but never concluded."""
    # Search for action-item-like content
    action_queries = [
        "action item TODO follow up next steps",
        "need to should must will do",
        "promised committed agreed to",
        "pending waiting blocked on",
        "decision needed open question",
    ]

    if scope != "all":
        action_queries = [f"{scope} {q}" for q in action_queries]

    all_results = []
    seen_paths = set()

    for query in action_queries:
        results = semantic_search(query, db, embedder, limit=limit)
        for r in results:
            if r["path"] not in seen_paths:
                seen_paths.add(r["path"])
                all_results.append(r)

    # Sort by distance (most relevant first)
    all_results.sort(key=lambda r: r["distance"])

    lines = [f"# Open Loops{f': {scope}' if scope != 'all' else ''}\n"]
    lines.append(f"Found {len(all_results)} potential open loops.\n")

    # Group by source
    by_source = defaultdict(list)
    for r in all_results:
        by_source[r["source_type"]].append(r)

    for source_type, items in by_source.items():
        lines.append(f"## From {source_type} ({len(items)} items)\n")
        for r in items[:limit]:
            lines.append(f"- **{r['title']}** ({r['folder']})")
            # Extract action-like sentences from snippet
            snippet_lines = r["snippet"].split("\n")
            for sl in snippet_lines:
                sl_lower = sl.lower().strip()
                if any(
                    kw in sl_lower
                    for kw in ["todo", "action", "follow up", "next step", "need to", "should", "will", "must", "pending", "blocked"]
                ):
                    lines.append(f"  > {sl.strip()}")
                    break
            lines.append("")

    if not all_results:
        lines.append("No open loops found. Everything looks resolved!")

    return "\n".join(lines)


def project_pulse(
    project: str,
    db: RootDB,
    embedder: Embedder,
    limit: int = 5,
) -> str:
    """Activity pulse for a project across all sources."""
    results = semantic_search(project, db, embedder, limit=limit * 4)

    by_source = defaultdict(list)
    for r in results:
        by_source[r["source_type"]].append(r)

    lines = [f"# Project Pulse: {project}\n"]
    lines.append(f"Found {len(results)} mentions across {len(by_source)} sources.\n")

    source_labels = {
        "vault": "Notes",
        "granola": "Meetings",
        "gmail": "Emails",
        "slack": "Slack",
    }

    for source_type in ["vault", "granola", "gmail", "slack"]:
        items = by_source.get(source_type, [])
        if not items:
            lines.append(f"### {source_labels.get(source_type, source_type)}: No mentions\n")
            continue

        label = source_labels.get(source_type, source_type)
        lines.append(f"### {label} ({len(items)} mentions)\n")
        for r in items[:limit]:
            lines.append(f"- **{r['title']}** | {r['folder']} | dist: {r['distance']}")
        lines.append("")

    return "\n".join(lines)
