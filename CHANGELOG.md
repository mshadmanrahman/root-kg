# Changelog

All notable changes to ROOT are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.2.1] - 2026-09-11

### Fixed

- **`pip install -e .` works on a fresh clone.** setuptools refused the flat layout ("multiple top-level packages discovered"). `pyproject.toml` now lists the modules and packages explicitly, and `root-server` points at a synchronous entry point so the console script actually starts the server.
- **Quick Start matched the code.** The README told you to run `python -m root init`, which never existed. The documented commands are now `root-kg init`, `root-index` and `root-server`, the console scripts `pip install` creates.

### Changed

- README rewritten install-first, with a drawn architecture diagram and a letterpress hero in place of the generated images.
- `pytest` is a `dev` extra: `pip install -e ".[dev]"`.

### Removed

- Owner-specific scripts that only ran inside the maintainer's private workspace (Slack alerting, a workspace health check, a skill-usage audit), a personal launchd plist, and an installer manifest for an unrelated tool.

### Added

- `CONTRIBUTING.md`, issue templates, and a GitHub Actions workflow that runs the test suite on Python 3.11 and 3.12.

## [1.2.0] — 2026-09-07

### Added

- **Multiple index roots.** `vault.roots` in `config.yaml` takes a list of folders alongside the main `vault.path`. Each root gets its own `source_type`, so `root_stats` reports per-root counts, `root_search` can filter to one root, and each root's stale sweep only removes its own notes. A scalar `vault.path` keeps working unchanged, and extra roots namespace their note paths with a prefix (their name, by default) so an existing vault is never orphaned or re-embedded.

- **`extract` flag per root, separating indexing cost from extraction cost.** Indexing embeds notes locally with MiniLM and is free; entity extraction calls an LLM once per note. Those were one decision and are now two. A root with `extract: false` is fully searchable and never reaches the LLM, so a large low-entity corpus costs nothing to make searchable. Measured on the author's setup: adding 939 agent memory files with `extract: false` left the extraction queue at 27 notes rather than 966.

### Changed

- **`RootDB.remove_stale_notes(valid_paths, source_type="vault")`** takes the source as a parameter. It previously hardcoded `source_type = 'vault'`, which meant a second root could not sweep its own notes.

- **`RootDB.get_notes_needing_extraction(source_types=None)`** filters by source. `None` keeps the old behaviour of every source.

- **`extract_all(..., source_types=None)`** passes that filter through from the indexer.

### Fixed

- **A missing folder can no longer purge an index.** The zero-scan safety guard and the thin-note circuit breaker were evaluated once against the totals for the whole run, so with several roots configured, one unreachable folder scanning zero notes would pass a global check that other roots had satisfied, and its notes would be swept as stale. Both guards now run per root, and an unreadable root is skipped with a warning instead of taking down the run.

---

## [1.1.0] — 2026-06-02

### Added

- **`rootd.py`** — Warm ROOT daemon. Keeps the database and embedder loaded in memory so searches return instantly without cold-start latency. Exposes a minimal localhost HTTP API (`GET /health`, `GET /search`) for fast agent-to-agent queries, with zero LLM calls on the hot path.

- **`query.py`** — Agent-callable CLI for the full ROOT tool surface. Lets any external process (shell scripts, other AI agents, cron jobs) invoke ROOT tools via `python query.py <tool> [args]` without standing up the MCP server. Supports: `search`, `open_loops`, `themes`, `blind_spots`, `weekly_digest`, `decision_trail`, `project_pulse`.

- **`health-check.py`** — Operational health check for ROOT deployments. Validates that the indexer is running, the database is not stale, background crons are firing, and the MCP server is reachable. Designed to run as a daily cron job and catch silent failures before they go unnoticed.

- **`skill-audit.py`** — Audits which ROOT MCP tools are actually being invoked. Surfaces underused tools and identifies gaps in how your knowledge graph is being queried, so you can tune extraction and indexing priorities.

- **`slack-alert.py`** — Sends ROOT health and digest alerts to a Slack webhook. Pairs with `health-check.py` for teams or individuals who want operational visibility without polling logs manually.

- **`run-indexer.sh`** — Convenience shell wrapper for the indexer. Activates the venv, runs incremental indexing with entity extraction, and logs output in a format compatible with launchd and cron. Useful as a drop-in replacement for the raw `python indexer.py` invocation in launchd plists.

### Changed

- **`llm.py`** — Multi-backend LLM client rebuilt. Anthropic and OpenRouter backends are now first-class and feature-equivalent. Extraction and synthesis are independently configurable per backend. Nested Claude environment variables are stripped before subprocess calls to prevent token leakage in agentic setups. OpenRouter's OpenAI-compatible tool schema is fully supported.

- **`indexer.py`** — Added an LLM pause guard: if a `.llm-paused` sentinel file exists in the project root (optionally containing a `reset_epoch` timestamp), entity extraction is skipped gracefully without killing the indexing run. Useful for rate-limit management and cost control in automated deployments.

- **`embeddings.py`** — Embedder initialization hardened; model loading errors now surface earlier with a clearer message rather than failing silently during the first batch.

- **`server.py`** — MCP server stability improvements. Tool handler errors are now caught at the transport layer and returned as structured error responses rather than crashing the server process.

- **`db.py`** — Minor schema query optimizations and additional defensive checks on write paths.

---

## [1.0.0] — 2026-03-21

Initial release. Personal knowledge graph with entity extraction, GraphRAG traversal, semantic search, and MCP integration for Claude Code.
