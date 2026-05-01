#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
import shutil
import sqlite3


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "database.db"
UPLOADS_PATH = ROOT / "uploads"
BACKUP_DIR = ROOT / "backups"


def backup_sqlite(db_path, backup_path):
    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(backup_path)
    with dest:
        source.backup(dest)
    source.close()
    dest.close()


def main():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_backup = BACKUP_DIR / f"database_{stamp}.db"
    uploads_backup = BACKUP_DIR / f"uploads_{stamp}"

    if DB_PATH.exists():
        backup_sqlite(str(DB_PATH), str(db_backup))
    if UPLOADS_PATH.exists():
        shutil.copytree(UPLOADS_PATH, uploads_backup)
    print(f"Backups written under: {BACKUP_DIR}")


if __name__ == "__main__":
    main()
