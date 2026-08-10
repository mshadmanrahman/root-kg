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


class TestResolveDeterminism:
    """Split-name resolution is deterministic.

    Several entities can share a case-insensitive name: UNIQUE(name, entity_type)
    is case-sensitive on writes, so "Acme"/"acme" coexist, while reads are
    case-insensitive. resolve_entity must pick the most-connected shard (and
    prefer an exact entity_type when given) instead of returning whichever row
    the scan happens to hit first.
    """

    @staticmethod
    def _add_relations(db, entity_id, note_id, n):
        """Attach n relations to entity_id via n filler partner entities."""
        for i in range(n):
            partner = db.upsert_entity(f"Filler{entity_id}_{i}", "concept")
            db.upsert_relation(entity_id, "discussed", partner, note_id)

    def test_prefers_most_connected_shard(self, db_with_notes):
        db = db_with_notes
        note = db.conn.execute("SELECT id FROM notes LIMIT 1").fetchone()["id"]
        db.upsert_entity("Acme", "project")        # near-empty orphan shard
        big = db.upsert_entity("acme", "project")  # the real cluster
        self._add_relations(db, big, note, 5)
        # queried in any casing, the connected shard wins
        assert db.resolve_entity("acme") == big
        assert db.resolve_entity("ACME") == big
        assert db.resolve_entity("Acme") == big

    def test_type_aware_prefers_exact_type(self, db_with_notes):
        db = db_with_notes
        note = db.conn.execute("SELECT id FROM notes LIMIT 1").fetchone()["id"]
        proj = db.upsert_entity("Globex", "project")
        org = db.upsert_entity("Globex", "organization")
        # project is MORE connected, so without the hint it would win
        self._add_relations(db, proj, note, 4)
        self._add_relations(db, org, note, 1)
        assert db.resolve_entity("Globex", entity_type="organization") == org
        assert db.resolve_entity("Globex", entity_type="project") == proj
        # untyped falls back to the most-connected shard
        assert db.resolve_entity("Globex") == proj

    def test_untyped_resolution_is_stable(self, db_with_notes):
        db = db_with_notes
        note = db.conn.execute("SELECT id FROM notes LIMIT 1").fetchone()["id"]
        db.upsert_entity("Initech Co", "project")
        b = db.upsert_entity("Initech co", "project")
        self._add_relations(db, b, note, 3)
        first = db.resolve_entity("initech co")
        assert first == b
        assert db.resolve_entity("initech co") == first  # repeatable

    def test_alias_path_still_resolves(self, db_with_notes):
        # Regression guard for the alias-query JOIN rewrite.
        db = db_with_notes
        note = db.conn.execute("SELECT id FROM notes LIMIT 1").fetchone()["id"]
        strong = db.upsert_entity("Hooli", "project")
        self._add_relations(db, strong, note, 3)
        db.add_alias(strong, "HL")
        assert db.resolve_entity("HL") == strong
        assert db.resolve_entity("hl") == strong  # case-insensitive alias


class TestResolveTypeFilter:
    # Regression, 2026-08-02. entity_type was only an ORDER BY preference inside
    # each of two sequential queries (name, then alias), and the alias query ran
    # only when the name query found nothing. So a wrong-type NAME match beat a
    # right-type ALIAS match. Measured live: resolve_entity("Robin", "person")
    # returned a project named "Robin" (0 rels) instead of the person Robin
    # Fielding (306 rels) holding "Robin" as an alias.
    def test_right_type_alias_beats_wrong_type_name(self, db):
        proj = db.upsert_entity("Robin", "project")
        person = db.upsert_entity("Robin Fielding", "person")
        db.add_alias(person, "Robin")
        assert db.resolve_entity("Robin", "person") == person
        assert db.resolve_entity("Robin", "project") == proj

    def test_falls_back_to_any_type_when_no_type_match(self, db):
        # the permissive old behaviour must survive: if nothing of the requested
        # type exists, an off-type match is still better than None
        proj = db.upsert_entity("Atlas", "project")
        assert db.resolve_entity("Atlas", "person") == proj

    def test_untyped_lookup_still_prefers_most_connected(self, db):
        a = db.upsert_entity("Acme", "organization")
        b = db.upsert_entity("ACME", "project")
        other = db.upsert_entity("Someone", "person")
        n = db.upsert_note(path="n.md", title="n", content="c", content_hash="h",
                           folder="f", indexed_at="2026-01-01T00:00:00Z")
        db.upsert_relation(entity_a_id=b, relation_type="owns", entity_b_id=other,
                           source_note_id=n, confidence=0.9, context="x")
        assert db.resolve_entity("Acme") == b   # b has the relation
        assert a != b

    def test_right_type_name_still_wins_over_right_type_alias(self, db):
        exact = db.upsert_entity("Sam", "person")
        other = db.upsert_entity("Sam Whitfield", "person")
        db.add_alias(other, "Sammy")
        assert db.resolve_entity("Sam", "person") == exact


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

    def test_merge_shared_note_link_no_pk_crash(self, db_with_notes):
        # Both entities linked to the SAME note: the naive UPDATE hit the
        # (entity_id, note_id) PK. Safe merge must collapse, not raise.
        keep = db_with_notes.upsert_entity("Sebastian", "person")
        loser = db_with_notes.upsert_entity("Sebastian Wallmark", "person")
        note = db_with_notes.conn.execute("SELECT id FROM notes LIMIT 1").fetchone()["id"]
        db_with_notes.link_entity_to_note(keep, note)
        db_with_notes.link_entity_to_note(loser, note)
        db_with_notes.merge_entities(keep_id=keep, merge_id=loser)
        links = db_with_notes.conn.execute(
            "SELECT COUNT(*) FROM entity_note_links WHERE entity_id=?", (keep,)).fetchone()[0]
        assert links == 1
        assert db_with_notes.conn.execute(
            "SELECT COUNT(*) FROM entity_note_links WHERE entity_id=?", (loser,)).fetchone()[0] == 0

    def test_merge_shared_alias_no_unique_crash(self, db_with_notes):
        # Both entities own the SAME alias: the naive UPDATE hit UNIQUE(alias).
        keep = db_with_notes.upsert_entity("Sebastian", "person")
        loser = db_with_notes.upsert_entity("Sebastian Wallmark", "person")
        db_with_notes.add_alias(keep, "Sebbe")
        db_with_notes.add_alias(loser, "Sebbe")  # add_alias swallows the dup
        db_with_notes.merge_entities(keep_id=keep, merge_id=loser)
        assert db_with_notes.resolve_entity("Sebbe") == keep
        assert db_with_notes.conn.execute(
            "SELECT COUNT(*) FROM entities WHERE id=?", (loser,)).fetchone()[0] == 0

    def test_merge_transfers_unique_aliases(self, db_with_notes):
        # Regression: alias is globally UNIQUE, so INSERT OR IGNORE-ing the loser's
        # rows under keep_id collided with the loser's OWN row every time; the
        # follow-up DELETE then destroyed the alias. Measured on the live graph:
        # 0 of 25 aliases survived a 9-merge sweep. The shared-alias test above
        # passed throughout, because collapsing a duplicate was the one path that
        # worked -- a UNIQUE alias on the loser was never covered.
        keep = db_with_notes.upsert_entity("Sebastian", "person")
        loser = db_with_notes.upsert_entity("Sebastian Wallmark", "person")
        db_with_notes.add_alias(loser, "Sebbe")
        db_with_notes.add_alias(loser, "sebastian.wallmark@example.com")
        db_with_notes.merge_entities(keep_id=keep, merge_id=loser)
        surviving = {
            r["alias"] for r in db_with_notes.conn.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id=?", (keep,))
        }
        assert "Sebbe" in surviving
        assert "sebastian.wallmark@example.com" in surviving
        assert db_with_notes.resolve_entity("Sebbe") == keep
        # and nothing is left dangling on the deleted loser
        assert db_with_notes.conn.execute(
            "SELECT COUNT(*) FROM entity_aliases WHERE entity_id=?", (loser,)).fetchone()[0] == 0

    def test_merge_drops_self_loops(self, db_with_notes):
        # A relation BETWEEN the merged pair must not survive as a self-loop.
        keep = db_with_notes.upsert_entity("Sebastian", "person")
        loser = db_with_notes.upsert_entity("Sebastian Wallmark", "person")
        note = db_with_notes.conn.execute("SELECT id FROM notes LIMIT 1").fetchone()["id"]
        db_with_notes.upsert_relation(keep, "works_with", loser, note)
        db_with_notes.merge_entities(keep_id=keep, merge_id=loser)
        loops = db_with_notes.conn.execute(
            "SELECT COUNT(*) FROM relations WHERE entity_a_id=entity_b_id").fetchone()[0]
        assert loops == 0

    def test_merge_noop_on_same_or_missing(self, db_with_notes):
        keep = db_with_notes.upsert_entity("Sebastian", "person")
        db_with_notes.merge_entities(keep_id=keep, merge_id=keep)      # same id
        db_with_notes.merge_entities(keep_id=keep, merge_id=999999)    # missing
        assert db_with_notes.conn.execute(
            "SELECT COUNT(*) FROM entities WHERE id=?", (keep,)).fetchone()[0] == 1


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


class TestClearExtractionForNote:
    """Regression, 2026-08-07. clear_extraction_for_note() promised entity removal
    in its docstring and only ever deleted relations, links and the extraction
    record. Extraction is nondeterministic, so every re-run stranded whatever the
    fresh LLM pass did not reproduce. On a live graph that reached 11,172 orphans
    out of 21,696 entities (51%), unreachable by search and invisible to traversal
    but counted in every stats line as real.
    """

    def _two_notes(self, db):
        for i in (1, 2):
            db.upsert_note(
                path=f"n{i}.md",
                title=f"N{i}",
                content="c",
                content_hash=f"h{i}",
                folder="f",
                source_type="vault",
                indexed_at="2026-01-01T00:00:00Z",
            )
        return [r[0] for r in db.conn.execute("SELECT id FROM notes ORDER BY id")]

    def test_sweeps_entities_only_this_note_held(self, db):
        n1, _ = self._two_notes(db)
        eid = db.upsert_entity("Ric", "person")
        db.link_entity_to_note(eid, n1)

        db.clear_extraction_for_note(n1)

        assert db.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0

    def test_keeps_entities_another_note_still_links(self, db):
        n1, n2 = self._two_notes(db)
        eid = db.upsert_entity("Heimdall", "project")
        db.link_entity_to_note(eid, n1)
        db.link_entity_to_note(eid, n2)

        db.clear_extraction_for_note(n1)

        assert db.resolve_entity("Heimdall") == eid

    def test_sweeps_entities_held_only_by_a_relation(self, db):
        # The subtle half. _extract_note() resolves a relation's endpoints via
        # resolve_entity(), which reuses an entity that already exists elsewhere
        # and does NOT link it to this note. Such an entity is held up by the
        # relation alone, so a sweep scoped to note-links only still strands it.
        # A 3-note re-extraction leaked exactly this way after the first cut.
        n1, _ = self._two_notes(db)
        a = db.upsert_entity("Ric", "person")
        db.link_entity_to_note(a, n1)
        endpoint_only = db.upsert_entity("Odin", "project")
        db.upsert_relation(
            entity_a_id=a,
            relation_type="uses",
            entity_b_id=endpoint_only,
            source_note_id=n1,
        )

        db.clear_extraction_for_note(n1)

        assert db.resolve_entity("Odin") is None

    def test_leaves_no_orphans_behind(self, db):
        n1, _ = self._two_notes(db)
        a = db.upsert_entity("Ric", "person")
        db.link_entity_to_note(a, n1)
        b = db.upsert_entity("Odin", "project")
        db.upsert_relation(
            entity_a_id=a, relation_type="uses", entity_b_id=b, source_note_id=n1
        )

        db.clear_extraction_for_note(n1)

        orphans = db.conn.execute(
            "SELECT COUNT(*) FROM entities e"
            " WHERE NOT EXISTS(SELECT 1 FROM entity_note_links l WHERE l.entity_id = e.id)"
            "   AND NOT EXISTS(SELECT 1 FROM relations r"
            "                  WHERE r.entity_a_id = e.id OR r.entity_b_id = e.id)"
        ).fetchone()[0]
        assert orphans == 0

    def test_sweeps_aliases_of_deleted_entities(self, db):
        # entity_aliases declares ON DELETE CASCADE, but SQLite ignores foreign
        # keys unless PRAGMA foreign_keys = ON and this connection never sets it.
        # Only reachable once entities began being deleted at all.
        n1, _ = self._two_notes(db)
        eid = db.upsert_entity("Ric", "person")
        db.add_alias(eid, "Rick")
        db.link_entity_to_note(eid, n1)

        db.clear_extraction_for_note(n1)

        assert db.conn.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0] == 0

    def test_keeps_aliases_of_surviving_entities(self, db):
        n1, n2 = self._two_notes(db)
        eid = db.upsert_entity("Heimdall", "project")
        db.add_alias(eid, "HD")
        db.link_entity_to_note(eid, n1)
        db.link_entity_to_note(eid, n2)

        db.clear_extraction_for_note(n1)

        assert db.resolve_entity("HD") == eid

    def test_freed_alias_can_be_reclaimed(self, db):
        # alias is UNIQUE. A stale row squats the name: add_alias() for a new
        # entity hits INSERT OR IGNORE, silently does nothing, and the alias
        # resolves to None forever.
        n1, _ = self._two_notes(db)
        old = db.upsert_entity("Ric", "person")
        db.add_alias(old, "Rick")
        db.link_entity_to_note(old, n1)

        db.clear_extraction_for_note(n1)

        new = db.upsert_entity("Richard", "person")
        db.add_alias(new, "Rick")
        assert db.resolve_entity("Rick") == new
