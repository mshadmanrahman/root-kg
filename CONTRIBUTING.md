# Contributing to ROOT

Thanks for looking under the hood. ROOT is a small project with one maintainer, so the rules are short.

## Set up a dev environment

```bash
git clone https://github.com/mshadmanrahman/root-kg.git
cd root-kg
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The test suite runs against a temporary SQLite database and mocks every LLM call, so it needs no API key. The first run downloads the local embedding model once.

## Before you open a pull request

- Open an issue first if the change is bigger than a bug fix, so we agree on the shape before you write it.
- Keep one change per pull request.
- Run `pytest` and make sure it passes.
- Add a test when you fix a bug or add a tool. `tests/test_db.py` shows the pattern.
- Add a line to `CHANGELOG.md` under an `Unreleased` heading.
- Match the style of the file you are in. There is no formatter configured; plain readable Python wins.

## Adding an MCP tool

Tools live in `tools/` grouped by kind (search, graph, patterns, correlations, intelligence). Register the tool in `server.py` in both `list_tools` and `call_tool`, and update the tool count in `README.md` if it changes.

## Adding a source adapter

`adapters/vault.py` walks a folder of markdown files. A new adapter should produce the same note shape (path, title, body, frontmatter, modified time) so the indexer, chunker and extractor stay untouched.

## Reporting a bug

Use the bug report template. Include your Python version, the LLM backend you configured, and the output of `root-kg stats`.

## Code of conduct

Be kind, assume good faith, and keep disagreements about the code.
