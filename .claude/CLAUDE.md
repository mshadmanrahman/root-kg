# ROOT: Personal Knowledge Graph

Semantic intelligence layer across Shadman's knowledge surfaces.
MCP server exposing search, pattern discovery, entity graph, and Q&A to Claude Code.

## Quick Start

```bash
cd _tools/root && source .venv/bin/activate
python indexer.py                    # Index/re-index vault
python indexer.py --extract          # Index + entity extraction
python indexer.py --extract-only     # Just entity extraction
python indexer.py --extract-only --limit 10  # Test on 10 notes
python server.py                     # Run MCP server (stdio, for testing)
```

## MCP Tools (18 total)

### Search & Discovery (Phase 1)
| Tool | Purpose |
|------|---------|
| `root_search(query)` | Semantic search across all indexed notes |
| `root_search_folder(query, folder)` | Search scoped to a vault folder |
| `root_note(path)` | Read full note content |
| `root_connections(path)` | Find cross-domain connections for a note |
| `root_themes(scope, num_themes)` | Discover recurring themes via clustering |
| `root_gaps(topic)` | Find knowledge gaps and blind spots |
| `root_stats()` | Index health and statistics |

### Multi-Source (Phase 2)
| Tool | Purpose |
|------|---------|
| `root_ingest(source_type, title, content, path)` | Ingest from any MCP source |
| `root_ingest_batch(items)` | Batch ingest |
| `root_about(person)` | Everything about a person across all sources |
| `root_open_loops(scope)` | Find unfollowed action items |
| `root_project_pulse(project)` | Activity pulse for a project |

### Entity Graph & Intelligence (Phase 3)
| Tool | Purpose |
|------|---------|
| `root_graph(entity, depth)` | Entity neighborhood traversal |
| `root_influence_map(project)` | Who touched a project, through what |
| `root_decision_trail(topic)` | How decisions evolved over time |
| `root_blind_spots()` | Entities with declining activity |
| `root_ask(question)` | Free-form Q&A (semantic + graph + LLM) |
| `root_weekly_digest()` | Weekly activity summary |

## Architecture

- **Embeddings:** `all-MiniLM-L6-v2` (local, free, 384 dims)
- **Storage:** SQLite + sqlite-vec (single file at `data/root.db`)
- **LLM:** Anthropic API via Haiku (extraction) and Sonnet (synthesis)
- **MCP:** stdio transport, registered as `root` in Claude Code
- **Indexing:** Incremental via SHA-256 content hashing
- **Entity extraction:** Incremental via hash tracking in entity_extractions table
- **Graph queries:** Recursive CTEs for traversal (fast at <5K entities)
- **Auto-refresh:** launchd daemon every 2 hours (com.shadman.root-refresh.plist)

## Database Tables

- `notes` - Full note content + metadata
- `chunks` / `chunks_vec` - Embeddings for semantic search
- `entities` - People, projects, decisions, events, concepts, organizations
- `entity_aliases` - Name normalization (e.g., "Fredrik" = "Frederick")
- `entity_note_links` - Which entities appear in which notes
- `relations` - Typed relations with confidence and source context
- `entity_extractions` - Hash tracking for incremental extraction

## Config

- `config.yaml` - Sources, embedding model, LLM backend
- `.env` - API keys (gitignored)
- Backend options: `anthropic` (default) or `openrouter`
