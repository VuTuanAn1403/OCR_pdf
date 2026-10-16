"""Durable prepared/publication/acknowledgement ledger (no Excel writes)."""
import json
from pathlib import Path
import sqlite3
from src.ocr.runtime.atomic_io import atomic_write_text


class BatchStore:
    def __init__(self, path, identity, records):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, ordinal INTEGER UNIQUE, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS revisions(revision INTEGER PRIMARY KEY, projection TEXT NOT NULL, workbook_sha256 TEXT);
          CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY, document_id TEXT, stage TEXT NOT NULL, payload TEXT);
        """)
        existing = self.get("identity")
        if existing is not None and existing != identity:
            self.connection.close()
            raise ValueError("BATCH_IDENTITY_MISMATCH: use a new output directory")
        if existing is None:
            with self.connection:
                self.put("identity", identity)
                self.put("prepared_revision", 0)
                self.put("published_revision", -1)
                for i, record in enumerate(records):
                    self.connection.execute("INSERT INTO documents VALUES(?,?,?)", (record["id"], i, json.dumps(record, ensure_ascii=False)))
                self.connection.execute("INSERT INTO revisions VALUES(0,?,NULL)", (json.dumps(records, ensure_ascii=False),))

    def close(self):
        self.connection.close()

    def export_events(self, path):
        """Rebuild the inspectable JSONL view from the authoritative ledger."""
        rows = self.connection.execute("SELECT sequence,document_id,stage,payload FROM events ORDER BY sequence")
        atomic_write_text(Path(path), "".join(json.dumps({"sequence":r[0], "document_id":r[1],
            "stage":r[2], "payload":json.loads(r[3]) if r[3] else {}}, ensure_ascii=False)+"\n" for r in rows))

    def get(self, key):
        row = self.connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        self.connection.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))

    def records(self):
        return [json.loads(r[0]) for r in self.connection.execute("SELECT payload FROM documents ORDER BY ordinal")]

    def event(self, document_id, stage, **payload):
        with self.connection:
            self.connection.execute("INSERT INTO events(document_id,stage,payload) VALUES(?,?,?)", (document_id, stage, json.dumps(payload)))

    def prepare(self, record, *, allow_unpublished=False):
        with self.connection:
            if not allow_unpublished and self.get("prepared_revision") != self.get("published_revision"):
                raise RuntimeError("Previous publication must be acknowledged first")
            revision = self.get("prepared_revision")+1
            self.connection.execute("UPDATE documents SET payload=? WHERE id=?", (json.dumps(record, ensure_ascii=False), record["id"]))
            projection = self.records()
            self.connection.execute("INSERT INTO revisions VALUES(?,?,NULL)", (revision, json.dumps(projection, ensure_ascii=False)))
            self.put("prepared_revision", revision)
            self.connection.execute("INSERT INTO events(document_id,stage) VALUES(?,?)", (record["id"], "READY_TO_PUBLISH"))
        return revision

    def projection(self):
        revision = self.get("prepared_revision")
        row = self.connection.execute("SELECT projection,workbook_sha256 FROM revisions WHERE revision=?", (revision,)).fetchone()
        return revision, json.loads(row[0]), row[1]

    def acknowledge(self, revision, workbook_sha256):
        with self.connection:
            if revision != self.get("prepared_revision"):
                raise RuntimeError("Stale publication acknowledgement")
            self.connection.execute("UPDATE revisions SET workbook_sha256=? WHERE revision=?", (workbook_sha256, revision))
            self.put("published_revision", revision)
            self.connection.execute("INSERT INTO events(stage,payload) VALUES('COMMITTED',?)", (json.dumps({"revision":revision}),))

