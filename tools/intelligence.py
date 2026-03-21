"""
ROOT intelligence layer.

Free-form Q&A combining semantic search, entity graph, and LLM synthesis.
Weekly digest assembling recent activity across all sources.
"""

from db import RootDB
from embeddings import Embedder
from llm import LLMClient
from tools.search import semantic_search


def ask(
    question: str,
    db: RootDB,
    embedder: Embedder,
    llm: LLMClient,
) -> str:
    """Answer a free-form question by combining three retrieval strategies:

    1. Semantic search (top matching chunks)
    2. Entity graph (if question mentions known entities)
    3. LLM synthesis (feed gathered context to Sonnet)
    """
    # 1. Semantic retrieval
    search_results = semantic_search(question, db, embedder, limit=5)

    # 2. Entity graph context
    graph_context = _gather_graph_context(question, db)

    # 3. Build combined context
    context = _format_context(search_results, graph_context)

    if not context.strip():
        return (
            f"I don't have enough information to answer: '{question}'\n\n"
            "Try ingesting more content with `root_ingest` or re-indexing with `python indexer.py`."
        )

    # 4. LLM synthesis
    answer = llm.synthesize(question, context)
    return f"# ROOT Answer\n\n{answer}\n\n---\n*Based on {len(search_results)} search results and {len(graph_context)} entity matches.*"


def weekly_digest(db: RootDB) -> str:
    """Generate a weekly digest of knowledge graph activity.

    Pure data assembly; no LLM calls needed.
    Surfaces: new/active entities, new relations, activity by source.
    """
    recent_entities = db.get_recent_entities(days=7)
    recent_relations = db.get_recent_relations(days=7)
    entity_stats = db.get_entity_stats()
    index_stats = db.get_stats()

    lines = ["# ROOT Weekly Digest\n"]

    # Overall stats
    lines.append("## Index Health")
    lines.append(f"- **Total notes:** {index_stats['total_notes']}")
    lines.append(f"- **Total chunks:** {index_stats['total_chunks']}")
    lines.append(f"- **Entities:** {entity_stats['total_entities']}")
    lines.append(f"- **Relations:** {entity_stats['total_relations']}")
    lines.append(f"- **Notes extracted:** {entity_stats['notes_extracted']}")
    lines.append(f"- **Last indexed:** {index_stats['last_indexed']}\n")

    # Recent entities
    if recent_entities:
        lines.append(f"## Active Entities This Week ({len(recent_entities)})\n")

        by_type: dict[str, list[dict]] = {}
        for ent in recent_entities:
            by_type.setdefault(ent["entity_type"], []).append(ent)

        for ent_type, entities in sorted(by_type.items()):
            lines.append(f"### {ent_type.title()}s ({len(entities)})")
            for ent in entities[:10]:
                lines.append(f"- **{ent['name']}** ({ent['mention_count']} mentions)")
            if len(entities) > 10:
                lines.append(f"  ... and {len(entities) - 10} more")
            lines.append("")
    else:
        lines.append("## Active Entities This Week\n*No entity activity in the past 7 days.*\n")

    # Recent relations
    if recent_relations:
        lines.append(f"## New Relations This Week ({len(recent_relations)})\n")
        for rel in recent_relations[:15]:
            lines.append(
                f"- {rel['entity_a_name']} --[{rel['relation_type']}]--> "
                f"{rel['entity_b_name']}"
            )
        if len(recent_relations) > 15:
            lines.append(f"  ... and {len(recent_relations) - 15} more")
        lines.append("")
    else:
        lines.append("## New Relations This Week\n*No new relations discovered.*\n")

    # Source breakdown
    lines.append("## Notes by Source\n")
    for source, count in index_stats.get("by_source", {}).items():
        lines.append(f"- **{source}:** {count} notes")

    # Entity type breakdown
    if entity_stats["by_entity_type"]:
        lines.append("\n## Entity Breakdown\n")
        for etype, count in entity_stats["by_entity_type"].items():
            lines.append(f"- **{etype}:** {count}")

    # Relation type breakdown
    if entity_stats["by_relation_type"]:
        lines.append("\n## Relation Types\n")
        for rtype, count in entity_stats["by_relation_type"].items():
            lines.append(f"- **{rtype}:** {count}")

    return "\n".join(lines)


def _gather_graph_context(question: str, db: RootDB) -> list[dict]:
    """Extract entity-based context for a question.

    Tokenizes the question, looks up each word as a potential entity,
    and gathers neighborhood + relations for matches.
    """
    # Simple word-based entity lookup (skip common words)
    stop_words = {
        "what", "who", "when", "where", "why", "how", "is", "are", "was",
        "were", "the", "a", "an", "in", "on", "at", "to", "for", "of",
        "with", "about", "did", "does", "do", "has", "have", "had",
        "this", "that", "these", "those", "and", "or", "but", "not",
        "all", "any", "some", "my", "your", "his", "her", "its",
        "from", "by", "say", "said", "tell", "me", "you", "i",
    }

    words = question.replace("?", "").replace(",", "").split()
    candidates = [w for w in words if w.lower() not in stop_words and len(w) > 2]

    # Also try multi-word phrases (2-grams and 3-grams)
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i + 1]}"
        if not all(w.lower() in stop_words for w in bigram.split()):
            candidates.append(bigram)
    for i in range(len(words) - 2):
        trigram = f"{words[i]} {words[i + 1]} {words[i + 2]}"
        if not all(w.lower() in stop_words for w in trigram.split()):
            candidates.append(trigram)

    graph_context = []
    seen_ids = set()

    for candidate in candidates:
        entity_id = db.resolve_entity(candidate)
        if entity_id and entity_id not in seen_ids:
            seen_ids.add(entity_id)
            neighborhood = db.get_entity_neighborhood(entity_id, depth=1)
            relations = db.get_entity_relations(entity_id)
            notes = db.get_notes_for_entity(entity_id)
            graph_context.append({
                "entity_name": candidate,
                "entity_id": entity_id,
                "neighbors": neighborhood,
                "relations": relations[:10],
                "notes": notes[:5],
            })

    return graph_context


def _format_context(search_results: list[dict], graph_context: list[dict]) -> str:
    """Format combined search + graph context for LLM synthesis."""
    sections = []

    if search_results:
        sections.append("## Relevant Notes\n")
        for r in search_results:
            sections.append(f"### {r['title']} ({r['folder']}, {r['source_type']})")
            sections.append(r["snippet"])
            sections.append("")

    if graph_context:
        sections.append("## Entity Graph Context\n")
        for gc in graph_context:
            sections.append(f"### Entity: {gc['entity_name']}")

            if gc["neighbors"]:
                neighbor_names = [
                    f"{n['name']} ({n['entity_type']})"
                    for n in gc["neighbors"][:10]
                ]
                sections.append(f"Connected to: {', '.join(neighbor_names)}")

            if gc["relations"]:
                sections.append("Relations:")
                for rel in gc["relations"][:5]:
                    sections.append(
                        f"- {rel['entity_a_name']} --[{rel['relation_type']}]--> "
                        f"{rel['entity_b_name']}: {rel.get('context', '')}"
                    )

            if gc["notes"]:
                sections.append(f"Mentioned in: {', '.join(n['title'] for n in gc['notes'][:5])}")

            sections.append("")

    return "\n".join(sections)
