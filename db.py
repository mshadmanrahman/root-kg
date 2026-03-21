"""
ROOT database layer.

SQLite + sqlite-vec for vector storage.
Entity graph with recursive CTE traversal.
Immutable pattern: all functions return new data, never mutate inputs.
"""

import json
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sqlite_vec


def _serialize_vector(vec: list[float]) -> bytes:
    """Serialize a float vector to bytes for sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


def _deserialize_vector(data: bytes, dim: int) -> list[float]:
    """Deserialize bytes back to a float vector."""
    return list(struct.unpack(f"{dim}f", data))


class RootDB:
    """SQLite database with vector search for ROOT."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self._init_schema()

    def _init_schema(self):
        """Create tables if they don't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                folder TEXT,
                source_type TEXT DEFAULT 'vault',
                created_at TEXT,
                updated_at TEXT,
                indexed_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_notes_path ON notes(path);
            CREATE INDEX IF NOT EXISTS idx_notes_hash ON notes(content_hash);
            CREATE INDEX IF NOT EXISTS idx_notes_source ON notes(source_type);
            CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder);
        """)

        # Create virtual table for vector search (384 dims for MiniLM)
        try:
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
                    chunk_id INTEGER PRIMARY KEY,
                    embedding FLOAT[384]
                );
            """)
        except sqlite3.OperationalError:
            pass  # Already exists

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id INTEGER NOT NULL,
                chunk_idx INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE,
                UNIQUE(note_id, chunk_idx)
            );
        """)

        # Phase 3: Entity graph tables
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS entity_extractions (
                note_id INTEGER PRIMARY KEY,
                content_hash TEXT NOT NULL,
                extracted_at TEXT NOT NULL,
                model TEXT NOT NULL,
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                first_seen_at TEXT,
                last_seen_at TEXT,
                mention_count INTEGER DEFAULT 1,
                metadata TEXT,
                UNIQUE(name, entity_type)
            );
            CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
            CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

            CREATE TABLE IF NOT EXISTS entity_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL,
                alias TEXT NOT NULL UNIQUE,
                FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_aliases_alias ON entity_aliases(alias);

            CREATE TABLE IF NOT EXISTS relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_a_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                entity_b_id INTEGER NOT NULL,
                source_note_id INTEGER NOT NULL,
                confidence REAL DEFAULT 0.8,
                context TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (entity_a_id) REFERENCES entities(id) ON DELETE CASCADE,
                FOREIGN KEY (entity_b_id) REFERENCES entities(id) ON DELETE CASCADE,
                FOREIGN KEY (source_note_id) REFERENCES notes(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_relations_a ON relations(entity_a_id);
            CREATE INDEX IF NOT EXISTS idx_relations_b ON relations(entity_b_id);
            CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(relation_type);
            CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_note_id);

            CREATE TABLE IF NOT EXISTS entity_note_links (
                entity_id INTEGER NOT NULL,
                note_id INTEGER NOT NULL,
                PRIMARY KEY (entity_id, note_id),
                FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE,
                FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
            );
        """)
        self.conn.commit()

    def upsert_note(
        self,
        path: str,
        title: str,
        content: str,
        content_hash: str,
        folder: str,
        source_type: str = "vault",
        created_at: Optional[str] = None,
        indexed_at: str = "",
    ) -> int:
        """Insert or update a note. Returns the note ID."""
        cursor = self.conn.execute(
            """
            INSERT INTO notes (path, title, content, content_hash, folder, source_type, created_at, updated_at, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                title = excluded.title,
                content = excluded.content,
                content_hash = excluded.content_hash,
                folder = excluded.folder,
                updated_at = excluded.indexed_at,
                indexed_at = excluded.indexed_at
            """,
            (path, title, content, content_hash, folder, source_type, created_at, indexed_at, indexed_at),
        )
        self.conn.commit()

        row = self.conn.execute("SELECT id FROM notes WHERE path = ?", (path,)).fetchone()
        return row["id"]

    def get_note_hash(self, path: str) -> Optional[str]:
        """Get the stored content hash for a note path."""
        row = self.conn.execute(
            "SELECT content_hash FROM notes WHERE path = ?", (path,)
        ).fetchone()
        return row["content_hash"] if row else None

    def store_chunks(self, note_id: int, chunks: list[dict]):
        """Store chunk texts and their embeddings for a note.

        Each chunk dict: {"idx": int, "text": str, "embedding": list[float]}
        """
        # Remove old chunks for this note
        old_chunk_ids = [
            r["id"]
            for r in self.conn.execute(
                "SELECT id FROM chunks WHERE note_id = ?", (note_id,)
            ).fetchall()
        ]
        if old_chunk_ids:
            placeholders = ",".join("?" * len(old_chunk_ids))
            self.conn.execute(
                f"DELETE FROM chunks_vec WHERE chunk_id IN ({placeholders})",
                old_chunk_ids,
            )
            self.conn.execute("DELETE FROM chunks WHERE note_id = ?", (note_id,))

        for chunk in chunks:
            cursor = self.conn.execute(
                "INSERT INTO chunks (note_id, chunk_idx, chunk_text) VALUES (?, ?, ?)",
                (note_id, chunk["idx"], chunk["text"]),
            )
            chunk_id = cursor.lastrowid
            self.conn.execute(
                "INSERT INTO chunks_vec (chunk_id, embedding) VALUES (?, ?)",
                (chunk_id, _serialize_vector(chunk["embedding"])),
            )

        self.conn.commit()

    def search(self, query_embedding: list[float], limit: int = 10) -> list[dict]:
        """Semantic search: find chunks closest to query embedding.

        Returns list of dicts with note metadata + chunk text + distance.
        """
        rows = self.conn.execute(
            """
            SELECT
                cv.chunk_id,
                cv.distance,
                c.chunk_text,
                c.note_id,
                n.path,
                n.title,
                n.folder,
                n.source_type
            FROM chunks_vec cv
            JOIN chunks c ON c.id = cv.chunk_id
            JOIN notes n ON n.id = c.note_id
            WHERE cv.embedding MATCH ?
            AND cv.k = ?
            ORDER BY cv.distance
            """,
            (_serialize_vector(query_embedding), limit),
        ).fetchall()

        return [dict(row) for row in rows]

    def get_all_embeddings(self) -> list[dict]:
        """Get all chunk embeddings with metadata (for clustering)."""
        rows = self.conn.execute(
            """
            SELECT
                c.id as chunk_id,
                c.chunk_text,
                c.note_id,
                n.path,
                n.title,
                n.folder,
                cv.embedding as raw_embedding
            FROM chunks c
            JOIN notes n ON n.id = c.note_id
            JOIN chunks_vec cv ON cv.chunk_id = c.id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def get_note_by_path(self, path: str) -> Optional[dict]:
        """Get full note content by path."""
        row = self.conn.execute(
            "SELECT * FROM notes WHERE path = ?", (path,)
        ).fetchone()
        return dict(row) if row else None

    def get_stats(self) -> dict:
        """Get index statistics."""
        note_count = self.conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        chunk_count = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        source_counts = self.conn.execute(
            "SELECT source_type, COUNT(*) as cnt FROM notes GROUP BY source_type"
        ).fetchall()
        folder_counts = self.conn.execute(
            "SELECT folder, COUNT(*) as cnt FROM notes GROUP BY folder ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        last_indexed = self.conn.execute(
            "SELECT MAX(indexed_at) FROM notes"
        ).fetchone()[0]

        return {
            "total_notes": note_count,
            "total_chunks": chunk_count,
            "last_indexed": last_indexed,
            "by_source": {r["source_type"]: r["cnt"] for r in source_counts},
            "top_folders": {r["folder"]: r["cnt"] for r in folder_counts},
        }

    def remove_stale_notes(self, valid_paths: set[str]) -> int:
        """Remove notes whose paths no longer exist. Returns count removed."""
        all_paths = {
            r["path"]
            for r in self.conn.execute("SELECT path FROM notes WHERE source_type = 'vault'").fetchall()
        }
        stale = all_paths - valid_paths
        if not stale:
            return 0

        for path in stale:
            note = self.conn.execute("SELECT id FROM notes WHERE path = ?", (path,)).fetchone()
            if note:
                note_id = note["id"]
                chunk_ids = [
                    r["id"]
                    for r in self.conn.execute("SELECT id FROM chunks WHERE note_id = ?", (note_id,)).fetchall()
                ]
                if chunk_ids:
                    placeholders = ",".join("?" * len(chunk_ids))
                    self.conn.execute(f"DELETE FROM chunks_vec WHERE chunk_id IN ({placeholders})", chunk_ids)
                self.conn.execute("DELETE FROM chunks WHERE note_id = ?", (note_id,))
                self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))

        self.conn.commit()
        return len(stale)

    # ── Entity Graph Methods ──────────────────────────────────────

    def upsert_entity(
        self,
        name: str,
        entity_type: str,
        first_seen: Optional[str] = None,
        last_seen: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> int:
        """Insert or update an entity. Returns entity ID."""
        meta_json = json.dumps(metadata) if metadata else None
        self.conn.execute(
            """
            INSERT INTO entities (name, entity_type, first_seen_at, last_seen_at, mention_count, metadata)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(name, entity_type) DO UPDATE SET
                mention_count = mention_count + 1,
                last_seen_at = COALESCE(excluded.last_seen_at, last_seen_at),
                first_seen_at = COALESCE(
                    MIN(first_seen_at, excluded.first_seen_at),
                    excluded.first_seen_at,
                    first_seen_at
                ),
                metadata = COALESCE(excluded.metadata, metadata)
            """,
            (name, entity_type, first_seen, last_seen, meta_json),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM entities WHERE name = ? AND entity_type = ?",
            (name, entity_type),
        ).fetchone()
        return row["id"]

    def add_alias(self, entity_id: int, alias: str) -> None:
        """Add an alias for an entity. Idempotent."""
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO entity_aliases (entity_id, alias) VALUES (?, ?)",
                (entity_id, alias),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass

    def resolve_entity(self, name: str) -> Optional[int]:
        """Look up entity by name or alias. Returns entity ID or None."""
        row = self.conn.execute(
            "SELECT id FROM entities WHERE LOWER(name) = LOWER(?)", (name,)
        ).fetchone()
        if row:
            return row["id"]
        row = self.conn.execute(
            "SELECT entity_id FROM entity_aliases WHERE LOWER(alias) = LOWER(?)", (name,)
        ).fetchone()
        return row["entity_id"] if row else None

    def link_entity_to_note(self, entity_id: int, note_id: int) -> None:
        """Record that an entity was mentioned in a note."""
        self.conn.execute(
            "INSERT OR IGNORE INTO entity_note_links (entity_id, note_id) VALUES (?, ?)",
            (entity_id, note_id),
        )
        self.conn.commit()

    def upsert_relation(
        self,
        entity_a_id: int,
        relation_type: str,
        entity_b_id: int,
        source_note_id: int,
        confidence: float = 0.8,
        context: Optional[str] = None,
    ) -> int:
        """Insert a relation between two entities. Returns relation ID."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """
            INSERT INTO relations (entity_a_id, relation_type, entity_b_id, source_note_id, confidence, context, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_a_id, relation_type, entity_b_id, source_note_id, confidence, context, now),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_extraction_hash(self, note_id: int) -> Optional[str]:
        """Get the content hash from the last extraction for a note."""
        row = self.conn.execute(
            "SELECT content_hash FROM entity_extractions WHERE note_id = ?", (note_id,)
        ).fetchone()
        return row["content_hash"] if row else None

    def mark_extracted(self, note_id: int, content_hash: str, model: str) -> None:
        """Record that a note has been entity-extracted."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO entity_extractions (note_id, content_hash, extracted_at, model)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                content_hash = excluded.content_hash,
                extracted_at = excluded.extracted_at,
                model = excluded.model
            """,
            (note_id, content_hash, now, model),
        )
        self.conn.commit()

    def get_notes_needing_extraction(self) -> list[dict]:
        """Get notes where content has changed since last extraction or never extracted."""
        rows = self.conn.execute(
            """
            SELECT n.id, n.path, n.title, n.content, n.content_hash, n.folder,
                   n.source_type, n.created_at, n.indexed_at
            FROM notes n
            LEFT JOIN entity_extractions ee ON ee.note_id = n.id
            WHERE ee.note_id IS NULL
               OR ee.content_hash != n.content_hash
            ORDER BY n.indexed_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def get_entity_neighborhood(self, entity_id: int, depth: int = 2) -> list[dict]:
        """Traverse the entity graph using recursive CTE. Returns connected entities with depth."""
        rows = self.conn.execute(
            """
            WITH RECURSIVE graph(entity_id, depth, path) AS (
                SELECT ?, 0, CAST(? AS TEXT)
                UNION ALL
                SELECT
                    CASE WHEN r.entity_a_id = g.entity_id THEN r.entity_b_id ELSE r.entity_a_id END,
                    g.depth + 1,
                    g.path || ',' || CASE WHEN r.entity_a_id = g.entity_id
                        THEN CAST(r.entity_b_id AS TEXT)
                        ELSE CAST(r.entity_a_id AS TEXT) END
                FROM relations r
                JOIN graph g ON (r.entity_a_id = g.entity_id OR r.entity_b_id = g.entity_id)
                WHERE g.depth < ?
                  AND g.path NOT LIKE '%,' || CASE WHEN r.entity_a_id = g.entity_id
                      THEN CAST(r.entity_b_id AS TEXT)
                      ELSE CAST(r.entity_a_id AS TEXT) END || '%'
            )
            SELECT DISTINCT e.id, e.name, e.entity_type, e.mention_count,
                   e.first_seen_at, e.last_seen_at, MIN(g.depth) as depth
            FROM graph g
            JOIN entities e ON e.id = g.entity_id
            GROUP BY e.id
            ORDER BY MIN(g.depth), e.mention_count DESC
            """,
            (entity_id, str(entity_id), depth),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_entity_relations(self, entity_id: int) -> list[dict]:
        """Get all direct relations for an entity with full context."""
        rows = self.conn.execute(
            """
            SELECT r.id, r.relation_type, r.confidence, r.context, r.created_at,
                   r.source_note_id, n.title as source_title, n.path as source_path,
                   ea.name as entity_a_name, ea.entity_type as entity_a_type,
                   eb.name as entity_b_name, eb.entity_type as entity_b_type
            FROM relations r
            JOIN entities ea ON ea.id = r.entity_a_id
            JOIN entities eb ON eb.id = r.entity_b_id
            JOIN notes n ON n.id = r.source_note_id
            WHERE r.entity_a_id = ? OR r.entity_b_id = ?
            ORDER BY r.confidence DESC, r.created_at DESC
            """,
            (entity_id, entity_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_entities(self, query: str, entity_type: Optional[str] = None) -> list[dict]:
        """Search entities by name (case-insensitive LIKE match)."""
        if entity_type:
            rows = self.conn.execute(
                """
                SELECT * FROM entities
                WHERE LOWER(name) LIKE LOWER(?) AND entity_type = ?
                ORDER BY mention_count DESC LIMIT 20
                """,
                (f"%{query}%", entity_type),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM entities
                WHERE LOWER(name) LIKE LOWER(?)
                ORDER BY mention_count DESC LIMIT 20
                """,
                (f"%{query}%",),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_notes_for_entity(self, entity_id: int) -> list[dict]:
        """Get all notes that mention an entity."""
        rows = self.conn.execute(
            """
            SELECT n.id, n.path, n.title, n.folder, n.source_type, n.created_at
            FROM entity_note_links enl
            JOIN notes n ON n.id = enl.note_id
            WHERE enl.entity_id = ?
            ORDER BY n.created_at DESC
            """,
            (entity_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_declining_entities(self, days_inactive: int = 30, min_mentions: int = 3) -> list[dict]:
        """Find entities that were active but have gone silent."""
        cutoff = datetime.now(timezone.utc).isoformat()[:10]
        rows = self.conn.execute(
            """
            SELECT * FROM entities
            WHERE mention_count >= ?
              AND last_seen_at IS NOT NULL
              AND DATE(last_seen_at) < DATE(?, '-' || ? || ' days')
            ORDER BY mention_count DESC
            """,
            (min_mentions, cutoff, days_inactive),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_entities(self, days: int = 7) -> list[dict]:
        """Get entities first seen or last seen in the past N days."""
        cutoff = datetime.now(timezone.utc).isoformat()[:10]
        rows = self.conn.execute(
            """
            SELECT * FROM entities
            WHERE DATE(last_seen_at) >= DATE(?, '-' || ? || ' days')
            ORDER BY last_seen_at DESC
            """,
            (cutoff, days),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_relations(self, days: int = 7) -> list[dict]:
        """Get relations created in the past N days."""
        cutoff = datetime.now(timezone.utc).isoformat()[:10]
        rows = self.conn.execute(
            """
            SELECT r.*, ea.name as entity_a_name, eb.name as entity_b_name
            FROM relations r
            JOIN entities ea ON ea.id = r.entity_a_id
            JOIN entities eb ON eb.id = r.entity_b_id
            WHERE DATE(r.created_at) >= DATE(?, '-' || ? || ' days')
            ORDER BY r.created_at DESC
            """,
            (cutoff, days),
        ).fetchall()
        return [dict(r) for r in rows]

    def merge_entities(self, keep_id: int, merge_id: int) -> None:
        """Merge one entity into another. Reassigns all relations and aliases."""
        self.conn.execute(
            "UPDATE relations SET entity_a_id = ? WHERE entity_a_id = ?",
            (keep_id, merge_id),
        )
        self.conn.execute(
            "UPDATE relations SET entity_b_id = ? WHERE entity_b_id = ?",
            (keep_id, merge_id),
        )
        self.conn.execute(
            "UPDATE entity_note_links SET entity_id = ? WHERE entity_id = ?",
            (keep_id, merge_id),
        )
        self.conn.execute(
            "UPDATE entity_aliases SET entity_id = ? WHERE entity_id = ?",
            (keep_id, merge_id),
        )
        # Add the merged entity's name as an alias
        merged = self.conn.execute(
            "SELECT name FROM entities WHERE id = ?", (merge_id,)
        ).fetchone()
        if merged:
            self.add_alias(keep_id, merged["name"])
        # Sum mention counts
        self.conn.execute(
            """
            UPDATE entities SET mention_count = mention_count + (
                SELECT mention_count FROM entities WHERE id = ?
            ) WHERE id = ?
            """,
            (merge_id, keep_id),
        )
        self.conn.execute("DELETE FROM entities WHERE id = ?", (merge_id,))
        self.conn.commit()

    def get_entity_stats(self) -> dict:
        """Get entity graph statistics."""
        entity_count = self.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        relation_count = self.conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        extraction_count = self.conn.execute("SELECT COUNT(*) FROM entity_extractions").fetchone()[0]

        type_counts = self.conn.execute(
            "SELECT entity_type, COUNT(*) as cnt FROM entities GROUP BY entity_type ORDER BY cnt DESC"
        ).fetchall()
        relation_type_counts = self.conn.execute(
            "SELECT relation_type, COUNT(*) as cnt FROM relations GROUP BY relation_type ORDER BY cnt DESC"
        ).fetchall()

        return {
            "total_entities": entity_count,
            "total_relations": relation_count,
            "notes_extracted": extraction_count,
            "by_entity_type": {r["entity_type"]: r["cnt"] for r in type_counts},
            "by_relation_type": {r["relation_type"]: r["cnt"] for r in relation_type_counts},
        }

    def clear_extraction_for_note(self, note_id: int) -> None:
        """Remove all entities/relations sourced from a specific note (for re-extraction)."""
        self.conn.execute("DELETE FROM relations WHERE source_note_id = ?", (note_id,))
        self.conn.execute("DELETE FROM entity_note_links WHERE note_id = ?", (note_id,))
        self.conn.execute("DELETE FROM entity_extractions WHERE note_id = ?", (note_id,))
        self.conn.commit()

    def close(self):
        self.conn.close()
