from pathlib import Path

from .database import get_db


def _table_columns(db, table):
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _ensure_column(db, table, column_name, definition):
    columns = _table_columns(db, table)
    if column_name not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column_name} {definition}")


def _harden_legacy_schema(db):
    if _table_columns(db, "users"):
        _ensure_column(db, "users", "display_name", "TEXT")
        _ensure_column(db, "users", "bio", "TEXT")
        _ensure_column(db, "users", "avatar_path", "TEXT")
        _ensure_column(db, "users", "last_seen", "DATETIME")
        _ensure_column(db, "users", "is_online", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(db, "users", "created_at", "DATETIME")
        db.execute(
            "UPDATE users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
        )

    if _table_columns(db, "messages"):
        _ensure_column(db, "messages", "attachment_path", "TEXT")
        _ensure_column(db, "messages", "status", "TEXT NOT NULL DEFAULT 'sent'")
        _ensure_column(db, "messages", "delivered_at", "DATETIME")
        _ensure_column(db, "messages", "read_at", "DATETIME")
        _ensure_column(db, "messages", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(db, "messages", "updated_at", "DATETIME")
        db.execute(
            "UPDATE messages SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL"
        )
        columns = _table_columns(db, "messages")
        if "created_at" not in columns:
            _ensure_column(db, "messages", "created_at", "DATETIME")
            db.execute(
                "UPDATE messages SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
            )
        columns = _table_columns(db, "messages")
        if "timestamp" in columns:
            db.execute(
                "UPDATE messages SET created_at = timestamp WHERE created_at IS NULL"
            )
        db.execute(
            "UPDATE messages SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
        )

    if _table_columns(db, "password_reset_tokens"):
        _ensure_column(db, "password_reset_tokens", "token_hash", "TEXT")

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_receiver_status ON messages (receiver_id, status)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_target ON audit_logs (target_type, target_id, created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_hash ON password_reset_tokens (token_hash, used_at, expires_at)"
    )


def run_sql_migrations(app):
    migrations_dir = Path(app.root_path) / "sql_migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL UNIQUE,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {row["filename"] for row in db.execute("SELECT filename FROM schema_migrations")}
    for migration_path in sorted(migrations_dir.glob("*.sql")):
        if migration_path.name in applied:
            continue
        sql = migration_path.read_text(encoding="utf-8")
        statements = [stmt.strip() for stmt in sql.split(";") if stmt.strip()]
        for statement in statements:
            try:
                db.execute(statement)
            except Exception as exc:
                message = str(exc).lower()
                statement_type = statement.lower()
                if "duplicate column name" in message and statement_type.startswith(
                    "alter table"
                ):
                    continue
                if "no such column" in message and statement_type.startswith("create index"):
                    continue
                raise
        db.execute(
            "INSERT INTO schema_migrations (filename) VALUES (?)",
            (migration_path.name,),
        )
    _harden_legacy_schema(db)
    db.commit()
