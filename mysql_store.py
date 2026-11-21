"""MySQL persistence for the authenticated web application."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone

import pymysql
from pymysql.cursors import DictCursor


def mysql_settings() -> dict:
    settings = {
        "host": os.getenv("OSINT_DB_HOST", "").strip(),
        "port": int(os.getenv("OSINT_DB_PORT", "3306")),
        "user": os.getenv("OSINT_DB_USER", "").strip(),
        "password": os.getenv("OSINT_DB_PASSWORD", ""),
        "database": os.getenv("OSINT_DB_NAME", "osint").strip(),
    }
    if not all(settings.values()):
        raise ValueError("MySQL 上线模式需要配置数据库地址、名称、用户和密码。")
    ca = os.getenv("OSINT_DB_SSL_CA", "").strip()
    if ca:
        settings.update(ssl_ca=ca, ssl_verify_cert=True, ssl_verify_identity=True)
    return settings


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _db_time(iso_time):
    return datetime.fromisoformat(iso_time).astimezone(timezone.utc).replace(tzinfo=None)


def _api_time(value):
    return value.replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds")


class MySQLStore:
    def __init__(self, settings):
        self.settings = settings

    @contextmanager
    def cursor(self):
        with pymysql.connect(
            **self.settings, charset="utf8mb4", cursorclass=DictCursor,
            connect_timeout=5, read_timeout=10, write_timeout=10,
            init_command="SET SESSION time_zone = '+00:00'",
        ) as conn:
            with conn.cursor() as cursor:
                yield cursor
            conn.commit()

    def check_schema(self):
        with self.cursor() as cursor:
            cursor.execute("SELECT MAX(version) AS version FROM osint_schema_versions")
            if cursor.fetchone()["version"] != 2:
                raise RuntimeError("MySQL 数据库结构不匹配，请先执行项目数据库初始化脚本。")

    def healthy(self):
        with self.cursor() as cursor:
            cursor.execute("SELECT MAX(version) AS version FROM osint_schema_versions")
            return cursor.fetchone()["version"] == 2

    def recover_interrupted_runs(self):
        with self.cursor() as cursor:
            cursor.execute("""
                UPDATE osint_runs AS r
                LEFT JOIN osint_run_checkpoints AS c ON c.run_id = r.id
                SET r.status = CASE
                    WHEN JSON_UNQUOTE(JSON_EXTRACT(c.payload, '$.status')) = 'done' THEN 'done'
                    ELSE 'error'
                END
                WHERE r.status NOT IN ('done', 'error', 'cancelled')
            """)

    def create_user(self, username, password_hash, digest, expires_at, previous_digest=None):
        with self.cursor() as cursor:
            cursor.execute(
                "INSERT INTO osint_users (username, password_hash) VALUES (%s, %s)",
                (username, password_hash),
            )
            user_id = cursor.lastrowid
            cursor.execute("INSERT INTO travel_accounts(owner_id) VALUES (%s)", (user_id,))
            if previous_digest:
                cursor.execute("DELETE FROM osint_sessions WHERE token_digest = %s", (previous_digest,))
            cursor.execute(
                "INSERT INTO osint_sessions (token_digest, user_id, expires_at) VALUES (%s, %s, %s)",
                (digest, user_id, expires_at),
            )
        return {"id": user_id, "username": username}

    def get_credentials(self, username):
        with self.cursor() as cursor:
            cursor.execute(
                "SELECT id, username, password_hash FROM osint_users WHERE username = %s",
                (username,),
            )
            return cursor.fetchone()

    def issue_session(self, user_id, digest, expires_at, previous_digest):
        with self.cursor() as cursor:
            if previous_digest:
                cursor.execute("DELETE FROM osint_sessions WHERE token_digest = %s", (previous_digest,))
            cursor.execute("DELETE FROM osint_sessions WHERE expires_at <= UTC_TIMESTAMP(6) LIMIT 100")
            cursor.execute(
                "INSERT INTO osint_sessions (token_digest, user_id, expires_at) VALUES (%s, %s, %s)",
                (digest, user_id, expires_at),
            )

    def session_user(self, digest):
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.username
                FROM osint_sessions AS s JOIN osint_users AS u ON u.id = s.user_id
                WHERE s.token_digest = %s AND s.expires_at > UTC_TIMESTAMP(6)
            """, (digest,))
            return cursor.fetchone()

    def revoke_session(self, digest):
        with self.cursor() as cursor:
            cursor.execute("DELETE FROM osint_sessions WHERE token_digest = %s", (digest,))

    def rate_limit(self, scope, identity, seconds, limit):
        now = int(datetime.now(timezone.utc).timestamp())
        offset = 8 * 3600
        window = (now + offset) // seconds
        bucket = hashlib.sha256(_json([scope, identity, window]).encode("utf-8")).digest()
        expires_at = datetime.fromtimestamp((window + 1) * seconds - offset, timezone.utc).replace(tzinfo=None)
        with self.cursor() as cursor:
            cursor.execute("""
                INSERT INTO osint_rate_limits (bucket, hits, expires_at)
                VALUES (%s, 1, %s) ON DUPLICATE KEY UPDATE hits = LEAST(hits + 1, %s)
            """, (bucket, expires_at, limit + 1))
            cursor.execute("SELECT hits FROM osint_rate_limits WHERE bucket = %s", (bucket,))
            allowed = cursor.fetchone()["hits"] <= limit
        if now % 19 == 0:
            with self.cursor() as cursor:
                cursor.execute("DELETE FROM osint_rate_limits WHERE expires_at <= UTC_TIMESTAMP(6) LIMIT 100")
        return allowed

    def create_run(self, owner_id, state):
        with self.cursor() as cursor:
            self.insert_run(cursor,owner_id,state)

    @staticmethod
    def insert_run(cursor,owner_id,state):
        cursor.execute("""
            INSERT INTO osint_runs (id, owner_id, task, model_id, status, execution_mode,
                                    search_strategy, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            state["id"], owner_id, state["task"], state["model_id"], state["status"],
            state["execution_mode"], state["search_strategy"],
            _db_time(state["created_at"]), _db_time(state["updated_at"]),
        ))

    @staticmethod
    def _format_run(row):
        for key in ("created_at", "updated_at"):
            row[key] = _api_time(row[key])
        return row

    def list_runs(self, owner_id):
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT id, task, model_id, status, execution_mode, search_strategy,
                       report_count, created_at, updated_at, JSON_UNQUOTE(JSON_EXTRACT(summary, '$.domain')) AS domain
                FROM osint_runs WHERE owner_id = %s ORDER BY created_at DESC, id DESC LIMIT 100
            """, (owner_id,))
            return [self._format_run(row) for row in cursor.fetchall()]

    def owned_run(self, owner_id, run_id):
        with self.cursor() as cursor:
            cursor.execute("SELECT * FROM osint_runs WHERE owner_id = %s AND id = %s", (owner_id, run_id))
            row = cursor.fetchone()
            if row:
                row["summary"] = json.loads(row["summary"]) if row["summary"] else {}
                return self._format_run(row)
        return None

    def save_run_progress(self, run_id, state, first_seq, events, summary):
        with self.cursor() as cursor:
            if events:
                cursor.executemany("""
                    INSERT INTO osint_run_events (run_id, seq, payload) VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE payload = VALUES(payload)
                """, [(run_id, seq, _json(payload)) for seq, payload in enumerate(events, first_seq)])
            cursor.execute("""
                UPDATE osint_runs SET status = %s, execution_mode = %s, search_strategy = %s,
                    updated_at = %s, report_count = %s, summary = COALESCE(%s, summary)
                WHERE id = %s
            """, (
                state["status"], state["execution_mode"], state["search_strategy"],
                _db_time(state["updated_at"]), state["report_count"],
                _json(summary) if summary is not None else None, run_id,
            ))

    def load_events(self, run_id):
        with self.cursor() as cursor:
            cursor.execute("SELECT payload FROM osint_run_events WHERE run_id = %s ORDER BY seq", (run_id,))
            return [json.loads(row["payload"]) for row in cursor.fetchall()]

    def save_checkpoint(self, run_id, payload):
        with self.cursor() as cursor:
            cursor.execute("""
                INSERT INTO osint_run_checkpoints (run_id, payload) VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE payload = VALUES(payload)
            """, (run_id, _json(payload)))

    def load_checkpoint(self, run_id):
        with self.cursor() as cursor:
            cursor.execute("SELECT payload FROM osint_run_checkpoints WHERE run_id = %s", (run_id,))
            row = cursor.fetchone()
            return json.loads(row["payload"]) if row else None

    def save_artifact(self, run_id, category, filename, content):
        with self.cursor() as cursor:
            cursor.execute("""
                INSERT INTO osint_run_artifacts (run_id, category, filename, content)
                VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE content = VALUES(content)
            """, (run_id, category, filename, content))

    def read_artifact(self, run_id, category, filename):
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT content FROM osint_run_artifacts WHERE run_id = %s AND category = %s AND filename = %s
            """, (run_id, category, filename))
            row = cursor.fetchone()
            return row["content"] if row else None

    def read_reports(self, run_id):
        with self.cursor() as cursor:
            cursor.execute("""
                SELECT filename, content FROM osint_run_artifacts
                WHERE run_id = %s AND category = 'reports' AND filename NOT LIKE '%%.partial.md'
            """, (run_id,))
            return {row["filename"]: row["content"] for row in cursor.fetchall()}

    def load_chat(self, run_id, key):
        with self.cursor() as cursor:
            cursor.execute(
                "SELECT payload FROM osint_report_chats WHERE run_id = %s AND chat_key = %s",
                (run_id, key),
            )
            row = cursor.fetchone()
            return json.loads(row["payload"]) if row else None

    def save_chat(self, run_id, key, payload):
        with self.cursor() as cursor:
            cursor.execute("""
                INSERT INTO osint_report_chats (run_id, chat_key, payload) VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE payload = VALUES(payload)
            """, (run_id, key, _json(payload)))
