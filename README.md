<p align="center">
  <a href="https://pypi.org/project/root-kg/"><img src="https://img.shields.io/pypi/v/root-kg?style=flat-square&color=blue" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License">
  <img src="https://img.shields.io/badge/MCP-native-purple?style=flat-square" alt="MCP Native">
  <img src="https://img.shields.io/badge/LLM-Anthropic%20%7C%20OpenRouter%20%7C%20Ollama-orange?style=flat-square" alt="Multi-LLM">
  <img src="https://img.shields.io/badge/storage-SQLite-lightgrey?style=flat-square&logo=sqlite" alt="SQLite">
</p>

<h1 align="center">ROOT</h1>

<p align="center">
  <strong>Ask questions across all your knowledge. Get cited answers.</strong><br>
  Turn your Obsidian vault, meeting notes, and emails into a queryable intelligence layer.
</p>

Read the story behind it: [My knowledge graph got fifteen stars](https://shadmanrahman.substack.com/p/my-knowledge-graph-got-fifteen-stars)

<p align="center">
  <img src="assets/hero.png" alt="ROOT: your notes, indexed and queryable from Claude Code" width="800">
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#what-you-can-ask-root">What You Can Ask</a> &bull;
  <a href="#how-it-works">How It Works</a> &bull;
  <a href="#the-18-mcp-tools">Tools</a> &bull;
  <a href="#running-costs">Running Costs</a> &bull;
  <a href="#comparison">Comparison</a>
</p>

---

You wrote it down. You know you did. It was in a meeting note, or a chat thread you pasted into Obsidian, or a document you made before the planning session. Now you are searching and finding nothing, or finding five things that contradict each other, and you are holding the whole mental model in your head again.

ROOT connects your notes, meetings and emails into one queryable layer. You ask in plain English, the way you would ask a colleague who had read everything you ever wrote.

---

## Quick Start

```bash
# Install. A venv or pipx keeps the embedding model's dependencies out of your system Python
pip install root-kg

# Answer the setup wizard: where your notes live, which LLM backend
root-kg init

# Index your notes. Embeddings run locally and cost nothing
root-index
# Extract entities into the graph. This step calls an LLM
root-index --extract

# Register ROOT with Claude Code, using the absolute path to the server
claude mcp add root "$(which root-server)"
```

`root-kg init` writes `config.yaml`, `data/` and `logs/` to `~/.root-kg`. Set `ROOT_KG_HOME` to put them somewhere else. A git clone keeps them in the repo root instead, see [Development](#development).

Indexing prints what it touched, so you can see the incremental behaviour on the second run:

```
ROOT indexer started at 2026-09-11T08:30:04Z
Scanning vault: ~/Documents/My Vault
Embedding 23 notes (9 new, 14 updated)...
  Embedded 118/118 chunks...
Done. 9 new, 14 updated, 412 unchanged, 3 removed, 0 errors.
Extraction complete: 23 processed, 147 entities, 96 relations, 0 errors
```

Now ask ROOT something from Claude Code: `root_search("your topic")`, `root_ask("your question")`, `root_graph("person name", 2)`. Two more worth knowing: `root-kg stats` prints what the index holds, and `root-index --extract-only --limit 10` tries extraction on ten notes before you pay for the rest.

> ROOT talks to Claude Code as an MCP server. If Claude Code is new to you, [claudecodeguide.dev](https://claudecodeguide.dev) gets you set up in under an hour.

## What You Can Ask ROOT

The questions that otherwise mean opening six tabs and rebuilding the story from memory:

- "What did I commit to Maya last week?"
- "How did the pricing decision evolve over the last month?"
- "Who has been working on Project X, and through what?"
- "What action items from last quarter are still open?"
- "Brief me on this person before my 1:1, everything we have discussed."

You ask in plain English. ROOT synthesizes an answer and cites the notes it came from:

```
> root_ask("What decisions were made about the Search Redesign?")

# ROOT Answer

Leadership APPROVED the Search Redesign project on March 17, 2026.
Scope was locked at the kick-off meeting on March 23: consolidate
1,200 product categories down to 85 across 12 groups, following the
industry taxonomy. Owner: Alex Chen. Sprint start: April 7.

*Based on 5 search results and 2 entity matches.*
```

That answer came from five notes and two separate meetings. ROOT also knows that "Alex" in the kick-off note and "Alex Chen" in the planning doc are the same person, so you can walk the connections without opening a file.

## How It Works

<p align="center">
  <img src="assets/architecture.svg" alt="ROOT architecture: ingest, embed, extract, query" width="800">
</p>

Four steps, in order.

**Ingest.** The vault adapter scans your markdown and hashes each file with SHA-256. Meetings, emails and anything else arrive through `root_ingest`, which takes content from any other MCP server.

**Embed.** Notes are split at heading boundaries and embedded with `all-MiniLM-L6-v2`, a local model that runs on your CPU. Nothing leaves the machine and nothing is billed. Vectors go into SQLite through sqlite-vec.

**Extract.** For each new or changed note, an LLM pulls out entities (person, project, decision, event, concept, organization, tool, document, skill) and typed relations (`works_with`, `owns`, `decided`, `attended`, `discussed`, `blocked_by`, `depends_on`, `manages`, `created`, `reviewed`). Each relation carries a confidence score: 0.9 and above for explicit statements, 0.7 for implied, 0.5 for weak signals. Aliases are captured too, so "Fredrik" and "Frederick" resolve to one entity.

**Query.** `root_ask` runs semantic search for the most relevant chunks, pulls the graph neighborhood of the entities it finds, and hands both to the synthesis model for a cited answer. Graph traversal is a breadth-first walk in Python over indexed SQLite reads, with a visited set for cycle detection.

### The two-model split

Extraction runs on every changed note, so it uses Haiku (`llm.extraction_model` in `config.yaml`). Synthesis only runs when you ask a question, so it uses Sonnet (`llm.synthesis_model`). Embeddings never call an API at all.

### What is in the repo

```
root-kg/
├── root_kg/
│   ├── server.py          # MCP server: exposes the 18 tools over stdio
│   ├── indexer.py         # Reads configured sources, embeds them, orchestrates extraction
│   ├── extractor.py       # Incremental, hash-tracked entity and relation extraction
│   ├── db.py              # SQLite + sqlite-vec storage and entity graph with BFS traversal
│   ├── llm.py             # Multi-backend LLM client for extraction and synthesis
│   ├── embeddings.py      # Local embeddings via sentence-transformers, zero API cost
│   ├── chunker.py         # Splits long notes at heading boundaries
│   ├── cli.py             # Setup wizard, stats, and cron-callable search and note ingest
│   ├── paths.py           # Where config, data and logs live: ROOT_KG_HOME, the checkout, or ~/.root-kg
│   ├── config.example.yaml  # Template that root-kg init copies into config.yaml
│   ├── .env.example       # Template that root-kg init copies into .env
│   ├── query.py           # Calls any ROOT tool from a shell or another agent
│   ├── rootd.py           # Warm daemon: keeps DB and embedder loaded for fast local search
│   ├── merge_cli.py       # Folds duplicate entity shards into one canonical entity
│   ├── adapters/vault.py  # Markdown scanner for the vault and any extra root
│   └── tools/             # search, patterns, correlations, graph, intelligence
├── tests/                 # 70 tests over the DB, extractor and multi-root paths
├── templates/root-instructions.md  # Drop-in usage instructions for an agent
├── .github/workflows/     # tests.yml runs pytest on every PR, release.yml publishes to PyPI on a v* tag
└── run-indexer.sh         # Wrapper that activates the venv and runs an incremental pass
```

Design principles worth knowing before you read the code: one SQLite file and no other server, no vendor SDK for LLM calls (stdlib `urllib` only), incremental everything through content hashing, and per-root safety guards so an unmounted drive skips its stale sweep instead of purging your index.

## The 18 MCP Tools

| Tool | What it does |
|------|--------------|
| **Search and discovery** | |
| `root_search(query)` | Semantic search across all indexed knowledge |
| `root_search_folder(query, folder)` | Semantic search scoped to one vault folder |
| `root_note(path)` | Read the full content of a note by path |
| `root_connections(path)` | Notes that are related but live in a different folder |
| `root_themes(scope)` | Recurring themes, found by clustering similar notes |
| `root_gaps(topic)` | What is mentioned but never explored, and which domains are absent |
| `root_stats()` | Notes, chunks, sources, top folders, last indexed time |
| **Multi-source intelligence** | |
| `root_ingest(source_type, title, content, path)` | Ingest a meeting, email or message from another MCP |
| `root_ingest_batch(items)` | Ingest many items in one call |
| `root_about(person)` | Everything ROOT knows about a person across all sources |
| `root_open_loops(scope)` | Things discussed or promised but never followed up |
| `root_project_pulse(project)` | Recent activity for a project across every source |
| **Entity graph and GraphRAG** | |
| `root_graph(entity, depth)` | An entity's neighborhood up to N hops |
| `root_influence_map(project)` | Who influenced a project, through which actions |
| `root_decision_trail(topic)` | How decisions around a topic evolved over time |
| `root_blind_spots()` | Entities gone quiet: 30+ days inactive after 3+ mentions |
| `root_ask(question)` | Free-form Q&A over search, graph and synthesis |
| `root_weekly_digest()` | New entities, new relations, and activity by source |

## Indexing More Than One Folder

Most people start with one vault. Your knowledge usually is not in one place: there is the vault, and then there is the project docs folder, and the notes your coding agent writes, and the memory store some tool keeps in a dotfile directory.

Add them as roots:

```yaml
vault:
  path: "~/Documents/My Vault"

  roots:
    - name: project-docs
      path: "~/code/myproject/docs"
      extract: true
    - name: agent-memory
      path: "~/.config/agent/memory"
      extract: false
```

Each root is indexed separately and keeps its own identity. `root_stats` breaks the counts out per root, `root_search` can filter to one of them, and each root's stale sweep only ever removes its own notes. If a root goes missing, say an external drive is unmounted, that root is skipped with a warning and its notes are left alone.

`extract` is the part worth understanding, because it is where the money is.

Indexing is free. It chunks your notes and embeds them with a local MiniLM model on your CPU, so a root becomes semantically searchable at zero cost. Entity extraction is the step that calls an LLM once per note to pull out people, projects and relationships for the graph.

Those are now separate decisions. `extract: false` gives you a root you can search but never pay for. Point a large, repetitive, low-entity corpus at it and your bill does not move. In my own setup a folder of agent memory files went in that way. Every file is searchable, and the extraction queue barely moved.

One detail if you already have an index: note paths are unique, and two folders can both contain `index.md`, so each extra root namespaces its paths with a prefix (its name, by default). The main `vault.path` keeps no prefix, which means adding roots never re-embeds the vault you already indexed.

## Running Costs

Setup costs a few dollars. After that you pay for changed notes and for questions you actually ask.

| Activity | When it runs | Cost |
|----------|--------------|------|
| Indexing and embedding | Every run | $0, local model on CPU |
| Entity extraction | New and changed notes only | about $0.003 per note on Haiku |
| `root_ask` | On demand | about $0.01 per query on Sonnet |
| Scheduled incremental pass | Every 2 hours | $0.01 to $0.05 per day |
| A root with `extract: false` | Every run | $0, searchable, never sent to an LLM |

Those are ranges measured on my corpus with the Anthropic backend. Mine settles between $1 and $3 a month. Yours will land somewhere else, depending on how long your notes are and how many of them change each day. On Ollama the LLM column goes to zero and the quality drops with it.

## LLM Backends

Three backends handle entity extraction and Q&A synthesis:

| Backend | Cost | Quality | Setup |
|---------|------|---------|-------|
| **Anthropic** (default) | about $0.003 per note | Best | `ANTHROPIC_API_KEY` in `.env` |
| **OpenRouter** | Free $1 credit to start | Good | `OPENROUTER_API_KEY` in `.env` |
| **Ollama** | Free, runs locally | Lower | `ollama pull llama3.1` |

Set it in `config.yaml`:

```yaml
llm:
  backend: "anthropic"  # or "openrouter" or "ollama"
```

## Keeping the index fresh

Run `root-index --extract` on a schedule and ROOT stays current. Only changed notes are reprocessed, so a typical incremental run finishes in well under a minute.

Cron and launchd both need the absolute path to `root-index`, which `which root-index` prints. Cron is the simpler option on macOS. A launchd agent cannot read `~/Documents` or iCloud paths without a Full Disk Access grant, and cron sidesteps that:

```bash
crontab -e

# Runs at :30, every two hours during the day
30 8,10,12,14,16,18,20,22 * * * ANTHROPIC_API_KEY=your-key /path/to/root-index --extract >> ~/Library/Logs/root-indexer.log 2>&1
```

If your vault sits outside those protected folders, launchd works and survives reboots. Save this as `~/Library/LaunchAgents/com.root-kg.refresh.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.root-kg.refresh</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/root-index</string>
    <string>--extract</string>
  </array>
  <key>StartInterval</key><integer>7200</integer>
</dict>
</plist>
```

Load it with `launchctl load ~/Library/LaunchAgents/com.root-kg.refresh.plist`. On Linux the equivalent is a systemd timer calling the same command, and a contribution there is welcome. One scheduling tip: if a meeting-notes tool syncs into your vault, offset ROOT by half an hour so the fresh notes are on disk before ROOT reads them.

## Comparison

| Feature | ROOT | Obsidian Graph | Mem.ai | Khoj | Rewind |
|---------|------|----------------|--------|------|--------|
| Entity extraction | LLM-powered | None | None | None | None |
| Typed relations | Yes, 10 types | Backlinks only | No | No | No |
| GraphRAG | Yes | No | Basic RAG | Basic RAG | No |
| Multi-source | Notes, meetings, email | Notes only | Yes | Notes only | Everything |
| MCP native | Yes | No | No | No | No |
| Self-hosted | Yes | Yes | No | Yes | No |
| Single file DB | Yes | N/A | Cloud | Postgres | Cloud |
| Free embeddings | Yes, local | N/A | No | Yes | No |

The ROOT column is checked against this repo. The other columns come from each product's public description, not from code I have read.

## Requirements

- Python 3.11 or newer
- Disk space for the local embedding model and its dependencies, downloaded on first run
- One of: an Anthropic API key, an OpenRouter key (free $1 credit), or Ollama running locally
- Four runtime dependencies: `sentence-transformers`, `sqlite-vec`, `mcp`, `pyyaml`. No Postgres, no Neo4j, no Docker

## Development

Clone, install in editable mode with the dev extra, and run the suite from the repo root:

```bash
git clone https://github.com/mshadmanrahman/root-kg.git
cd root-kg
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

70 tests pass, covering the database layer, the extraction pipeline, the multi-root indexing paths and where config lives. In a checkout, `root-kg init` writes `config.yaml`, `data/` and `logs/` next to `pyproject.toml` rather than to `~/.root-kg`.

Releases go out from a tag: bump the version in `pyproject.toml` and `root_kg/__init__.py`, merge, then `git tag vX.Y.Z && git push origin vX.Y.Z`. The `release` workflow builds the sdist and wheel, checks the tag against the version, and publishes through PyPI trusted publishing.

## What went wrong and what I learned

`clear_extraction_for_note()` had a docstring saying it removed all entities and relations sourced from a note. It deleted relations, note links, and the extraction record. It never deleted a single entity row.

Extraction runs through an LLM, so it is nondeterministic. Re-index a note and the fresh pass returns a slightly different entity set. Everything the old pass found and the new one missed stayed in the database forever, holding no relation and no note link. A few per note, every run, compounding.

When I finally counted, 11,172 of 21,696 entities were unreachable. That is 51% of the graph. Search could not return them, traversal could not reach them, and `root_stats()` counted every one as real. That last part is why it took months to notice. The graph was reporting roughly double its true size and I believed it.

The obvious fix was wrong. I swept entities holding a note link to the cleared note, and a three-note re-extraction still leaked two orphans. `_extract_note()` resolves a relation's endpoints through `resolve_entity()`, which matches an entity that already exists elsewhere and does not link it to the current note. That entity is held up by the relation alone. Both sets have to go: entities linked to the note, and both endpoints of every relation sourced from it.

Fixing that exposed a second bug that had been unreachable until then. `entity_aliases` declares `ON DELETE CASCADE`, but SQLite ignores foreign keys unless the connection sets `PRAGMA foreign_keys = ON`, and this code never did. Deleted entities left their alias rows behind. `alias` is `UNIQUE`, so the dead row squats the name: `add_alias()` for a new entity hits `INSERT OR IGNORE`, does nothing, and that alias resolves to `None` permanently. It could not happen before, because nothing was ever deleted. The fix created the conditions for it.

Last one, and it is the least technical. I fixed this in the copy I run on 2026-08-07 and did not push it here until 2026-08-10. Anyone who cloned in between got the orphan factory. A fix that only exists in the copy you run is not a fix.

## Contributing

PRs welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first. The codebase stays deliberately plain: Python 3.11+, no frameworks, small files.

Areas where help goes furthest:

- **Adapters**: LogSeq, Notion, Apple Notes, Google Docs
- **Backends**: Google Gemini, local models via llama.cpp
- **Visualization**: a web UI for exploring the entity graph
- **Platforms**: a systemd timer for Linux, Task Scheduler for Windows

## See also

Other things I have built for the same workflow:

- [pm-pilot](https://github.com/mshadmanrahman/pm-pilot): Claude Code configured for PMs. Meeting prep, PRDs, market sizing, 25 skills.
- [morning-digest](https://github.com/mshadmanrahman/morning-digest): morning briefing automation. Calendar, email and news in one digest.
- [discovery-md](https://github.com/mshadmanrahman/discovery-md): AI product discovery for PMs.
- [ceremonies](https://github.com/mshadmanrahman/ceremonies): agile ceremonies that do not waste the hour.
- [claudecode-guide](https://github.com/mshadmanrahman/claudecode-guide): a friendly guide to Claude Code, also at [claudecodeguide.dev](https://claudecodeguide.dev).
- [riff](https://github.com/mshadmanrahman/riff): LinkedIn engagement assistant. Extracts posts and comments for AI-drafted replies.

## License

MIT. See [LICENSE](LICENSE).

## Support

Questions and bugs go to [GitHub Issues](https://github.com/mshadmanrahman/root-kg/issues); ideas and setups go to [Discussions](https://github.com/mshadmanrahman/root-kg/discussions).

<p align="center">
  Built by <a href="https://github.com/mshadmanrahman">Shadman Rahman</a>
</p>
