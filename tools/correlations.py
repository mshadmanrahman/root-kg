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
    """Everything ROOT knows about a person across all sources."""
    # Search for the person across all sources
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

    # Group by source type
    by_source = defaultdict(list)
    for r in all_results:
        by_source[r["source_type"]].append(r)

    lines = [f"# Everything about: {person}\n"]
    lines.append(f"Found {len(all_results)} relevant items across {len(by_source)} sources.\n")

    source_labels = {
        "vault": "Obsidian Vault Notes",
        "granola": "Meeting Transcripts",
        "gmail": "Email Threads",
        "slack": "Slack Messages",
    }

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
