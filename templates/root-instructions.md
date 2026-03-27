## ROOT Knowledge Graph

ROOT is a personal knowledge graph MCP server. Use ROOT tools for knowledge questions that require synthesis across multiple notes.

### Available MCP Tools

- `root_ask(question)` - Free-form Q&A (semantic search + entity graph + LLM synthesis)
- `root_search(query)` - Semantic search across all indexed notes
- `root_graph(entity, depth)` - Entity neighborhood traversal
- `root_influence_map(project)` - Who touched a project and how
- `root_decision_trail(topic)` - How decisions evolved over time

### When to Use ROOT vs File Reads

- **ROOT**: "Who influences this project?", "How did the pricing decision evolve?", "What are the knowledge gaps?"
- **File reads**: When you know the exact file path and need the full content

### Setup

Vault path: `__VAULT_PATH__`

To re-index: `cd <root-kg-dir> && source .venv/bin/activate && python indexer.py --extract`
