"""Tests for multi-root indexing and the extraction cost gate."""

import tempfile
from pathlib import Path

import pytest

from root_kg.db import RootDB
from root_kg.indexer import configured_roots, extraction_source_types


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        database = RootDB(Path(tmp) / "test.db")
        yield database
        database.close()


def _note(db, path, source_type):
    return db.upsert_note(
        path=path,
        title=path,
        content="Some real prose about the pricing model and the German market.",
        content_hash=f"hash-{path}",
        folder="(root)",
        source_type=source_type,
        created_at="2026-03-01T10:00:00Z",
        indexed_at="2026-03-21T00:00:00Z",
    )


class TestConfiguredRoots:
    def test_scalar_path_stays_one_unprefixed_root(self):
        roots = configured_roots({"vault": {"path": "~/Vault"}})
        assert len(roots) == 1
        assert roots[0]["source_type"] == "vault"
        assert roots[0]["prefix"] == ""
        assert roots[0]["extract"] is True
        assert "~" not in roots[0]["path"]

    def test_extra_roots_default_to_no_extraction(self):
        roots = configured_roots({"vault": {
            "path": "~/Vault",
            "roots": [{"name": "docs", "path": "/tmp/docs"}],
        }})
        docs = next(r for r in roots if r["source_type"] == "docs")
        assert docs["extract"] is False
        assert docs["prefix"] == "docs"

    def test_roots_only_config_needs_no_scalar_path(self):
        roots = configured_roots({"vault": {"roots": [{"name": "docs", "path": "/tmp/docs"}]}})
        assert [r["source_type"] for r in roots] == ["docs"]

    def test_explicit_prefix_and_source_type_win(self):
        roots = configured_roots({"vault": {"roots": [
            {"name": "docs", "path": "/tmp/docs", "prefix": "d", "source_type": "project-docs"},
        ]}})
        assert roots[0]["source_type"] == "project-docs"
        assert roots[0]["prefix"] == "d"

    def test_extraction_sources_exclude_opted_out_roots(self):
        config = {"vault": {"path": "~/Vault", "roots": [
            {"name": "docs", "path": "/tmp/docs", "extract": True},
            {"name": "memory", "path": "/tmp/memory", "extract": False},
        ]}}
        assert extraction_source_types(config) == ["vault", "docs"]


class TestPerSourceStaleSweep:
    def test_sweep_only_touches_its_own_source(self, db):
        _note(db, "a.md", "vault")
        _note(db, "docs/b.md", "docs")

        removed = db.remove_stale_notes(set(), source_type="docs")

        assert removed == 1
        assert db.count_notes_by_source("docs") == 0
        assert db.count_notes_by_source("vault") == 1

    def test_default_source_type_is_vault(self, db):
        _note(db, "a.md", "vault")
        _note(db, "docs/b.md", "docs")

        db.remove_stale_notes(set())

        assert db.count_notes_by_source("vault") == 0
        assert db.count_notes_by_source("docs") == 1

    def test_paths_still_in_the_root_survive(self, db):
        _note(db, "docs/keep.md", "docs")
        _note(db, "docs/drop.md", "docs")

        removed = db.remove_stale_notes({"docs/keep.md"}, source_type="docs")

        assert removed == 1
        assert db.count_notes_by_source("docs") == 1


class TestExtractionGate:
    def test_unscoped_returns_every_source(self, db):
        _note(db, "a.md", "vault")
        _note(db, "memory/b.md", "memory")

        assert len(db.get_notes_needing_extraction()) == 2

    def test_scoped_excludes_other_sources(self, db):
        _note(db, "a.md", "vault")
        _note(db, "memory/b.md", "memory")

        notes = db.get_notes_needing_extraction(source_types=["vault"])

        assert [n["path"] for n in notes] == ["a.md"]

    def test_empty_scope_is_treated_as_unscoped(self, db):
        _note(db, "a.md", "vault")

        assert len(db.get_notes_needing_extraction(source_types=[])) == 1
