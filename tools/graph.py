"""
ROOT graph query tools.

Entity neighborhood traversal, influence maps, decision trails,
and blind spot detection using recursive CTE graph queries on SQLite.
"""

from typing import Optional

from db import RootDB
from embeddings import Embedder
from tools.search import semantic_search


def entity_graph(
    entity_name: str,
    db: RootDB,
    depth: int = 2,
) -> str:
    """Get the neighborhood of an entity in the knowledge graph.

    Resolves the entity by name/alias, then traverses relations
    up to the given depth using recursive CTEs.
    """
    entity_id = db.resolve_entity(entity_name)
    if entity_id is None:
        # Try fuzzy match
        matches = db.search_entities(entity_name)
        if not matches:
            return f"Entity '{entity_name}' not found in the knowledge graph."
        entity_id = matches[0]["id"]
        entity_name = matches[0]["name"]

    neighborhood = db.get_entity_neighborhood(entity_id, depth=depth)
    relations = db.get_entity_relations(entity_id)

    lines = [f"# Entity Graph: {entity_name}\n"]
    lines.append(f"**Depth:** {depth}  |  **Connected entities:** {len(neighborhood)}\n")

    # Group neighbors by depth
    by_depth: dict[int, list[dict]] = {}
    for node in neighborhood:
        d = node["depth"]
        by_depth.setdefault(d, []).append(node)

    for d in sorted(by_depth.keys()):
        label = "Center" if d == 0 else f"Depth {d}"
        lines.append(f"## {label}\n")
        for node in by_depth[d]:
            lines.append(f"- **{node['name']}** ({node['entity_type']}) - {node['mention_count']} mentions")

    # Show direct relations
    if relations:
        lines.append(f"\n## Relations ({len(relations)})\n")
        for rel in relations:
            # Show relation direction
            if rel["entity_a_name"].lower() == entity_name.lower():
                arrow = f"{rel['entity_a_name']} --[{rel['relation_type']}]--> {rel['entity_b_name']}"
            else:
                arrow = f"{rel['entity_a_name']} --[{rel['relation_type']}]--> {rel['entity_b_name']}"
            conf = f"({rel['confidence']:.0%})" if rel["confidence"] else ""
            lines.append(f"- {arrow} {conf}")
            if rel.get("context"):
                lines.append(f"  > {rel['context']}")
            lines.append(f"  Source: {rel['source_title']}")

    return "\n".join(lines)


def influence_map(
    project_name: str,
    db: RootDB,
    embedder: Embedder,
) -> str:
    """Map who influenced a project and through what actions.

    Finds the project entity, pulls all relations, and groups
    by person + relation type to show stakeholder influence.
    """
    entity_id = db.resolve_entity(project_name)
    if entity_id is None:
        matches = db.search_entities(project_name, entity_type="project")
        if not matches:
            # Fall back to semantic search for project-related content
            return _influence_from_search(project_name, db, embedder)
        entity_id = matches[0]["id"]
        project_name = matches[0]["name"]

    relations = db.get_entity_relations(entity_id)
    if not relations:
        return _influence_from_search(project_name, db, embedder)

    # Group by person
    people: dict[str, list[dict]] = {}
    for rel in relations:
        # Identify the "other" entity (not the project)
        if rel["entity_a_name"].lower() == project_name.lower():
            other_name = rel["entity_b_name"]
            other_type = rel["entity_b_type"]
        else:
            other_name = rel["entity_a_name"]
            other_type = rel["entity_a_type"]

        if other_type == "person":
            people.setdefault(other_name, []).append(rel)

    lines = [f"# Influence Map: {project_name}\n"]

    if people:
        lines.append(f"**Stakeholders:** {len(people)} people connected\n")
        # Sort by number of relations (most involved first)
        for person, rels in sorted(people.items(), key=lambda x: -len(x[1])):
            rel_types = sorted({r["relation_type"] for r in rels})
            lines.append(f"## {person} ({len(rels)} interactions)")
            lines.append(f"**Roles:** {', '.join(rel_types)}\n")
            for rel in rels[:5]:  # Cap at 5 per person
                lines.append(f"- [{rel['relation_type']}] {rel.get('context', 'No context')}")
                lines.append(f"  Source: {rel['source_title']}")
            if len(rels) > 5:
                lines.append(f"  ... and {len(rels) - 5} more interactions")
            lines.append("")
    else:
        lines.append("No people directly connected to this project in the graph.")
        lines.append("Try `root_search` for semantic matches.\n")

    # Also show non-person entities connected to the project
    other_entities = []
    for rel in relations:
        if rel["entity_a_name"].lower() == project_name.lower():
            other = {"name": rel["entity_b_name"], "type": rel["entity_b_type"], "rel": rel["relation_type"]}
        else:
            other = {"name": rel["entity_a_name"], "type": rel["entity_a_type"], "rel": rel["relation_type"]}
        if other["type"] != "person":
            other_entities.append(other)

    if other_entities:
        lines.append("## Related Entities\n")
        seen = set()
        for ent in other_entities:
            key = (ent["name"], ent["rel"])
            if key not in seen:
                seen.add(key)
                lines.append(f"- **{ent['name']}** ({ent['type']}) via {ent['rel']}")

    return "\n".join(lines)


def _influence_from_search(project_name: str, db: RootDB, embedder: Embedder) -> str:
    """Fallback: build influence from semantic search when no graph entity exists."""
    results = semantic_search(f"{project_name} team stakeholders", db, embedder, limit=10)
    if not results:
        return f"No information found about '{project_name}' in the knowledge graph."

    lines = [f"# Influence Map: {project_name} (from search)\n"]
    lines.append("*No graph entity found; results from semantic search:*\n")
    for r in results:
        lines.append(f"- **{r['title']}** ({r['folder']})")
        lines.append(f"  {r['snippet'][:200]}\n")
    return "\n".join(lines)


def decision_trail(
    topic: str,
    db: RootDB,
    embedder: Embedder,
) -> str:
    """Trace how decisions evolved around a topic over time.

    Finds decision entities related to the topic, pulls their
    relations and source notes, and orders chronologically.
    """
    # Search for decision-type entities matching the topic
    decision_entities = db.search_entities(topic, entity_type="decision")

    # Also search semantically for decision-related content
    search_results = semantic_search(
        f"{topic} decision decided agreed strategy",
        db, embedder, limit=10,
    )

    lines = [f"# Decision Trail: {topic}\n"]

    if decision_entities:
        lines.append(f"## Explicit Decisions ({len(decision_entities)})\n")
        for ent in decision_entities:
            lines.append(f"### {ent['name']}")
            lines.append(f"First seen: {ent.get('first_seen_at', 'unknown')}  |  "
                         f"Last seen: {ent.get('last_seen_at', 'unknown')}  |  "
                         f"Mentions: {ent['mention_count']}")

            # Get relations for this decision
            rels = db.get_entity_relations(ent["id"])
            if rels:
                for rel in rels[:5]:
                    other = (rel["entity_b_name"] if rel["entity_a_name"] == ent["name"]
                             else rel["entity_a_name"])
                    lines.append(f"- {other} [{rel['relation_type']}]")
                    if rel.get("context"):
                        lines.append(f"  > {rel['context']}")
                    lines.append(f"  Source: {rel['source_title']} ({rel['created_at'][:10]})")

            # Get notes mentioning this decision
            notes = db.get_notes_for_entity(ent["id"])
            if notes:
                lines.append(f"\n**Mentioned in {len(notes)} notes:**")
                for n in notes[:5]:
                    lines.append(f"- {n['title']} ({n.get('created_at', 'undated')}) [{n['source_type']}]")
            lines.append("")

    # Semantic search fallback/supplement
    if search_results:
        lines.append(f"## Related Discussions ({len(search_results)} notes)\n")
        for r in search_results:
            lines.append(f"- **{r['title']}** ({r['folder']}, {r['source_type']})")
            # Show decision-relevant snippet lines
            snippet_lines = r["snippet"].split("\n")
            for sl in snippet_lines[:3]:
                sl_lower = sl.lower()
                if any(kw in sl_lower for kw in [
                    "decid", "agreed", "strategy", "chose", "option",
                    "recommend", "approved", "rejected", "pivot",
                ]):
                    lines.append(f"  > {sl.strip()[:200]}")
                    break
            lines.append("")

    if not decision_entities and not search_results:
        lines.append(f"No decisions found related to '{topic}'.")

    return "\n".join(lines)


def blind_spots(db: RootDB) -> str:
    """Detect entities with declining activity.

    Surfaces entities that were actively discussed (3+ mentions)
    but haven't been seen in the last 30 days.
    """
    declining = db.get_declining_entities(days_inactive=30, min_mentions=3)

    if not declining:
        return "# Blind Spots\n\nNo declining entities found. All active topics are still being discussed."

    lines = ["# Blind Spots\n"]
    lines.append(f"Found {len(declining)} entities that were active but have gone silent.\n")

    # Group by type
    by_type: dict[str, list[dict]] = {}
    for ent in declining:
        by_type.setdefault(ent["entity_type"], []).append(ent)

    for ent_type in ["project", "person", "decision", "concept", "event", "organization"]:
        entities = by_type.get(ent_type, [])
        if not entities:
            continue

        lines.append(f"## {ent_type.title()}s ({len(entities)})\n")
        for ent in entities:
            days_since = _days_since(ent.get("last_seen_at"))
            days_label = f"{days_since}d ago" if days_since is not None else "unknown"
            lines.append(
                f"- **{ent['name']}** - {ent['mention_count']} mentions, "
                f"last seen {days_label}"
            )
        lines.append("")

    return "\n".join(lines)


def _days_since(date_str: Optional[str]) -> Optional[int]:
    """Calculate days since a date string. Returns None if unparseable."""
    if not date_str:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - dt).days
    except (ValueError, TypeError):
        return None
