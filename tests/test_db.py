"""Tests for ROOT database layer, focusing on entity graph methods."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import RootDB


@pytest.fixture
def db():
    """Create a temporary in-memory-like database for testing."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        database = RootDB(db_path)
        yield database
        database.close()


@pytest.fixture
def db_with_notes(db):
    """Database pre-populated with sample notes."""
    db.upsert_note(
        path="meetings/ric-1on1.md",
        title="1-1 with Ric",
        content="Discussed pricing model and German market strategy.",
        content_hash="hash1",
        folder="Meetings",
        source_type="vault",
        created_at="2026-03-01T10:00:00Z",
        indexed_at="2026-03-21T00:00:00Z",
    )
    db.upsert_note(
        path="projects/heimdall.md",
        title="Heimdall Ad Server",
        content="Heimdall replaces Kevel. Sebastian architected the API.",
        content_hash="hash2",
        folder="Projects",
        source_type="vault",
        created_at="2026-02-15T10:00:00Z",
        indexed_at="2026-03-21T00:00:00Z",
    )
    return db


class TestUpsertEntity:
    def test_creates_entity(self, db):
        eid = db.upsert_entity("Ric", "person")
        assert eid > 0

    def test_increments_mention_count(self, db):
        db.upsert_entity("Ric", "person")
        db.upsert_entity("Ric", "person")
        entities = db.search_entities("Ric")
        assert entities[0]["mention_count"] == 2

    def test_different_types_are_separate(self, db):
        id1 = db.upsert_entity("Lead Scoring", "project")
        id2 = db.upsert_entity("Lead Scoring", "event")
        assert id1 != id2


class TestAliases:
    def test_add_and_resolve_alias(self, db):
        eid = db.upsert_entity("Fredrik", "person")
        db.add_alias(eid, "Frederick")
        resolved = db.resolve_entity("Frederick")
        assert resolved == eid

    def test_resolve_by_name(self, db):
        eid = db.upsert_entity("Ric", "person")
        resolved = db.resolve_entity("Ric")
        assert resolved == eid

    def test_resolve_case_insensitive(self, db):
        eid = db.upsert_entity("Ric", "person")
        assert db.resolve_entity("ric") == eid
        assert db.resolve_entity("RIC") == eid

    def test_resolve_unknown_returns_none(self, db):
        assert db.resolve_entity("Nobody") is None

    def test_duplicate_alias_is_idempotent(self, db):
        eid = db.upsert_entity("Ric", "person")
        db.add_alias(eid, "Rick")
        db.add_alias(eid, "Rick")  # Should not raise


class TestRelations:
    def test_create_relation(self, db_with_notes):
        ric = db_with_notes.upsert_entity("Ric", "person")
        heimdall = db_with_notes.upsert_entity("Heimdall", "project")
        note = db_with_notes.conn.execute("SELECT id FROM notes LIMIT 1").fetchone()

        rid = db_with_notes.upsert_relation(
            ric, "discussed", heimdall, note["id"], 0.9, "Ric discussed Heimdall"
        )
        assert rid > 0

    def test_get_entity_relations(self, db_with_notes):
        ric = db_with_notes.upsert_entity("Ric", "person")
        heimdall = db_with_notes.upsert_entity("Heimdall", "project")
        note = db_with_notes.conn.execute("SELECT id FROM notes LIMIT 1").fetchone()

        db_with_notes.upsert_relation(ric, "owns", heimdall, note["id"], 0.95, "Ric owns Heimdall")
        rels = db_with_notes.get_entity_relations(ric)

        assert len(rels) == 1
        assert rels[0]["relation_type"] == "owns"
        assert rels[0]["entity_b_name"] == "Heimdall"


class TestGraphTraversal:
    def test_neighborhood_depth_0(self, db_with_notes):
        ric = db_with_notes.upsert_entity("Ric", "person")
        neighbors = db_with_notes.get_entity_neighborhood(ric, depth=0)
        assert len(neighbors) == 1
        assert neighbors[0]["name"] == "Ric"

    def test_neighborhood_depth_1(self, db_with_notes):
        ric = db_with_notes.upsert_entity("Ric", "person")
        heimdall = db_with_notes.upsert_entity("Heimdall", "project")
        note = db_with_notes.conn.execute("SELECT id FROM notes LIMIT 1").fetchone()
        db_with_notes.upsert_relation(ric, "owns", heimdall, note["id"])

        neighbors = db_with_notes.get_entity_neighborhood(ric, depth=1)
        names = {n["name"] for n in neighbors}
        assert "Ric" in names
        assert "Heimdall" in names

    def test_neighborhood_depth_2(self, db_with_notes):
        ric = db_with_notes.upsert_entity("Ric", "person")
        heimdall = db_with_notes.upsert_entity("Heimdall", "project")
        seb = db_with_notes.upsert_entity("Sebastian", "person")
        note = db_with_notes.conn.execute("SELECT id FROM notes LIMIT 1").fetchone()

        db_with_notes.upsert_relation(ric, "owns", heimdall, note["id"])
        db_with_notes.upsert_relation(seb, "created", heimdall, note["id"])

        neighbors = db_with_notes.get_entity_neighborhood(ric, depth=2)
        names = {n["name"] for n in neighbors}
        assert "Sebastian" in names  # Reachable through Heimdall

    def test_no_cycles(self, db_with_notes):
        a = db_with_notes.upsert_entity("A", "concept")
        b = db_with_notes.upsert_entity("B", "concept")
        note = db_with_notes.conn.execute("SELECT id FROM notes LIMIT 1").fetchone()

        db_with_notes.upsert_relation(a, "depends_on", b, note["id"])
        db_with_notes.upsert_relation(b, "depends_on", a, note["id"])

        # Should not infinite loop
        neighbors = db_with_notes.get_entity_neighborhood(a, depth=3)
        assert len(neighbors) == 2


class TestExtractionTracking:
    def test_notes_needing_extraction(self, db_with_notes):
        notes = db_with_notes.get_notes_needing_extraction()
        assert len(notes) == 2  # Both notes need extraction

    def test_mark_extracted_skips_next_time(self, db_with_notes):
        notes = db_with_notes.get_notes_needing_extraction()
        db_with_notes.mark_extracted(notes[0]["id"], notes[0]["content_hash"], "test-model")

        remaining = db_with_notes.get_notes_needing_extraction()
        assert len(remaining) == 1

    def test_changed_content_triggers_reextraction(self, db_with_notes):
        notes = db_with_notes.get_notes_needing_extraction()
        note = notes[0]
        db_with_notes.mark_extracted(note["id"], note["content_hash"], "test-model")

        # Update the note content (changes hash)
        db_with_notes.upsert_note(
            path=note["path"],
            title=note["title"],
            content="Updated content here",
            content_hash="new_hash",
            folder="Meetings",
            indexed_at="2026-03-21T01:00:00Z",
        )

        remaining = db_with_notes.get_notes_needing_extraction()
        paths = {n["path"] for n in remaining}
        assert note["path"] in paths


class TestMergeEntities:
    def test_merge_reassigns_relations(self, db_with_notes):
        seb = db_with_notes.upsert_entity("Sebastian", "person")
        seb_full = db_with_notes.upsert_entity("Sebastian Wallmark", "person")
        heimdall = db_with_notes.upsert_entity("Heimdall", "project")
        note = db_with_notes.conn.execute("SELECT id FROM notes LIMIT 1").fetchone()

        db_with_notes.upsert_relation(seb_full, "created", heimdall, note["id"])
        db_with_notes.merge_entities(keep_id=seb, merge_id=seb_full)

        # Relation should now point to seb
        rels = db_with_notes.get_entity_relations(seb)
        assert len(rels) == 1
        assert rels[0]["entity_a_name"] == "Sebastian"

    def test_merge_adds_alias(self, db_with_notes):
        seb = db_with_notes.upsert_entity("Sebastian", "person")
        seb_full = db_with_notes.upsert_entity("Sebastian Wallmark", "person")

        db_with_notes.merge_entities(keep_id=seb, merge_id=seb_full)

        # "Sebastian Wallmark" should now resolve to seb
        resolved = db_with_notes.resolve_entity("Sebastian Wallmark")
        assert resolved == seb


class TestEntityStats:
    def test_empty_stats(self, db):
        stats = db.get_entity_stats()
        assert stats["total_entities"] == 0
        assert stats["total_relations"] == 0

    def test_populated_stats(self, db_with_notes):
        db_with_notes.upsert_entity("Ric", "person")
        db_with_notes.upsert_entity("Heimdall", "project")
        stats = db_with_notes.get_entity_stats()
        assert stats["total_entities"] == 2
        assert stats["by_entity_type"]["person"] == 1
        assert stats["by_entity_type"]["project"] == 1
