"""
ROOT MCP Server.

Exposes semantic search, pattern discovery, gap analysis,
entity graph traversal, and intelligence tools
to Claude Code via the Model Context Protocol.
"""

import sys
from pathlib import Path

import yaml
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from db import RootDB
from embeddings import Embedder
from chunker import chunk_note
from llm import LLMClient
from tools.search import semantic_search, search_by_folder
from tools.patterns import find_connections, discover_themes, find_gaps
from tools.correlations import about_person, open_loops, project_pulse
from tools.graph import entity_graph, influence_map, decision_trail, blind_spots
from tools.intelligence import ask, weekly_digest

# Load config
PROJECT_ROOT = Path(__file__).parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

with open(CONFIG_PATH) as f:
    CONFIG = yaml.safe_load(f)

DB_PATH = PROJECT_ROOT / CONFIG["database"]["path"]

# Lazy-loaded singletons
_db: RootDB | None = None
_embedder: Embedder | None = None
_llm: LLMClient | None = None


def get_db() -> RootDB:
    global _db
    if _db is None:
        _db = RootDB(DB_PATH)
    return _db


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder(CONFIG["embeddings"]["model"])
    return _embedder


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        llm_config = CONFIG.get("llm", {})
        _llm = LLMClient(
            backend=llm_config.get("backend", "anthropic"),
            extraction_model=llm_config.get("extraction_model"),
            synthesis_model=llm_config.get("synthesis_model"),
        )
    return _llm


# Create MCP server
app = Server("root")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="root_search",
            description="Semantic search across all indexed knowledge (Obsidian vault, meetings, email). Returns the most relevant notes for a natural language query.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
                    "source": {"type": "string", "description": "Filter by source: 'vault', 'granola', 'gmail', or omit for all", "default": None},
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="root_search_folder",
            description="Semantic search scoped to a specific Obsidian vault folder (e.g., 'Keystone', 'Side Projects', 'Career').",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language search query"},
                    "folder": {"type": "string", "description": "Vault folder name to scope search to"},
                    "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
                },
                "required": ["query", "folder"],
            },
        ),
        Tool(
            name="root_note",
            description="Read the full content of a specific note by its path (as returned by root_search).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Note path (relative to vault root)"},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="root_connections",
            description="Find unexpected cross-domain connections for a note. Surfaces notes that are semantically related but live in different folders/contexts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Note path to find connections for"},
                    "limit": {"type": "integer", "description": "Max connections (default 10)", "default": 10},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="root_themes",
            description="Discover recurring themes across your knowledge base by clustering semantically similar notes. Reveals patterns you might not see manually.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "Scope: 'all' or a specific folder name", "default": "all"},
                    "num_themes": {"type": "integer", "description": "Number of themes to discover (default 8)", "default": 8},
                },
            },
        ),
        Tool(
            name="root_gaps",
            description="Find knowledge gaps and blind spots around a topic. Shows what's tangentially mentioned but never explored, and which domains are surprisingly absent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic to analyze for gaps"},
                },
                "required": ["topic"],
            },
        ),
        Tool(
            name="root_stats",
            description="Get ROOT index statistics: total notes, chunks, sources, top folders, last indexed time.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="root_ingest",
            description="Ingest content from any source (meeting transcript, email, Slack message) into ROOT's index. Call this after fetching content from Granola, Gmail, or Slack MCPs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_type": {"type": "string", "description": "Source type: 'granola', 'gmail', 'slack'", "enum": ["granola", "gmail", "slack"]},
                    "title": {"type": "string", "description": "Title (meeting name, email subject, or Slack thread summary)"},
                    "content": {"type": "string", "description": "Full text content to index"},
                    "path": {"type": "string", "description": "Unique identifier (meeting ID, email thread ID, Slack message URL)"},
                    "folder": {"type": "string", "description": "Category folder (e.g., 'Meetings', 'Email', 'Slack-general')"},
                    "created_at": {"type": "string", "description": "ISO date when the content was created (optional)"},
                },
                "required": ["source_type", "title", "content", "path"],
            },
        ),
        Tool(
            name="root_ingest_batch",
            description="Ingest multiple items at once. More efficient than calling root_ingest repeatedly. Each item has the same shape as root_ingest.",
            inputSchema={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Array of items to ingest",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source_type": {"type": "string"},
                                "title": {"type": "string"},
                                "content": {"type": "string"},
                                "path": {"type": "string"},
                                "folder": {"type": "string"},
                                "created_at": {"type": "string"},
                            },
                            "required": ["source_type", "title", "content", "path"],
                        },
                    },
                },
                "required": ["items"],
            },
        ),
        Tool(
            name="root_about",
            description="Everything ROOT knows about a person across all sources (vault notes, meetings, emails, Slack). Surfaces interactions, decisions, and open items.",
            inputSchema={
                "type": "object",
                "properties": {
                    "person": {"type": "string", "description": "Person's name (e.g., 'Ric', 'Sebastian', 'Fredrik')"},
                    "limit": {"type": "integer", "description": "Max results per source (default 5)", "default": 5},
                },
                "required": ["person"],
            },
        ),
        Tool(
            name="root_open_loops",
            description="Find open loops: things discussed or promised but never followed up on. Searches for action items, TODOs, commitments, and decisions across all sources.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "description": "Scope: 'all', a person name, or a project name", "default": "all"},
                    "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
                },
            },
        ),
        Tool(
            name="root_project_pulse",
            description="Activity pulse for a project across all sources. Shows recent mentions in vault, meetings, email, and Slack.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name (e.g., 'Heimdall', 'MatchMaker', 'Ceremonies')"},
                    "limit": {"type": "integer", "description": "Max results per source (default 5)", "default": 5},
                },
                "required": ["project"],
            },
        ),
        # Phase 3: Entity Graph tools
        Tool(
            name="root_graph",
            description="Traverse the entity knowledge graph. Shows an entity's neighborhood: connected people, projects, decisions, and their relations up to N hops.",
            inputSchema={
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "Entity name (person, project, concept)"},
                    "depth": {"type": "integer", "description": "Traversal depth (default 2, max 4)", "default": 2},
                },
                "required": ["entity"],
            },
        ),
        Tool(
            name="root_influence_map",
            description="Map who influenced a project and through what actions. Shows stakeholders, their roles, and interaction evidence.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name (e.g., 'Heimdall', 'MatchMaker')"},
                },
                "required": ["project"],
            },
        ),
        Tool(
            name="root_decision_trail",
            description="Trace how decisions evolved around a topic over time. Shows decision entities, who was involved, and chronological progression.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic to trace decisions for"},
                },
                "required": ["topic"],
            },
        ),
        Tool(
            name="root_blind_spots",
            description="Detect entities with declining activity. Surfaces people, projects, and topics that were actively discussed but have gone silent (30+ days inactive, 3+ prior mentions).",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="root_ask",
            description="Free-form Q&A combining semantic search, entity graph, and LLM synthesis. Ask any question about your knowledge and get a cited answer.",
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Natural language question about your knowledge"},
                },
                "required": ["question"],
            },
        ),
        Tool(
            name="root_weekly_digest",
            description="Weekly digest of knowledge graph activity: new entities, relations, activity by source, and overall health stats.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    db = get_db()
    embedder = get_embedder()

    try:
        if name == "root_search":
            results = semantic_search(
                query=arguments["query"],
                db=db,
                embedder=embedder,
                limit=arguments.get("limit", 10),
                source_type=arguments.get("source"),
            )
            return [TextContent(type="text", text=_format_search_results(results, arguments["query"]))]

        elif name == "root_search_folder":
            results = search_by_folder(
                query=arguments["query"],
                folder=arguments["folder"],
                db=db,
                embedder=embedder,
                limit=arguments.get("limit", 10),
            )
            return [TextContent(type="text", text=_format_search_results(results, arguments["query"]))]

        elif name == "root_note":
            note = db.get_note_by_path(arguments["path"])
            if not note:
                return [TextContent(type="text", text=f"Note not found: {arguments['path']}")]
            return [TextContent(type="text", text=f"# {note['title']}\n\nPath: {note['path']}\nFolder: {note['folder']}\nIndexed: {note['indexed_at']}\n\n---\n\n{note['content']}")]

        elif name == "root_connections":
            connections = find_connections(
                note_path=arguments["path"],
                db=db,
                embedder=embedder,
                limit=arguments.get("limit", 10),
            )
            return [TextContent(type="text", text=_format_connections(connections))]

        elif name == "root_themes":
            themes = discover_themes(
                db=db,
                embedder=embedder,
                scope=arguments.get("scope", "all"),
                num_themes=arguments.get("num_themes", 8),
            )
            return [TextContent(type="text", text=_format_themes(themes))]

        elif name == "root_gaps":
            gaps = find_gaps(
                topic=arguments["topic"],
                db=db,
                embedder=embedder,
            )
            return [TextContent(type="text", text=_format_gaps(gaps, arguments["topic"]))]

        elif name == "root_stats":
            stats = db.get_stats()
            lines = [
                "# ROOT Index Stats",
                f"Total notes: {stats['total_notes']}",
                f"Total chunks: {stats['total_chunks']}",
                f"Last indexed: {stats['last_indexed']}",
                "\n## By Source",
            ]
            for src, count in stats["by_source"].items():
                lines.append(f"  {src}: {count}")
            lines.append("\n## Top Folders")
            for folder, count in stats["top_folders"].items():
                lines.append(f"  {folder}: {count}")
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "root_ingest":
            result = _ingest_single(arguments, db, embedder)
            return [TextContent(type="text", text=result)]

        elif name == "root_ingest_batch":
            items = arguments.get("items", [])
            results = []
            for item in items:
                results.append(_ingest_single(item, db, embedder))
            return [TextContent(type="text", text=f"Ingested {len(items)} items.\n" + "\n".join(results))]

        elif name == "root_about":
            results = about_person(
                person=arguments["person"],
                db=db,
                embedder=embedder,
                limit=arguments.get("limit", 5),
            )
            return [TextContent(type="text", text=results)]

        elif name == "root_open_loops":
            results = open_loops(
                db=db,
                embedder=embedder,
                scope=arguments.get("scope", "all"),
                limit=arguments.get("limit", 10),
            )
            return [TextContent(type="text", text=results)]

        elif name == "root_project_pulse":
            results = project_pulse(
                project=arguments["project"],
                db=db,
                embedder=embedder,
                limit=arguments.get("limit", 5),
            )
            return [TextContent(type="text", text=results)]

        # Phase 3: Entity Graph + Intelligence tools
        elif name == "root_graph":
            depth = min(arguments.get("depth", 2), 4)
            result = entity_graph(
                entity_name=arguments["entity"],
                db=db,
                depth=depth,
            )
            return [TextContent(type="text", text=result)]

        elif name == "root_influence_map":
            result = influence_map(
                project_name=arguments["project"],
                db=db,
                embedder=embedder,
            )
            return [TextContent(type="text", text=result)]

        elif name == "root_decision_trail":
            result = decision_trail(
                topic=arguments["topic"],
                db=db,
                embedder=embedder,
            )
            return [TextContent(type="text", text=result)]

        elif name == "root_blind_spots":
            result = blind_spots(db=db)
            return [TextContent(type="text", text=result)]

        elif name == "root_ask":
            llm = get_llm()
            result = ask(
                question=arguments["question"],
                db=db,
                embedder=embedder,
                llm=llm,
            )
            return [TextContent(type="text", text=result)]

        elif name == "root_weekly_digest":
            result = weekly_digest(db=db)
            return [TextContent(type="text", text=result)]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error in {name}: {str(e)}")]


def _ingest_single(item: dict, db: RootDB, embedder: Embedder) -> str:
    """Ingest a single item into ROOT's index."""
    import hashlib
    from datetime import datetime, timezone

    source_type = item["source_type"]
    title = item["title"]
    content = item["content"]
    path = item.get("path", f"{source_type}/{hashlib.md5(title.encode()).hexdigest()}")
    folder = item.get("folder", source_type.capitalize())
    created_at = item.get("created_at")
    now = datetime.now(timezone.utc).isoformat()

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # Check if already indexed with same content
    existing_hash = db.get_note_hash(path)
    if existing_hash == content_hash:
        return f"  Unchanged: {title}"

    # Upsert note
    note_id = db.upsert_note(
        path=path,
        title=title,
        content=content,
        content_hash=content_hash,
        folder=folder,
        source_type=source_type,
        created_at=created_at,
        indexed_at=now,
    )

    # Chunk and embed
    chunks = chunk_note(content, title)
    if chunks:
        texts = [c["text"] for c in chunks]
        embeddings = embedder.embed_batch(texts)
        indexed_chunks = [
            {"idx": c["idx"], "text": c["text"], "embedding": emb}
            for c, emb in zip(chunks, embeddings)
        ]
        db.store_chunks(note_id, indexed_chunks)

    action = "Updated" if existing_hash else "Ingested"
    return f"  {action}: {title} ({len(chunks)} chunks)"


def _format_search_results(results: list[dict], query: str) -> str:
    if not results:
        return f"No results found for: {query}"

    lines = [f"# Search: {query}", f"Found {len(results)} results:\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"## {i}. {r['title']}")
        lines.append(f"**Path:** {r['path']}  |  **Folder:** {r['folder']}  |  **Distance:** {r['distance']}")
        lines.append(f"\n{r['snippet']}\n")
    return "\n".join(lines)


def _format_connections(connections: list[dict]) -> str:
    if not connections:
        return "No cross-domain connections found."

    lines = ["# Cross-Domain Connections\n"]
    for c in connections:
        lines.append(f"## {c['title']}")
        lines.append(f"**Folder:** {c['folder']}  |  **Distance:** {c['distance']}")
        lines.append(f"*{c['why']}*")
        lines.append(f"\n{c['snippet']}\n")
    return "\n".join(lines)


def _format_themes(themes: list[dict]) -> str:
    if not themes:
        return "Not enough data to discover themes."

    lines = ["# Discovered Themes\n"]
    for i, t in enumerate(themes, 1):
        cross = " (CROSS-DOMAIN)" if t.get("cross_domain") else ""
        lines.append(f"## Theme {i}: {t['theme_label']}{cross}")
        lines.append(f"**Notes in cluster:** {t['note_count']}  |  **Folders:** {', '.join(t['spans_folders'])}")
        lines.append("Representative notes:")
        for n in t["representative_notes"]:
            lines.append(f"  - {n}")
        lines.append("")
    return "\n".join(lines)


def _format_gaps(gaps: list[dict], topic: str) -> str:
    if not gaps:
        return f"No gaps found for: {topic}"

    lines = [f"# Knowledge Gaps: {topic}\n"]
    for g in gaps:
        if "gap" in g:
            lines.append(f"- {g['gap']}")
        else:
            lines.append(f"### {g.get('type', 'gap').replace('_', ' ').title()}")
            lines.append(f"{g['insight']}")
            if "title" in g:
                lines.append(f"  Note: {g['title']} ({g['folder']})")
            lines.append("")
    return "\n".join(lines)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
