"""
Unit Tests for Database Operations
"""

import pytest
from pathlib import Path
from config.settings import settings
from database.db import init_db
from database.models import (
    upsert_user,
    record_task_start,
    record_task_complete,
    register_file_cache,
    get_file_cache,
    get_user_stats,
    get_user_recent_tasks
)


@pytest.mark.asyncio
async def test_database_crud(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test_opt.db"
    monkeypatch.setattr(settings, "DB_PATH", test_db)

    await init_db()
    assert test_db.exists()

    user_id = 999999
    await upsert_user(user_id, "testuser", "Tester")

    task_id = "test_task_123"
    await record_task_start(
        task_id=task_id,
        user_id=user_id,
        file_name="demo.mp4",
        input_size=10485760,
        duration=10.0,
        action="optimize_auto"
    )

    await record_task_complete(
        task_id=task_id,
        output_size=2097152,
        elapsed_time=3.5
    )

    # File cache separate deletion tests
    await register_file_cache(task_id, user_id, "/tmp/in.mp4", "/tmp/out.mp4")
    cache = await get_file_cache(task_id)
    assert cache is not None
    assert cache["input_path"] == "/tmp/in.mp4"
    assert cache["input_deleted"] == 0
    assert cache["output_deleted"] == 0

    from database.models import mark_input_file_deleted, mark_output_file_deleted
    await mark_input_file_deleted(task_id)
    cache_in_del = await get_file_cache(task_id)
    assert cache_in_del["input_deleted"] == 1
    assert cache_in_del["output_deleted"] == 0

    await mark_output_file_deleted(task_id)
    cache_out_del = await get_file_cache(task_id)
    assert cache_out_del["output_deleted"] == 1
    assert cache_out_del["deleted"] == 1

    # Stats
    stats = await get_user_stats(user_id)
    assert stats["total_tasks"] == 1
    assert stats["successful_tasks"] == 1
    assert stats["total_input_bytes"] == 10485760
    assert stats["total_output_bytes"] == 2097152

    # History
    history = await get_user_recent_tasks(user_id, limit=5)
    assert len(history) == 1
    assert history[0]["task_id"] == task_id

    # Test get_user_cached_files and get_single_cached_file with physical files
    file_a = tmp_path / "vid_a.mp4"
    file_a.write_bytes(b"0" * 1024)
    file_b = tmp_path / "vid_b.mp4"
    file_b.write_bytes(b"1" * 2048)

    task_id2 = "test_task_456"
    await register_file_cache(task_id2, user_id, str(file_a), str(file_b))

    from database.models import get_user_cached_files, get_single_cached_file
    cached = await get_user_cached_files(user_id)
    assert len(cached) == 2
    types = [c["file_type"] for c in cached]
    assert "original" in types
    assert "processed" in types

    single_orig = await get_single_cached_file(user_id, task_id2, "original")
    assert single_orig is not None
    assert single_orig["name"] == "vid_a.mp4"
    assert single_orig["size"] == 1024

    single_opt = await get_single_cached_file(user_id, task_id2, "processed")
    assert single_opt is not None
    assert single_opt["name"] == "vid_b.mp4"
    assert single_opt["size"] == 2048


@pytest.mark.asyncio
async def test_sqlite_wal_pragmas(tmp_path: Path, monkeypatch):
    """Verifies that SQLite is configured with WAL journal mode and NORMAL synchronous."""
    from database.db import get_database_pragmas

    test_db = tmp_path / "wal_test.db"
    monkeypatch.setattr(settings, "DB_PATH", test_db)

    await init_db()
    pragmas = await get_database_pragmas(test_db)
    assert pragmas["journal_mode"] == "WAL"
    # synchronous = 1 corresponds to NORMAL in SQLite
    assert pragmas["synchronous"] in ("1", "NORMAL")


@pytest.mark.asyncio
async def test_startup_task_reconciliation_simulated_crash(tmp_path: Path, monkeypatch):
    """Verifies that in-flight unfinished tasks transition to 'interrupted' upon startup reconciliation."""
    import aiosqlite
    from database.db import reconcile_interrupted_tasks

    test_db = tmp_path / "crash_test.db"
    monkeypatch.setattr(settings, "DB_PATH", test_db)

    await init_db()

    # Seed tasks with unfinished states simulating crash mid-operation
    async with aiosqlite.connect(test_db) as db:
        await db.execute("""
            INSERT INTO tasks (task_id, user_id, file_name, input_size, status)
            VALUES 
                ('t_proc', 101, 'video1.mp4', 1000, 'processing'),
                ('t_opt', 102, 'video2.mp4', 2000, 'optimizing'),
                ('t_dl', 103, 'video3.mp4', 3000, 'downloading'),
                ('t_comp', 104, 'video4.mp4', 4000, 'completed')
        """)
        await db.commit()

    # Reconcile on startup
    reconciled = await reconcile_interrupted_tasks()
    reconciled_ids = [t["task_id"] for t in reconciled]

    assert "t_proc" in reconciled_ids
    assert "t_opt" in reconciled_ids
    assert "t_dl" in reconciled_ids
    assert "t_comp" not in reconciled_ids

    # Verify database state after recovery
    async with aiosqlite.connect(test_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT task_id, status FROM tasks") as cursor:
            rows = {r["task_id"]: r["status"] for r in await cursor.fetchall()}

    assert rows["t_proc"] == "interrupted"
    assert rows["t_opt"] == "interrupted"
    assert rows["t_dl"] == "interrupted"
    assert rows["t_comp"] == "completed"


@pytest.mark.asyncio
async def test_media_files_and_file_cache_preserved_after_crash_recovery(tmp_path: Path, monkeypatch):
    """Verifies that media files and file_cache pointers are retained during crash recovery."""
    import aiosqlite
    from database.db import reconcile_interrupted_tasks
    from database.models import get_user_cached_files

    test_db = tmp_path / "retention_test.db"
    monkeypatch.setattr(settings, "DB_PATH", test_db)

    await init_db()

    # Create dummy in-flight file
    in_file = tmp_path / "crashed_in.mp4"
    in_file.write_bytes(b"INPUT_CONTENT_DATA")

    async with aiosqlite.connect(test_db) as db:
        await db.execute("""
            INSERT INTO tasks (task_id, user_id, file_name, input_size, status)
            VALUES ('task_reboot', 555, 'crashed_in.mp4', 18, 'processing')
        """)
        await db.execute("""
            INSERT INTO file_cache (task_id, user_id, input_path, output_path, storage_type)
            VALUES ('task_reboot', 555, ?, '', 'converted')
        """, (str(in_file),))
        await db.commit()

    # Execute recovery reconciliation
    await reconcile_interrupted_tasks()

    # 1. Assert disk file was NOT deleted
    assert in_file.exists()
    assert in_file.read_bytes() == b"INPUT_CONTENT_DATA"

    # 2. Assert file_cache row is intact and visible in user cached files
    cached_files = await get_user_cached_files(555)
    assert len(cached_files) == 1
    assert cached_files[0]["task_id"] == "task_reboot"
    assert cached_files[0]["name"] == "crashed_in.mp4"
