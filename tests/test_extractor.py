"""Tests for ROOT extraction pipeline."""

import logging
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import RootDB
from extractor import extract_all, _extract_note


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        database = RootDB(db_path)
        # Add a test note
        database.upsert_note(
            path="test/note1.md",
            title="Meeting with Ric",
            content="Ric discussed the Heimdall project timeline.",
            content_hash="abc123",
            folder="Meetings",
            source_type="vault",
            indexed_at="2026-03-21T00:00:00Z",
        )
        yield database
        database.close()


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.extraction_model = "test-model"
    llm.extract_entities.return_value = {
        "entities": [
            {"name": "Ric", "type": "person", "aliases": ["Rick"]},
            {"name": "Heimdall", "type": "project", "aliases": []},
        ],
        "relations": [
            {
                "from_entity": "Ric",
                "relation": "discussed",
                "to_entity": "Heimdall",
                "confidence": 0.95,
                "context": "Ric discussed the Heimdall project timeline",
            },
        ],
    }
    return llm


@pytest.fixture
def logger():
    return logging.getLogger("test")


class TestExtractNote:
    def test_extracts_entities(self, db, mock_llm, logger):
        notes = db.get_notes_needing_extraction()
        result = _extract_note(db, mock_llm, notes[0], logger)

        assert result["entity_count"] == 2
        assert result["relation_count"] == 1

    def test_stores_entities_in_db(self, db, mock_llm, logger):
        notes = db.get_notes_needing_extraction()
        _extract_note(db, mock_llm, notes[0], logger)

        entities = db.search_entities("Ric")
        assert len(entities) == 1
        assert entities[0]["entity_type"] == "person"

    def test_stores_aliases(self, db, mock_llm, logger):
        notes = db.get_notes_needing_extraction()
        _extract_note(db, mock_llm, notes[0], logger)

        resolved = db.resolve_entity("Rick")
        assert resolved is not None

    def test_stores_relations(self, db, mock_llm, logger):
        notes = db.get_notes_needing_extraction()
        _extract_note(db, mock_llm, notes[0], logger)

        ric_id = db.resolve_entity("Ric")
        rels = db.get_entity_relations(ric_id)
        assert len(rels) == 1
        assert rels[0]["relation_type"] == "discussed"

    def test_marks_as_extracted(self, db, mock_llm, logger):
        notes = db.get_notes_needing_extraction()
        _extract_note(db, mock_llm, notes[0], logger)

        remaining = db.get_notes_needing_extraction()
        assert len(remaining) == 0

    def test_links_entities_to_notes(self, db, mock_llm, logger):
        notes = db.get_notes_needing_extraction()
        _extract_note(db, mock_llm, notes[0], logger)

        ric_id = db.resolve_entity("Ric")
        linked_notes = db.get_notes_for_entity(ric_id)
        assert len(linked_notes) == 1
        assert linked_notes[0]["title"] == "Meeting with Ric"


class TestExtractAll:
    def test_processes_all_notes(self, db, mock_llm, logger):
        stats = extract_all(db, mock_llm, logger)
        assert stats.processed == 1
        assert stats.errors == 0

    def test_respects_limit(self, db, mock_llm, logger):
        # Add more notes
        for i in range(5):
            db.upsert_note(
                path=f"test/note{i + 2}.md",
                title=f"Note {i + 2}",
                content=f"Content {i + 2}",
                content_hash=f"hash{i + 2}",
                folder="Test",
                indexed_at="2026-03-21T00:00:00Z",
            )

        stats = extract_all(db, mock_llm, logger, limit=3)
        assert stats.processed == 3

    def test_handles_llm_errors(self, db, logger):
        failing_llm = MagicMock()
        failing_llm.extraction_model = "test-model"
        failing_llm.extract_entities.side_effect = RuntimeError("API error")

        stats = extract_all(db, failing_llm, logger)
        assert stats.errors == 1
        assert stats.processed == 0

    def test_skips_empty_entities(self, db, logger):
        empty_llm = MagicMock()
        empty_llm.extraction_model = "test-model"
        empty_llm.extract_entities.return_value = {
            "entities": [{"name": "", "type": "person"}],
            "relations": [],
        }

        stats = extract_all(db, empty_llm, logger)
        assert stats.processed == 1
        # Empty name entity should be skipped
        all_entities = db.search_entities("")
        assert len(all_entities) == 0

    def test_idempotent_reextraction(self, db, mock_llm, logger):
        # Extract once
        extract_all(db, mock_llm, logger)
        entities_after_first = db.get_entity_stats()["total_entities"]

        # Change the note to force re-extraction
        db.upsert_note(
            path="test/note1.md",
            title="Meeting with Ric",
            content="Updated: Ric discussed Heimdall and pricing.",
            content_hash="new_hash",
            folder="Meetings",
            indexed_at="2026-03-21T01:00:00Z",
        )

        # Extract again
        extract_all(db, mock_llm, logger)
        entities_after_second = db.get_entity_stats()["total_entities"]

        # Should not duplicate entities
        assert entities_after_second == entities_after_first
