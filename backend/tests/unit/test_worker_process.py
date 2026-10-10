"""Воркер — отдельный процесс: он должен сам загружать все модели (иначе внешние ключи не разрешатся)."""

import os
import subprocess
import sys


def test_worker_process_can_resolve_all_tables() -> None:
    code = (
        "import app.worker\n"
        "from sqlalchemy.orm import configure_mappers\n"
        "from app.platform.base import Base\n"
        "configure_mappers()\n"
        "tables = [t.name for t in Base.metadata.sorted_tables]\n"  # падает, если FK ведёт на незагруженную таблицу
        "assert 'users' in tables and 'ai_job' in tables and 'document' in tables, tables\n"
        "print('ok', len(tables))\n"
    )
    env = {**os.environ, "DATABASE_URL": "postgresql+psycopg://x:y@localhost/z"}
    r = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60
    )
    assert r.returncode == 0 and r.stdout.startswith("ok"), r.stderr[-2000:]
