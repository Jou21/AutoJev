"""Additive persistence for plans and manually authored CT definitions."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from copy import deepcopy

from autojev.adapters.repository import NotFoundError
from autojev.domain.models import utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_plans (
 id TEXT PRIMARY KEY, root_id TEXT NOT NULL REFERENCES explorations(id),
 revision INTEGER NOT NULL, status TEXT NOT NULL, document_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_execution_plans_root ON execution_plans(root_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_live_execution_round ON execution_plans((1)) WHERE status IN ('running', 'waiting_manual', 'stopping');
CREATE TABLE IF NOT EXISTS suite_sessions (
 session_id TEXT PRIMARY KEY REFERENCES explorations(id), root_id TEXT NOT NULL REFERENCES explorations(id),
 kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_suite_sessions_root ON suite_sessions(root_id);
CREATE TABLE IF NOT EXISTS authored_test_cases (
 root_id TEXT NOT NULL REFERENCES explorations(id), case_id TEXT NOT NULL,
 revision INTEGER NOT NULL, definition_json TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(root_id, case_id)
);
CREATE TABLE IF NOT EXISTS test_case_catalog_state (
 root_id TEXT NOT NULL REFERENCES explorations(id), case_id TEXT NOT NULL,
 archived INTEGER NOT NULL CHECK(archived IN (0, 1)), revision INTEGER NOT NULL,
 updated_at TEXT NOT NULL, PRIMARY KEY(root_id, case_id)
);
"""


class PlanConflict(ValueError):
    pass


class ExecutionPlanStore:
    def __init__(self, repository):
        self.repository = repository

    def get(self, identity):
        with self.repository._lock, self.repository._connect() as connection:
            row = connection.execute("SELECT document_json FROM execution_plans WHERE id = ?", (identity,)).fetchone()
        if row is None:
            raise NotFoundError("Planejamento não encontrado.")
        return json.loads(row[0])

    def session(self, identity):
        with self.repository._lock, self.repository._connect() as connection:
            row = connection.execute("SELECT root_id, kind FROM suite_sessions WHERE session_id = ?", (identity,)).fetchone()
        return dict(row) if row else None

    def execution_context(self, identity):
        with self.repository._lock, self.repository._connect() as connection:
            row = connection.execute("""SELECT json_extract(payload_json, '$.root_id') AS root_id,
                json_extract(payload_json, '$.round_id') AS round_id, json_extract(payload_json, '$.case_id') AS case_id
                FROM exploration_events WHERE exploration_id = ? AND type = 'execution_round_item'
                ORDER BY id DESC LIMIT 1""", (identity,)).fetchone()
        return dict(row) if row else None

    def sessions(self, root_id):
        with self.repository._lock, self.repository._connect() as connection:
            rows = connection.execute("SELECT session_id, kind FROM suite_sessions WHERE root_id = ?", (root_id,)).fetchall()
        return [dict(row) for row in rows]

    def register_session(self, identity, root_id, kind):
        with self.repository._lock, self.repository._connect() as connection:
            connection.execute("INSERT INTO suite_sessions VALUES (?, ?, ?)", (identity, root_id, kind))

    def list(self, root_id=None):
        with self.repository._lock, self.repository._connect() as connection:
            rows = connection.execute(
                "SELECT document_json FROM execution_plans" + (" WHERE root_id = ?" if root_id else "") + " ORDER BY created_at, id",
                (root_id,) if root_id else (),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def create(self, root_id, name, items):
        now = utc_now()
        document = dict(id=str(uuid.uuid4()), root_id=root_id, name=name, items=deepcopy(items), revision=1,
                        status="draft", created_at=now, updated_at=now, started_at=None, finished_at=None)
        with self.repository._lock, self.repository._connect() as connection:
            connection.execute("INSERT INTO execution_plans VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (document["id"], root_id, 1, "draft", json.dumps(document), now, now))
        return document

    def replace(self, document, expected_revision):
        value = deepcopy(document)
        value.update(revision=expected_revision + 1, updated_at=utc_now())
        with self.repository._lock, self.repository._connect() as connection:
            try:
                result = connection.execute(
                    "UPDATE execution_plans SET revision = ?, status = ?, document_json = ?, updated_at = ? WHERE id = ? AND revision = ?",
                    (value["revision"], value["status"], json.dumps(value), value["updated_at"], value["id"], expected_revision),
                )
            except sqlite3.IntegrityError as exc:
                raise PlanConflict("Já existe uma rodada em andamento. Finalize ou pare a rodada atual antes de iniciar outra.") from exc
            if result.rowcount != 1:
                raise PlanConflict("O planejamento mudou em outra tela. Atualize antes de salvar.")
        return value

    def cases(self, root_id):
        with self.repository._lock, self.repository._connect() as connection:
            rows = connection.execute("SELECT definition_json FROM authored_test_cases WHERE root_id = ? ORDER BY created_at, case_id", (root_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def catalog_states(self, root_id):
        with self.repository._lock, self.repository._connect() as connection:
            rows = connection.execute("SELECT case_id, archived, revision, updated_at FROM test_case_catalog_state WHERE root_id = ?", (root_id,)).fetchall()
        return {row["case_id"]: {"archived": bool(row["archived"]), "catalog_revision": row["revision"], "catalog_updated_at": row["updated_at"]} for row in rows}

    def set_archived(self, root_id, case_id, archived, expected_revision):
        # Catalog membership is separate from definitions, results and evidence.
        with self.repository._lock, self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT revision, archived, updated_at FROM test_case_catalog_state WHERE root_id = ? AND case_id = ?", (root_id, case_id)).fetchone()
            revision = row["revision"] if row else 0
            if revision != expected_revision:
                raise PlanConflict("Este teste mudou em outra tela. Atualize antes de arquivar ou restaurar.")
            if row and bool(row["archived"]) == archived:
                return {"archived": archived, "catalog_revision": revision, "catalog_updated_at": row["updated_at"]}
            now = utc_now()
            connection.execute("INSERT INTO test_case_catalog_state VALUES (?, ?, ?, ?, ?) ON CONFLICT(root_id, case_id) DO UPDATE SET archived = excluded.archived, revision = excluded.revision, updated_at = excluded.updated_at",
                               (root_id, case_id, int(archived), revision + 1, now))
        return {"archived": archived, "catalog_revision": revision + 1, "catalog_updated_at": now}

    def save_case(self, root_id, definition, initial_ids, identity=None, expected_revision=None):
        # The ID allocation and insert share one transaction, including across
        # repository instances. Existing exploration records are never edited.
        with self.repository._lock, self.repository._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if identity:
                row = connection.execute("SELECT revision FROM authored_test_cases WHERE root_id = ? AND case_id = ?", (root_id, identity)).fetchone()
                if row is None:
                    raise NotFoundError("CT manual não encontrado.")
                if row[0] != expected_revision:
                    raise PlanConflict("O CT foi alterado em outra tela. Atualize sua versão.")
                revision = row[0] + 1
            else:
                if definition.get("mode") == "automatic":
                    existing = connection.execute("SELECT definition_json FROM authored_test_cases WHERE root_id = ? AND json_extract(definition_json, '$.source_exploration_id') = ? AND json_extract(definition_json, '$.source_scenario_id') = ?",
                                                  (root_id, definition.get("source_exploration_id"), definition.get("source_scenario_id"))).fetchone()
                    if existing:
                        return json.loads(existing[0])
                ids = [*initial_ids, *(row[0] for row in connection.execute("SELECT case_id FROM authored_test_cases WHERE root_id = ?", (root_id,)))]
                numbers = [int(match[1]) for value in ids if (match := re.fullmatch(r"CT(\d+)", value))]
                identity, revision = "CT" + str(max(numbers, default=0) + 1).zfill(3), 1
            value = dict(definition, id=identity, revision=revision, mode=definition.get("mode", "manual"), updated_at=utc_now())
            if revision == 1:
                connection.execute("INSERT INTO authored_test_cases VALUES (?, ?, ?, ?, ?)",
                                   (root_id, identity, revision, json.dumps(value), value["updated_at"]))
            else:
                connection.execute("UPDATE authored_test_cases SET revision = ?, definition_json = ? WHERE root_id = ? AND case_id = ?",
                                   (revision, json.dumps(value), root_id, identity))
        return value
