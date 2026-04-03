"""SQLite state tracking for the repovore pipeline."""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path


def _now() -> str:
    return datetime.now(UTC).isoformat()


CREATE_REPOS = """
CREATE TABLE IF NOT EXISTS repos (
    project_path TEXT PRIMARY KEY,
    url TEXT,
    status TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""

CREATE_PROCESSING_STATE = """
CREATE TABLE IF NOT EXISTS processing_state (
    project_path TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    started_at TEXT,
    completed_at TEXT,
    PRIMARY KEY (project_path, stage),
    FOREIGN KEY (project_path) REFERENCES repos(project_path)
)
"""

CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    completed_at TEXT,
    config_hash TEXT,
    stages_run TEXT
)
"""

CREATE_METADATA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
)
"""

CREATE_CARDS = """
CREATE TABLE IF NOT EXISTS cards (
    project_path TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    health_score REAL,
    stars INTEGER,
    forks INTEGER,
    maintenance_level TEXT,
    verdict TEXT,
    primary_language TEXT,
    last_commit_date TEXT,
    fetched_at TEXT,
    FOREIGN KEY (project_path) REFERENCES repos(project_path)
)
"""


class Database:
    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(CREATE_REPOS)
            self._conn.execute(CREATE_PROCESSING_STATE)
            self._conn.execute(CREATE_RUNS)
            self._conn.execute(CREATE_CARDS)
            self._conn.execute(CREATE_METADATA)

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return dict(row)

    def get_or_create_repo(self, project_path: str, **kwargs: str | None) -> dict:
        now = _now()
        existing = self.get_repo(project_path)
        if existing is not None:
            return existing

        fields: dict[str, str | None] = {
            "project_path": project_path,
            "created_at": now,
            "updated_at": now,
            **kwargs,
        }
        columns = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT OR IGNORE INTO repos ({columns}) VALUES ({placeholders})",
                list(fields.values()),
            )
        return self.get_repo(project_path)  # type: ignore[return-value]

    def update_repo(self, project_path: str, **kwargs: str | None) -> None:
        if not kwargs:
            return
        kwargs["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [project_path]
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE repos SET {set_clause} WHERE project_path = ?",
                values,
            )

    def get_repo(self, project_path: str) -> dict | None:
        cursor = self._conn.execute(
            "SELECT * FROM repos WHERE project_path = ?", (project_path,)
        )
        row = cursor.fetchone()
        return self._row_to_dict(row)

    def get_repos(self) -> list[dict]:
        cursor = self._conn.execute("SELECT * FROM repos")
        return [self._row_to_dict(row) for row in cursor.fetchall()]  # type: ignore[misc]

    def update_stage_status(
        self,
        project_path: str,
        stage: str,
        status: str,
        error: str | None = None,
    ) -> None:
        now = _now()
        started_at: str | None = None
        completed_at: str | None = None
        if status == "running":
            started_at = now
        elif status in ("done", "failed"):
            completed_at = now

        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO processing_state
                    (project_path, stage, status, error, started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_path, stage) DO UPDATE SET
                    status = excluded.status,
                    error = excluded.error,
                    started_at = CASE
                        WHEN excluded.started_at IS NOT NULL THEN excluded.started_at
                        ELSE started_at
                    END,
                    completed_at = CASE
                        WHEN excluded.completed_at IS NOT NULL THEN excluded.completed_at
                        ELSE completed_at
                    END
                """,
                (project_path, stage, status, error, started_at, completed_at),
            )

    def get_repos_needing_stage(self, stage: str, force: bool = False) -> list[dict]:
        if force:
            return self.get_repos()
        cursor = self._conn.execute(
            """
            SELECT r.* FROM repos r
            WHERE r.project_path NOT IN (
                SELECT ps.project_path FROM processing_state ps
                WHERE ps.stage = ? AND ps.status = 'done'
            )
            """,
            (stage,),
        )
        return [self._row_to_dict(row) for row in cursor.fetchall()]  # type: ignore[misc]

    def get_stage_status(self, project_path: str, stage: str) -> dict | None:
        cursor = self._conn.execute(
            "SELECT * FROM processing_state WHERE project_path = ? AND stage = ?",
            (project_path, stage),
        )
        row = cursor.fetchone()
        return self._row_to_dict(row)

    def start_run(self, config_hash: str, stages: list[str]) -> int:
        now = _now()
        stages_json = json.dumps(stages)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO runs (started_at, config_hash, stages_run) VALUES (?, ?, ?)",
                (now, config_hash, stages_json),
            )
        return cursor.lastrowid  # type: ignore[return-value]

    def complete_run(self, run_id: int) -> None:
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE runs SET completed_at = ? WHERE id = ?",
                (now, run_id),
            )

    def get_run_stats(self) -> dict:
        cursor = self._conn.execute(
            """
            SELECT stage, status, COUNT(*) as count
            FROM processing_state
            GROUP BY stage, status
            """
        )
        stats: dict = {}
        for row in cursor.fetchall():
            stage = row["stage"]
            status = row["status"]
            count = row["count"]
            if stage not in stats:
                stats[stage] = {}
            stats[stage][status] = count
        return stats

    # ── Backfill helpers ────────────────────────────────────────────────────

    def get_incomplete_repos(self, all_stages: list[str]) -> list[dict]:  # type: ignore[type-arg]
        """Return repos that have any stage not 'done', with gap details.

        Each returned dict has:
          - project_path: str
          - missing_stages: list of stage names that are not done (in pipeline order)
          - earliest_missing: first stage that needs work
          - stage_statuses: dict of stage -> status for all recorded stages

        Uses a single SQL query instead of per-repo lookups.
        """
        placeholders = ",".join("?" for _ in all_stages)
        cursor = self._conn.execute(
            f"""
            SELECT r.project_path, ps.stage, ps.status
            FROM repos r
            LEFT JOIN processing_state ps
                ON r.project_path = ps.project_path
                AND ps.stage IN ({placeholders})
            ORDER BY r.project_path
            """,
            all_stages,
        )

        # Build a {project_path: {stage: status}} map from the flat result set
        repo_stages: dict[str, dict[str, str]] = {}
        for row in cursor.fetchall():
            path = row["project_path"]
            if path not in repo_stages:
                repo_stages[path] = {}
            if row["stage"] is not None:
                repo_stages[path][row["stage"]] = row["status"]

        incomplete: list[dict] = []  # type: ignore[type-arg]
        for path, recorded in repo_stages.items():
            stage_statuses: dict[str, str] = {}
            missing: list[str] = []
            for stage in all_stages:
                status = recorded.get(stage, "pending")
                stage_statuses[stage] = status
                if status != "done":
                    missing.append(stage)
            if missing:
                incomplete.append({
                    "project_path": path,
                    "missing_stages": missing,
                    "earliest_missing": missing[0],
                    "stage_statuses": stage_statuses,
                })
        return incomplete

    # ── Cards index ─────────────────────────────────────────────────────────

    def upsert_card_index(self, card_data: dict) -> None:
        """Upsert a row in the cards index from card dict fields."""
        project_path = f"{card_data['owner']}/{card_data['name']}"
        languages = card_data.get("languages") or {}
        primary_language = max(languages, key=languages.get, default=None) if languages else None
        fetched_at = card_data.get("fetched_at")
        if hasattr(fetched_at, "isoformat"):
            fetched_at = fetched_at.isoformat()
        last_commit = card_data.get("last_commit_date")
        if hasattr(last_commit, "isoformat"):
            last_commit = last_commit.isoformat()

        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO cards
                    (project_path, owner, name, description, health_score, stars, forks,
                     maintenance_level, verdict, primary_language, last_commit_date, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (project_path) DO UPDATE SET
                    description = excluded.description,
                    health_score = excluded.health_score,
                    stars = excluded.stars,
                    forks = excluded.forks,
                    maintenance_level = excluded.maintenance_level,
                    verdict = excluded.verdict,
                    primary_language = excluded.primary_language,
                    last_commit_date = excluded.last_commit_date,
                    fetched_at = excluded.fetched_at
                """,
                (
                    project_path,
                    card_data["owner"],
                    card_data["name"],
                    card_data.get("description"),
                    card_data.get("health_score"),
                    card_data.get("stars", 0),
                    card_data.get("forks", 0),
                    card_data.get("maintenance_level"),
                    card_data.get("verdict"),
                    primary_language,
                    last_commit,
                    fetched_at,
                ),
            )

    def list_cards(
        self,
        sort_by: str = "health_score",
        order: str = "desc",
        maintenance: str | None = None,
        verdict: str | None = None,
        search: str | None = None,
    ) -> list[dict]:
        """Query the cards index with sorting and filtering."""
        allowed_sorts = {
            "health_score", "stars", "last_commit_date", "maintenance_level", "name",
        }
        if sort_by not in allowed_sorts:
            sort_by = "health_score"
        if order not in ("asc", "desc"):
            order = "desc"

        clauses: list[str] = []
        params: list[str] = []

        if maintenance:
            clauses.append("maintenance_level = ?")
            params.append(maintenance)
        if verdict:
            clauses.append("verdict = ?")
            params.append(verdict)
        if search:
            clauses.append("(name LIKE ? OR owner LIKE ? OR description LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM cards {where} ORDER BY {sort_by} IS NULL, {sort_by} {order}"
        cursor = self._conn.execute(query, params)
        return [self._row_to_dict(row) for row in cursor.fetchall()]  # type: ignore[misc]

    def get_card_index(self, project_path: str) -> dict | None:
        """Fetch a single card index row."""
        cursor = self._conn.execute(
            "SELECT * FROM cards WHERE project_path = ?", (project_path,)
        )
        row = cursor.fetchone()
        return self._row_to_dict(row)

    # ── Metadata ────────────────────────────────────────────────────────────

    def set_metadata(self, key: str, value: str) -> None:
        """Set a metadata key-value pair."""
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO metadata (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT (key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def get_metadata(self, key: str) -> str | None:
        """Get a metadata value by key, or None if not set."""
        cursor = self._conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return row["value"] if row else None
