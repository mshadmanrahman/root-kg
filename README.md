<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License">
  <img src="https://img.shields.io/badge/MCP-native-purple?style=flat-square" alt="MCP Native">
  <img src="https://img.shields.io/badge/LLM-Anthropic%20%7C%20OpenRouter%20%7C%20Ollama-orange?style=flat-square" alt="Multi-LLM">
  <img src="https://img.shields.io/badge/storage-SQLite-lightgrey?style=flat-square&logo=sqlite" alt="SQLite">
</p>

<h1 align="center">ROOT</h1>

<p align="center">
  <strong>Personal knowledge graph with entity extraction, GraphRAG, and MCP integration.</strong><br>
  Turn your scattered notes into searchable intelligence that AI tools can query in real-time.
</p>

<p align="center">
  <img src="assets/hero.png" alt="ROOT - Ask a question, see the graph, get a cited answer" width="800">
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#how-it-works">How It Works</a> &bull;
  <a href="#18-mcp-tools">Tools</a> &bull;
  <a href="#llm-backends">LLM Backends</a> &bull;
  <a href="#use-cases">Use Cases</a>
</p>

---

## The Problem

You have knowledge everywhere. Obsidian vault. Meeting transcripts. Email threads. Slack messages. When you need to answer "Who influences this project?" or "How did this decision evolve?", you're manually searching across systems, holding the mental model in your head.

**ROOT fixes this.** It indexes everything, extracts entities and relations, builds a graph, and gives your AI tools a way to query it all in real-time.

```
> root_ask("What decisions were made about the pricing model?")

# ROOT Answer

Ric is developing a new pricing model with weighted intensity scoring,
school ranking, course count, and FOS difficulty metrics. This was
discussed in the 1-1 with Ric (Feb 2026) and the ESP Lead Capping
meeting. Timeline: Option B pricing model by mid-January.

*Based on 5 search results and 3 entity matches.*
```

## How It Works

ROOT builds three layers on top of your notes:

```
┌─────────────────────────────────────────────────────────────────┐
│                        root_ask (GraphRAG)                      │
│              Semantic Search + Graph + LLM Synthesis             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────┐    ┌──────────────────────────────────┐  │
│  │  LAYER 1          │    │  LAYER 3                         │  │
│  │  Semantic Search   │    │  Entity Graph                    │  │
│  │                    │    │                                  │  │
│  │  "pricing model"   │    │  [Ric] ──owns──▶ [Pricing Model] │  │
│  │       ↓            │    │    │                     │        │  │
│  │  ┌──────────┐      │    │    ├──discussed──▶ [Heimdall]    │  │
│  │  │ chunk 1  │ 0.92 │    │    │                     │        │  │
│  │  │ chunk 2  │ 0.87 │    │    └──works_with──▶ [Scott]      │  │
│  │  │ chunk 3  │ 0.84 │    │                                  │  │
│  │  └──────────┘      │    │  Recursive CTE traversal on      │  │
│  │  384-dim vectors   │    │  SQLite. <10ms at depth 2.       │  │
│  └──────────────────┘    └──────────────────────────────────┘  │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  LAYER 2: Multi-Source Ingestion                                │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Obsidian  │  │ Meetings │  │  Email   │  │  Slack   │      │
│  │  Vault    │  │ (Granola)│  │ (Gmail)  │  │          │      │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘  └─────┬────┘      │
│        └──────────────┴──────────────┴──────────────┘           │
│                           ↓                                     │
│              ┌────────────────────────┐                         │
│              │   data/root.db         │                         │
│              │   Single SQLite file   │                         │
│              │   Notes + Embeddings   │                         │
│              │   Entities + Relations │                         │
│              └────────────────────────┘                         │
└─────────────────────────────────────────────────────────────────┘
```

### Layer 1: Semantic Search
Every note is embedded locally using `all-MiniLM-L6-v2` (free, CPU, 384 dims). Search finds notes by *meaning*, not just keywords.

### Layer 2: Multi-Source Ingestion
Pull content from any source via MCP adapters. Meetings, email, Slack: everything goes into one unified index.

### Layer 3: Entity Graph
An LLM extracts structured data from each note:

```
Note: "Ric discussed Heimdall timeline. Sebastian architected the API."
                           ↓ LLM extraction
┌─────────────────────────────────────────────────┐
│ Entities:                                        │
│   [Ric]        (person)                          │
│   [Sebastian]  (person)                          │
│   [Heimdall]   (project)                         │
│                                                   │
│ Relations:                                        │
│   Ric ──discussed──▶ Heimdall     (95%)          │
│   Sebastian ──created──▶ Heimdall  (90%)          │
│                                                   │
│ Context: "Sebastian architected the API"          │
└─────────────────────────────────────────────────┘
```

### GraphRAG: The Intelligence Layer
`root_ask` fuses all three layers:
1. **Semantic search** finds the 5 most relevant chunks
2. **Entity graph** pulls the neighborhood of mentioned entities
3. **LLM synthesis** produces a cited, natural language answer

This consistently outperforms pure vector search for multi-hop questions.

## Quick Start

```bash
# Clone and setup
git clone https://github.com/mshadmanrahman/root-kg.git
cd root-kg
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Interactive setup wizard
python -m root init

# Index your notes (~2 min for 2,500 notes)
python indexer.py

# Extract entities (~$3 on Anthropic Haiku, or free with Ollama)
python indexer.py --extract

# Register as MCP server in Claude Code
claude mcp add root -- python server.py

# Try it
# root_search("your topic")
# root_ask("your question")
# root_graph("person name", 2)
```

## 18 MCP Tools

### Search & Discovery
```
root_search(query)              Semantic search across all notes
root_search_folder(query, dir)  Search within a specific folder
root_note(path)                 Read full note content
root_stats()                    Index health and statistics
root_connections(path)          Cross-domain connections for a note
root_themes(scope)              Recurring themes via clustering
root_gaps(topic)                Knowledge gaps and blind spots
```

### Multi-Source Intelligence
```
root_ingest(source, title, content)  Ingest from any MCP source
root_ingest_batch(items)             Batch ingest
root_about(person)                   Everything about a person
root_open_loops(scope)               Unfollowed action items
root_project_pulse(project)          Activity pulse for a project
```

### Entity Graph & GraphRAG
```
root_graph(entity, depth)       Entity neighborhood traversal
root_influence_map(project)     Who touched this project, through what
root_decision_trail(topic)      How decisions evolved over time
root_blind_spots()              Entities with declining activity
root_ask(question)              Free-form Q&A (GraphRAG)
root_weekly_digest()            Weekly activity summary
```

## Use Cases

### For Product Managers
- **"Who influences Project X?"** `root_influence_map("Project X")` shows every stakeholder, their role, and evidence from meetings and notes
- **"What decisions were made about pricing?"** `root_decision_trail("pricing")` traces the chronological evolution
- **"What did I promise Ric last week?"** `root_open_loops("Ric")` surfaces unfollowed action items
- **"Brief me before my 1:1"** `root_about("colleague name")` pulls everything across all sources

### For Engineers
- **"How does the auth system work?"** `root_ask("authentication architecture")` synthesizes from architecture docs, meeting notes, and ADRs
- **"What depends on this service?"** `root_graph("service name", 2)` shows the dependency graph
- **"What's gone stale?"** `root_blind_spots()` finds topics that were hot but went silent

### For Researchers & Writers
- **"What themes connect my notes?"** `root_themes()` discovers patterns via clustering
- **"What am I missing about this topic?"** `root_gaps("your topic")` finds blind spots
- **"Connect the dots"** `root_connections("note path")` finds unexpected cross-domain links

### For Teams
- **"Weekly knowledge pulse"** `root_weekly_digest()` summarizes what changed across all sources
- **"Project health check"** `root_project_pulse("project")` shows activity across notes, meetings, and email

## LLM Backends

Three backends for entity extraction and Q&A synthesis:

```
┌─────────────┬──────────────────┬─────────┬────────────────────┐
│ Backend     │ Cost             │ Quality │ Setup              │
├─────────────┼──────────────────┼─────────┼────────────────────┤
│ Anthropic   │ ~$3 / 2,500      │ Best    │ ANTHROPIC_API_KEY  │
│ (default)   │ notes            │         │ in .env            │
├─────────────┼──────────────────┼─────────┼────────────────────┤
│ OpenRouter  │ Free $1 credit   │ Good    │ OPENROUTER_API_KEY │
│             │                  │         │ in .env            │
├─────────────┼──────────────────┼─────────┼────────────────────┤
│ Ollama      │ Free (local)     │ Lower   │ ollama pull        │
│             │                  │         │ llama3.1           │
└─────────────┴──────────────────┴─────────┴────────────────────┘
```

Set in `config.yaml`:
```yaml
llm:
  backend: "anthropic"  # or "openrouter" or "ollama"
```

Embeddings are always free and local. Only entity extraction and `root_ask` use the LLM.

## Architecture

```
root-kg/
├── server.py           # MCP server (18 tools, stdio transport)
├── db.py               # SQLite + sqlite-vec + entity graph
├── embeddings.py       # Local embedding model (free, CPU)
├── llm.py              # Multi-backend LLM (zero pip deps, stdlib urllib)
├── extractor.py        # Incremental entity extraction pipeline
├── indexer.py           # Vault indexer + extraction orchestrator
├── cli.py              # Setup wizard (python -m root init)
├── chunker.py          # Markdown-aware note splitter
├── tools/
│   ├── search.py       # Semantic search
│   ├── patterns.py     # Themes, connections, gaps
│   ├── correlations.py # About, open loops, pulse
│   ├── graph.py        # Entity graph, influence map, decision trail
│   └── intelligence.py # root_ask (GraphRAG), weekly digest
├── config.example.yaml # Template config
├── .env.example        # Template env
└── data/root.db        # Everything in one file (gitignored)
```

**Design principles:**
- **Single file database.** No Postgres, no Neo4j, no Docker. One SQLite file.
- **Zero new pip deps for LLM.** Uses stdlib `urllib` for API calls. No `anthropic` or `openai` SDK.
- **Incremental everything.** SHA-256 content hashing for both indexing and extraction. Only changed notes are reprocessed.
- **Immutable data patterns.** All functions return new data, never mutate inputs.
- **Graph on SQLite.** Recursive CTEs for traversal. <10ms at depth 2 with thousands of entities.

## Comparison

| Feature | ROOT | Obsidian Graph | Mem.ai | Khoj | Rewind |
|---------|------|----------------|--------|------|--------|
| Entity extraction | LLM-powered | None | None | None | None |
| Typed relations | Yes (10 types) | Backlinks only | No | No | No |
| GraphRAG | Yes | No | Basic RAG | Basic RAG | No |
| Multi-source | Notes+meetings+email | Notes only | Yes | Notes only | Everything |
| MCP native | Yes | No | No | No | No |
| Self-hosted | Yes | Yes | No | Yes | No |
| Single file DB | Yes | N/A | Cloud | Postgres | Cloud |
| Free embeddings | Yes (local) | N/A | No | Yes | No |
| Privacy | 100% local | 100% local | Cloud | Hybrid | Cloud |
| Cost | ~$3 one-time | Free | $20/mo | Free* | $20/mo |

## Auto-refresh

ROOT includes a macOS `launchd` plist that re-indexes every 2 hours:

```bash
cp com.shadman.root-refresh.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.shadman.root-refresh.plist
```

Only changed notes are reprocessed. Typical incremental run: <30 seconds.

## Contributing

PRs welcome. The codebase is intentionally simple: Python 3.11+, no frameworks, small files (<400 lines each).

Areas that would benefit from contributions:
- **Adapters**: LogSeq, Notion, Apple Notes, Google Docs
- **Backends**: Google Gemini, local models via llama.cpp
- **Visualization**: Web UI for entity graph exploration
- **Platforms**: Linux systemd timer (equivalent to macOS launchd)

## Requirements

- Python 3.11+
- ~500MB disk for embeddings model (downloaded on first run)
- One of: Anthropic API key ($5 min), OpenRouter key (free $1), or Ollama

## License

MIT

---

<p align="center">
  Built by <a href="https://github.com/mshadmanrahman">Shadman Rahman</a>
</p>
