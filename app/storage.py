from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .models import Paper


STATUS_ORDER = [
    "discovered",
    "screened",
    "venue_verified",
    "selected",
    "extracted",
    "drafted",
    "ready_for_review",
    "approved",
    "published",
]
ALLOWED_TRANSITIONS = {
    current: {STATUS_ORDER[index + 1]}
    for index, current in enumerate(STATUS_ORDER[:-1])
}
ALLOWED_TRANSITIONS.update({"published": set()})


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS papers (
                    id INTEGER PRIMARY KEY,
                    arxiv_id TEXT NOT NULL UNIQUE,
                    doi TEXT,
                    title TEXT NOT NULL,
                    abstract TEXT NOT NULL,
                    authors_json TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    categories_json TEXT NOT NULL,
                    pdf_url TEXT NOT NULL,
                    journal_ref TEXT,
                    comment TEXT,
                    venue TEXT,
                    venue_code TEXT,
                    venue_status TEXT NOT NULL DEFAULT 'unverified',
                    venue_evidence_url TEXT,
                    topic TEXT,
                    topic_label TEXT,
                    score REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'discovered',
                    is_demo INTEGER NOT NULL DEFAULT 0,
                    source_text_path TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    rejection_reason TEXT,
                    first_seen_at TEXT NOT NULL,
                    updated_db_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS papers_doi_unique
                    ON papers(doi) WHERE doi IS NOT NULL AND doi != '';
                CREATE TABLE IF NOT EXISTS claims (
                    id INTEGER PRIMARY KEY,
                    paper_id INTEGER NOT NULL REFERENCES papers(id),
                    claim_type TEXT NOT NULL,
                    claim_text TEXT NOT NULL,
                    source_page INTEGER,
                    source_anchor TEXT
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    id INTEGER PRIMARY KEY,
                    paper_id INTEGER NOT NULL REFERENCES papers(id),
                    channel TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    validation_status TEXT NOT NULL,
                    approved_at TEXT,
                    UNIQUE(paper_id, channel)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    candidates INTEGER NOT NULL DEFAULT 0,
                    accepted INTEGER NOT NULL DEFAULT 0,
                    selected INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    mode TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    paper_id INTEGER REFERENCES papers(id),
                    run_id INTEGER REFERENCES runs(id),
                    event TEXT NOT NULL,
                    detail TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS publications (
                    id INTEGER PRIMARY KEY,
                    paper_id INTEGER NOT NULL REFERENCES papers(id),
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    external_id TEXT,
                    external_url TEXT,
                    message TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(paper_id, channel)
                );
                CREATE INDEX IF NOT EXISTS idx_publications_paper_id
                    ON publications(paper_id);
                """
            )
            db.execute("PRAGMA optimize")
        # Rejection is no longer a retained business state. This also removes
        # legacy rejected rows the first time an existing database is opened.
        self.delete_papers_by_status("rejected")

    def start_run(self, mode: str) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO runs(started_at, mode) VALUES (?, ?)",
                (now_iso(), mode),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        candidates: int,
        accepted: int,
        selected: int,
        error: str | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """UPDATE runs SET finished_at=?, candidates=?, accepted=?,
                   selected=?, error=? WHERE id=?""",
                (now_iso(), candidates, accepted, selected, error, run_id),
            )

    def upsert_paper(self, paper: Paper) -> tuple[int, bool]:
        timestamp = now_iso()
        with self.connect() as db:
            existing = db.execute(
                """SELECT id, status FROM papers
                   WHERE arxiv_id=?
                      OR (? IS NOT NULL AND doi=?)
                      OR lower(trim(title))=lower(trim(?))
                   ORDER BY CASE WHEN arxiv_id=? THEN 0 ELSE 1 END
                   LIMIT 1""",
                (paper.arxiv_id, paper.doi, paper.doi, paper.title, paper.arxiv_id),
            ).fetchone()
            if existing:
                db.execute(
                    """UPDATE papers SET title=?, abstract=?, authors_json=?,
                       updated_at=?, categories_json=?, pdf_url=?, doi=COALESCE(?, doi),
                       journal_ref=COALESCE(?, journal_ref), comment=COALESCE(?, comment),
                       metadata_json=?, updated_db_at=? WHERE id=?""",
                    (
                        paper.title,
                        paper.abstract,
                        json.dumps(paper.authors, ensure_ascii=False),
                        paper.updated_at,
                        json.dumps(paper.categories, ensure_ascii=False),
                        paper.pdf_url,
                        paper.doi,
                        paper.journal_ref,
                        paper.comment,
                        json.dumps(paper.metadata, ensure_ascii=False),
                        timestamp,
                        existing["id"],
                    ),
                )
                return int(existing["id"]), False
            cursor = db.execute(
                """INSERT INTO papers(
                       arxiv_id, doi, title, abstract, authors_json, published_at,
                       updated_at, categories_json, pdf_url, journal_ref, comment,
                       venue, venue_code, venue_status, venue_evidence_url, topic,
                       topic_label, score, status, is_demo, source_text_path,
                       metadata_json, first_seen_at, updated_db_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    paper.arxiv_id,
                    paper.doi,
                    paper.title,
                    paper.abstract,
                    json.dumps(paper.authors, ensure_ascii=False),
                    paper.published_at,
                    paper.updated_at,
                    json.dumps(paper.categories, ensure_ascii=False),
                    paper.pdf_url,
                    paper.journal_ref,
                    paper.comment,
                    paper.venue,
                    paper.venue_code,
                    paper.venue_status,
                    paper.venue_evidence_url,
                    paper.topic,
                    paper.topic_label,
                    paper.score,
                    paper.status,
                    int(paper.is_demo),
                    paper.source_text_path,
                    json.dumps(paper.metadata, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid), True

    def get_paper(self, paper_id_or_arxiv: int | str) -> dict[str, Any] | None:
        field = "id" if isinstance(paper_id_or_arxiv, int) else "arxiv_id"
        with self.connect() as db:
            row = db.execute(
                f"SELECT * FROM papers WHERE {field}=?", (paper_id_or_arxiv,)
            ).fetchone()
        return self._decode_paper(row) if row else None

    def list_papers(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM papers"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status=?"
            params = (status,)
        query += " ORDER BY score DESC, id DESC"
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [self._decode_paper(row) for row in rows]

    def delete_paper(self, paper_id: int) -> bool:
        """Delete a discarded paper and every dependent record in one transaction."""
        with self.connect() as db:
            exists = db.execute("SELECT 1 FROM papers WHERE id=?", (paper_id,)).fetchone()
            if not exists:
                return False
            for table in ("publications", "drafts", "claims", "events"):
                db.execute(f"DELETE FROM {table} WHERE paper_id=?", (paper_id,))
            db.execute("DELETE FROM papers WHERE id=?", (paper_id,))
        return True

    def delete_papers_by_status(self, status: str) -> int:
        paper_ids = [int(paper["id"]) for paper in self.list_papers(status)]
        for paper_id in paper_ids:
            self.delete_paper(paper_id)
        return len(paper_ids)

    @staticmethod
    def _decode_paper(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["authors"] = json.loads(value.pop("authors_json"))
        value["categories"] = json.loads(value.pop("categories_json"))
        value["metadata"] = json.loads(value.pop("metadata_json"))
        value["is_demo"] = bool(value["is_demo"])
        return value

    def update_fields(self, paper_id: int, **fields: Any) -> None:
        allowed = {
            "venue", "venue_code", "venue_status", "venue_evidence_url",
            "topic", "topic_label", "score", "source_text_path",
            "rejection_reason", "metadata_json",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unsupported paper fields: {sorted(invalid)}")
        if not fields:
            return
        fields["updated_db_at"] = now_iso()
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self.connect() as db:
            db.execute(
                f"UPDATE papers SET {assignments} WHERE id=?",
                (*fields.values(), paper_id),
            )

    def transition(
        self, paper_id: int, new_status: str, *, run_id: int | None = None, detail: str = ""
    ) -> None:
        with self.connect() as db:
            row = db.execute("SELECT status FROM papers WHERE id=?", (paper_id,)).fetchone()
            if not row:
                raise KeyError(f"Unknown paper id: {paper_id}")
            current = str(row["status"])
            if new_status == current:
                return
            if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
                raise ValueError(f"Invalid status transition: {current} -> {new_status}")
            db.execute(
                "UPDATE papers SET status=?, updated_db_at=? WHERE id=?",
                (new_status, now_iso(), paper_id),
            )
            db.execute(
                """INSERT INTO events(paper_id, run_id, event, detail, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (paper_id, run_id, f"{current}->{new_status}", detail, now_iso()),
            )

    def replace_claims(self, paper_id: int, claims: list[dict[str, Any]]) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM claims WHERE paper_id=?", (paper_id,))
            db.executemany(
                """INSERT INTO claims(paper_id, claim_type, claim_text,
                   source_page, source_anchor) VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        paper_id,
                        item["claim_type"],
                        item["claim_text"],
                        item.get("source_page"),
                        item.get("source_anchor"),
                    )
                    for item in claims
                ],
            )

    def save_draft(
        self,
        paper_id: int,
        channel: str,
        content: dict[str, Any],
        artifact_path: Path,
        validation_status: str,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO drafts(paper_id, channel, content_json,
                   artifact_path, prompt_version, validation_status)
                   VALUES (?, ?, ?, ?, 'v1', ?)
                   ON CONFLICT(paper_id, channel) DO UPDATE SET
                     content_json=excluded.content_json,
                     artifact_path=excluded.artifact_path,
                     prompt_version=excluded.prompt_version,
                     validation_status=excluded.validation_status""",
                (
                    paper_id,
                    channel,
                    json.dumps(content, ensure_ascii=False),
                    str(artifact_path),
                    validation_status,
                ),
            )

    def approve(self, paper_id: int) -> None:
        paper = self.get_paper(paper_id)
        if not paper or paper["status"] != "ready_for_review":
            actual = paper["status"] if paper else "missing"
            raise ValueError(f"Paper must be ready_for_review, got {actual}")
        self.transition(paper_id, "approved", detail="human approval")
        with self.connect() as db:
            db.execute(
                "UPDATE drafts SET approved_at=? WHERE paper_id=?",
                (now_iso(), paper_id),
            )

    def log_event(
        self, event: str, *, paper_id: int | None = None,
        run_id: int | None = None, detail: str = ""
    ) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO events(paper_id, run_id, event, detail, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (paper_id, run_id, event, detail, now_iso()),
            )

    def save_publication(
        self,
        paper_id: int,
        channel: str,
        status: str,
        *,
        external_id: str | None = None,
        external_url: str | None = None,
        message: str = "",
    ) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO publications(
                       paper_id, channel, status, external_id, external_url,
                       message, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(paper_id, channel) DO UPDATE SET
                     status=excluded.status,
                     external_id=excluded.external_id,
                     external_url=excluded.external_url,
                     message=excluded.message,
                     updated_at=excluded.updated_at""",
                (
                    paper_id,
                    channel,
                    status,
                    external_id,
                    external_url,
                    message,
                    now_iso(),
                ),
            )

    def list_publications(self, paper_id: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT channel, status, external_id, external_url, message,
                          updated_at
                   FROM publications WHERE paper_id=? ORDER BY channel""",
                (paper_id,),
            ).fetchall()
        return [dict(row) for row in rows]
