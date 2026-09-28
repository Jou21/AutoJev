from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from autojev.domain.models import (
    ExplorationRequest,
    ExplorationStatus,
    Provider,
    ProviderModel,
    TestScenario,
    utc_now,
)

from .secrets import SecretCipher


class NotFoundError(LookupError):
    pass


class SQLiteRepository:
    def __init__(self, database_path: Path, cipher: SecretCipher) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.cipher = cipher
        self._lock = RLock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS providers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            api_key_encrypted TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_models (
            provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
            model_id TEXT NOT NULL,
            label TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(provider_id, model_id)
        );
        CREATE TABLE IF NOT EXISTS explorations (
            id TEXT PRIMARY KEY,
            app_url TEXT NOT NULL,
            agent_provider_id TEXT NOT NULL,
            agent_model_id TEXT NOT NULL,
            auxiliary_provider_id TEXT NOT NULL,
            auxiliary_model_id TEXT NOT NULL,
            options_json TEXT NOT NULL,
            status TEXT NOT NULL,
            current_url TEXT,
            steps_completed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            report_path TEXT,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            cost REAL,
            cost_known INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS exploration_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exploration_id TEXT NOT NULL REFERENCES explorations(id) ON DELETE CASCADE,
            level TEXT NOT NULL,
            type TEXT NOT NULL,
            message TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exploration_id TEXT NOT NULL REFERENCES explorations(id) ON DELETE CASCADE,
            step_number INTEGER NOT NULL,
            decision_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scenarios (
            id TEXT NOT NULL,
            exploration_id TEXT NOT NULL REFERENCES explorations(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(exploration_id, id)
        );
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exploration_id TEXT NOT NULL REFERENCES explorations(id) ON DELETE CASCADE,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS screenshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exploration_id TEXT NOT NULL REFERENCES explorations(id) ON DELETE CASCADE,
            scenario_id TEXT,
            relative_path TEXT NOT NULL,
            label TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_exploration_events ON exploration_events(exploration_id, id);
        CREATE INDEX IF NOT EXISTS idx_actions_exploration ON actions(exploration_id, step_number);
        CREATE INDEX IF NOT EXISTS idx_scenarios_exploration ON scenarios(exploration_id, sequence);
        """
        with self._lock, self._connect() as connection:
            connection.executescript(schema)
            for column, definition in (
                ("prompt_tokens", "INTEGER NOT NULL DEFAULT 0"),
                ("completion_tokens", "INTEGER NOT NULL DEFAULT 0"),
                ("total_tokens", "INTEGER NOT NULL DEFAULT 0"),
                ("cost", "REAL"),
                ("cost_known", "INTEGER NOT NULL DEFAULT 0"),
            ):
                try:
                    connection.execute(f"ALTER TABLE explorations ADD COLUMN {column} {definition}")
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            connection.execute(
                """UPDATE explorations
                   SET status = ?, finished_at = ?, error = ?
                   WHERE status IN (?, ?, ?, ?)""",
                (
                    ExplorationStatus.FAILED,
                    utc_now(),
                    "Backend restarted before this exploration finished",
                    ExplorationStatus.QUEUED,
                    ExplorationStatus.RUNNING,
                    ExplorationStatus.WAITING_INPUT,
                    ExplorationStatus.POSTPROCESSING,
                ),
            )

    @staticmethod
    def _normalise_base_url(value: str) -> str:
        return value.strip().rstrip("/")

    def create_provider(
        self,
        name: str,
        base_url: str,
        api_key: str | None,
        enabled: bool,
        models: Iterable[ProviderModel],
    ) -> Provider:
        provider_id = str(uuid.uuid4())
        now = utc_now()
        encrypted = self.cipher.encrypt(api_key)
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO providers VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    provider_id,
                    name.strip(),
                    self._normalise_base_url(base_url),
                    encrypted,
                    int(enabled),
                    now,
                    now,
                ),
            )
            self._replace_models(connection, provider_id, models)
        return self.get_provider(provider_id)

    @staticmethod
    def _replace_models(
        connection: sqlite3.Connection,
        provider_id: str,
        models: Iterable[ProviderModel],
    ) -> None:
        connection.execute("DELETE FROM provider_models WHERE provider_id = ?", (provider_id,))
        seen: set[str] = set()
        for model in models:
            model_id = model.id.strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            connection.execute(
                "INSERT INTO provider_models VALUES (?, ?, ?, ?)",
                (provider_id, model_id, model.label.strip() or model_id, int(model.enabled)),
            )

    def update_provider(
        self,
        provider_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        replace_api_key: bool = False,
        enabled: bool | None = None,
        models: list[ProviderModel] | None = None,
    ) -> Provider:
        current = self._provider_row(provider_id)
        encrypted = current["api_key_encrypted"]
        if replace_api_key:
            encrypted = self.cipher.encrypt(api_key)
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE providers SET name = ?, base_url = ?, api_key_encrypted = ?,
                   enabled = ?, updated_at = ? WHERE id = ?""",
                (
                    name.strip() if name is not None else current["name"],
                    self._normalise_base_url(base_url)
                    if base_url is not None
                    else current["base_url"],
                    encrypted,
                    int(enabled) if enabled is not None else current["enabled"],
                    utc_now(),
                    provider_id,
                ),
            )
            if models is not None:
                self._replace_models(connection, provider_id, models)
        return self.get_provider(provider_id)

    def replace_provider_models(
        self, provider_id: str, models: list[ProviderModel]
    ) -> Provider:
        self._provider_row(provider_id)
        with self._lock, self._connect() as connection:
            self._replace_models(connection, provider_id, models)
            connection.execute(
                "UPDATE providers SET updated_at = ? WHERE id = ?", (utc_now(), provider_id)
            )
        return self.get_provider(provider_id)

    def _provider_row(self, provider_id: str) -> sqlite3.Row:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM providers WHERE id = ?", (provider_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Provider {provider_id} was not found")
        return row

    def get_provider_secret(self, provider_id: str) -> str | None:
        return self.cipher.decrypt(self._provider_row(provider_id)["api_key_encrypted"])

    def get_provider(self, provider_id: str) -> Provider:
        row = self._provider_row(provider_id)
        with self._lock, self._connect() as connection:
            model_rows = connection.execute(
                "SELECT * FROM provider_models WHERE provider_id = ? ORDER BY label, model_id",
                (provider_id,),
            ).fetchall()
        return Provider(
            id=row["id"],
            name=row["name"],
            base_url=row["base_url"],
            enabled=bool(row["enabled"]),
            has_api_key=bool(row["api_key_encrypted"]),
            models=[
                ProviderModel(
                    id=model["model_id"], label=model["label"], enabled=bool(model["enabled"])
                )
                for model in model_rows
            ],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_providers(self) -> list[Provider]:
        with self._lock, self._connect() as connection:
            ids = [row["id"] for row in connection.execute(
                "SELECT id FROM providers ORDER BY created_at DESC"
            ).fetchall()]
        return [self.get_provider(provider_id) for provider_id in ids]

    def delete_provider(self, provider_id: str) -> None:
        self._provider_row(provider_id)
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM providers WHERE id = ?", (provider_id,))

    def create_exploration(self, request: ExplorationRequest) -> str:
        exploration_id = str(uuid.uuid4())
        options = {
            key: getattr(request.options, key)
            for key in request.options.__dataclass_fields__
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO explorations (
                    id, app_url, agent_provider_id, agent_model_id,
                    auxiliary_provider_id, auxiliary_model_id, options_json,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    exploration_id,
                    request.app_url,
                    request.agent.provider_id,
                    request.agent.model_id,
                    request.auxiliary_llm.provider_id,
                    request.auxiliary_llm.model_id,
                    json.dumps(options),
                    ExplorationStatus.QUEUED,
                    utc_now(),
                ),
            )
        return exploration_id

    _exploration_fields = {
        "status",
        "current_url",
        "steps_completed",
        "started_at",
        "finished_at",
        "cancel_requested",
        "error",
        "report_path",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost",
        "cost_known",
    }

    def update_exploration(self, exploration_id: str, **values: Any) -> None:
        invalid = set(values) - self._exploration_fields
        if invalid:
            raise ValueError(f"Invalid exploration fields: {', '.join(sorted(invalid))}")
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        params = [int(value) if isinstance(value, bool) else value for value in values.values()]
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE explorations SET {assignments} WHERE id = ?",
                (*params, exploration_id),
            )
        if cursor.rowcount == 0:
            raise NotFoundError(f"Exploration {exploration_id} was not found")

    def request_cancel(self, exploration_id: str) -> None:
        item = self.get_exploration(exploration_id, include_details=False)
        if item["status"] not in {
            ExplorationStatus.QUEUED,
            ExplorationStatus.RUNNING,
            ExplorationStatus.WAITING_INPUT,
            ExplorationStatus.POSTPROCESSING,
        }:
            return
        self.update_exploration(exploration_id, cancel_requested=True)

    def is_cancel_requested(self, exploration_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM explorations WHERE id = ?", (exploration_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Exploration {exploration_id} was not found")
        return bool(row["cancel_requested"])

    def add_event(
        self,
        exploration_id: str,
        level: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO exploration_events
                   (exploration_id, level, type, message, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    exploration_id,
                    level,
                    event_type,
                    message,
                    json.dumps(payload or {}, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def add_action(
        self,
        exploration_id: str,
        step_number: int,
        decision: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO actions
                   (exploration_id, step_number, decision_json, result_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    exploration_id,
                    step_number,
                    json.dumps(decision, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def add_scenario(
        self, exploration_id: str, sequence: int, scenario: TestScenario
    ) -> None:
        data = {
            key: (value.value if hasattr(value, "value") else value)
            for key, value in asdict(scenario).items()
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO scenarios VALUES (?, ?, ?, ?, ?)",
                (
                    scenario.id,
                    exploration_id,
                    sequence,
                    json.dumps(data, ensure_ascii=False),
                    scenario.created_at,
                ),
            )

    def add_issue(self, exploration_id: str, issue: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO issues (exploration_id, data_json, created_at) VALUES (?, ?, ?)",
                (exploration_id, json.dumps(issue, ensure_ascii=False), utc_now()),
            )

    def add_screenshot(
        self,
        exploration_id: str,
        relative_path: str,
        label: str,
        scenario_id: str | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO screenshots
                   (exploration_id, scenario_id, relative_path, label, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (exploration_id, scenario_id, relative_path, label, utc_now()),
            )

    @staticmethod
    def _row_to_exploration(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "app_url": row["app_url"],
            "agent": {
                "provider_id": row["agent_provider_id"],
                "model_id": row["agent_model_id"],
            },
            "auxiliary_llm": {
                "provider_id": row["auxiliary_provider_id"],
                "model_id": row["auxiliary_model_id"],
            },
            "options": json.loads(row["options_json"]),
            "status": row["status"],
            "current_url": row["current_url"],
            "steps_completed": row["steps_completed"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "cancel_requested": bool(row["cancel_requested"]),
            "error": row["error"],
            "report_available": bool(row["report_path"]),
            "usage": {
                "prompt_tokens": row["prompt_tokens"],
                "completion_tokens": row["completion_tokens"],
                "total_tokens": row["total_tokens"],
                "cost": row["cost"],
                "cost_known": bool(row["cost_known"]),
            },
        }

    def get_exploration(
        self, exploration_id: str, include_details: bool = True
    ) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM explorations WHERE id = ?", (exploration_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"Exploration {exploration_id} was not found")
            result = self._row_to_exploration(row)
            if not include_details:
                return result
            result["events"] = [
                {
                    "id": event["id"],
                    "level": event["level"],
                    "type": event["type"],
                    "message": event["message"],
                    "payload": json.loads(event["payload_json"]),
                    "created_at": event["created_at"],
                }
                for event in connection.execute(
                    "SELECT * FROM exploration_events WHERE exploration_id = ? ORDER BY id",
                    (exploration_id,),
                ).fetchall()
            ]
            result["actions"] = [
                {
                    "id": action["id"],
                    "step_number": action["step_number"],
                    "decision": json.loads(action["decision_json"]),
                    "result": json.loads(action["result_json"]),
                    "created_at": action["created_at"],
                }
                for action in connection.execute(
                    "SELECT * FROM actions WHERE exploration_id = ? ORDER BY step_number",
                    (exploration_id,),
                ).fetchall()
            ]
            result["scenarios"] = [
                json.loads(item["data_json"])
                for item in connection.execute(
                    "SELECT data_json FROM scenarios WHERE exploration_id = ? ORDER BY sequence",
                    (exploration_id,),
                ).fetchall()
            ]
            result["issues"] = [
                json.loads(item["data_json"])
                for item in connection.execute(
                    "SELECT data_json FROM issues WHERE exploration_id = ? ORDER BY id",
                    (exploration_id,),
                ).fetchall()
            ]
            result["screenshots"] = [
                {
                    "id": item["id"],
                    "scenario_id": item["scenario_id"],
                    "path": item["relative_path"],
                    "label": item["label"],
                    "created_at": item["created_at"],
                }
                for item in connection.execute(
                    "SELECT * FROM screenshots WHERE exploration_id = ? ORDER BY id",
                    (exploration_id,),
                ).fetchall()
            ]
        counts = {status: 0 for status in ("PASS", "FAIL", "BLOCKED", "SKIPPED")}
        for scenario in result["scenarios"]:
            if scenario.get("status") in counts:
                counts[scenario["status"]] += 1
        result["summary"] = {
            "total_scenarios": len(result["scenarios"]),
            **{key.lower(): value for key, value in counts.items()},
            "issues": len(result["issues"]),
        }
        return result

    def list_explorations(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT e.*,
                    (SELECT COUNT(*) FROM actions a WHERE a.exploration_id = e.id) AS actions_count,
                    (SELECT COUNT(*) FROM issues i WHERE i.exploration_id = e.id) AS issues_count,
                    (SELECT COUNT(*) FROM scenarios s WHERE s.exploration_id = e.id) AS scenarios_count,
                    (SELECT COUNT(*) FROM scenarios s WHERE s.exploration_id = e.id
                       AND json_extract(s.data_json, '$.status') = 'PASS') AS pass_count,
                    (SELECT COUNT(*) FROM scenarios s WHERE s.exploration_id = e.id
                       AND json_extract(s.data_json, '$.status') = 'FAIL') AS fail_count,
                    (SELECT COUNT(*) FROM scenarios s WHERE s.exploration_id = e.id
                       AND json_extract(s.data_json, '$.status') = 'BLOCKED') AS blocked_count,
                    (SELECT COUNT(*) FROM scenarios s WHERE s.exploration_id = e.id
                       AND json_extract(s.data_json, '$.status') = 'SKIPPED') AS skipped_count,
                    (SELECT COUNT(DISTINCT json_extract(a.result_json, '$.after_url'))
                       FROM actions a WHERE a.exploration_id = e.id) AS visited_count,
                    (SELECT relative_path FROM screenshots ss
                       WHERE ss.exploration_id = e.id ORDER BY ss.id DESC LIMIT 1) AS latest_screenshot_path,
                    (SELECT label FROM screenshots ss
                       WHERE ss.exploration_id = e.id ORDER BY ss.id DESC LIMIT 1) AS latest_screenshot_label,
                    (SELECT created_at FROM exploration_events ev
                       WHERE ev.exploration_id = e.id ORDER BY ev.id DESC LIMIT 1) AS latest_event_at,
                    (SELECT type FROM exploration_events ev
                       WHERE ev.exploration_id = e.id ORDER BY ev.id DESC LIMIT 1) AS latest_event_type,
                    (SELECT message FROM exploration_events ev
                       WHERE ev.exploration_id = e.id ORDER BY ev.id DESC LIMIT 1) AS latest_event_message,
                    (SELECT type FROM exploration_events ev
                       WHERE ev.exploration_id = e.id
                         AND ev.type IN (
                           'limit_reached', 'coverage_partial',
                           'coverage_scope_not_found', 'coverage_complete',
                           'coverage_complete_with_restrictions',
                           'coverage_stalled', 'failed', 'cancelled',
                           'browser_closed'
                         )
                       ORDER BY ev.id DESC LIMIT 1) AS completion_event_type,
                    (SELECT message FROM exploration_events ev
                       WHERE ev.exploration_id = e.id
                         AND ev.type IN (
                           'limit_reached', 'coverage_partial',
                           'coverage_scope_not_found', 'coverage_complete',
                           'coverage_complete_with_restrictions',
                           'coverage_stalled', 'failed', 'cancelled',
                           'browser_closed'
                         )
                       ORDER BY ev.id DESC LIMIT 1) AS completion_event_message,
                    (SELECT payload_json FROM exploration_events ev
                       WHERE ev.exploration_id = e.id
                         AND ev.type IN (
                           'limit_reached', 'coverage_partial',
                           'coverage_scope_not_found', 'coverage_complete',
                           'coverage_complete_with_restrictions',
                           'coverage_stalled', 'failed', 'cancelled',
                           'browser_closed'
                         )
                       ORDER BY ev.id DESC LIMIT 1) AS completion_event_payload
                   FROM explorations e ORDER BY e.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = self._row_to_exploration(row)
            item["counters"] = {
                "actions": int(row["actions_count"] or 0),
                "issues": int(row["issues_count"] or 0),
                "scenarios": int(row["scenarios_count"] or 0),
                "pages_visited": max(int(row["visited_count"] or 0), 1),
            }
            item["summary"] = {
                "total_scenarios": int(row["scenarios_count"] or 0),
                "pass": int(row["pass_count"] or 0),
                "fail": int(row["fail_count"] or 0),
                "blocked": int(row["blocked_count"] or 0),
                "skipped": int(row["skipped_count"] or 0),
                "issues": int(row["issues_count"] or 0),
            }
            item["updated_at"] = (
                row["latest_event_at"]
                or row["finished_at"]
                or row["started_at"]
                or row["created_at"]
            )
            item["current_action"] = (
                row["latest_event_message"]
                if row["latest_event_type"] == "action_started"
                else None
            )
            item["completion_event"] = (
                {
                    "type": row["completion_event_type"],
                    "message": row["completion_event_message"],
                    "payload": json.loads(row["completion_event_payload"] or "{}"),
                }
                if row["completion_event_type"]
                else None
            )
            item["screenshots"] = (
                [
                    {
                        "id": None,
                        "scenario_id": None,
                        "path": row["latest_screenshot_path"],
                        "label": row["latest_screenshot_label"] or "Latest screenshot",
                        "created_at": item["updated_at"],
                    }
                ]
                if row["latest_screenshot_path"]
                else []
            )
            results.append(item)
        return results

    def get_report_path(self, exploration_id: str) -> str | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT report_path FROM explorations WHERE id = ?", (exploration_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError(f"Exploration {exploration_id} was not found")
        return row["report_path"]
