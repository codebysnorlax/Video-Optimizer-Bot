"""
End-to-End Integration Check for Production Overhaul and Cleanup Change:
1. Multi-domain Rotating Logging (system, ai, error)
2. Workspace Pruning (strict 5-directory ceiling + active session preservation)
3. Deliverable Promotion (Ai_Workplace -> downloads -> file_cache -> /files retrieval)
4. Crash Recovery (SQLite WAL pragmas + interrupted task reconciliation)
"""

import os
import shutil
import asyncio
import aiosqlite
import pytest
from pathlib import Path

from config.settings import settings
from config.logging import setup_logging, flush_all_logs
from core.ai.agent import prune_ai_workspaces
from bot.ai_handler import promote_ai_deliverable
from database.db import init_db, get_database_pragmas, reconcile_interrupted_tasks
from database.models import (
    record_task_start,
    get_file_cache,
    get_user_cached_files,
    register_file_cache
)


@pytest.mark.asyncio
async def test_e2e_production_overhaul_integration(tmp_path: Path, monkeypatch):
    """
    Executes a comprehensive end-to-end integration scenario across all four overhaul pillars.
    """
    # --------------------------------------------------------------------------
    # Pillar 1: Multi-domain Structured & Rotating Logging
    # --------------------------------------------------------------------------
    log_dir = tmp_path / "test_logs"
    monkeypatch.setattr(settings, "LOG_DIR", log_dir)
    monkeypatch.setattr(settings, "SYSTEM_LOG_DIR", log_dir / "system")
    monkeypatch.setattr(settings, "AI_LOG_DIR", log_dir / "ai")
    monkeypatch.setattr(settings, "ERROR_LOG_DIR", log_dir / "errors")

    loggers = setup_logging(base_log_dir=log_dir)
    sys_logger = loggers["system"]
    ai_logger = loggers["ai"]
    err_logger = loggers["errors"]

    sys_logger.info("SYSTEM_EVENT_101: Bot startup sequence initialized.")
    ai_logger.info("AI_EVENT_202: Model ReAct loop started for user=999.")
    err_logger.error("ERROR_EVENT_303: Simulated hardware glitch captured.")

    flush_all_logs()

    sys_log_file = log_dir / "system" / "system.log"
    ai_log_file = log_dir / "ai" / "ai.log"
    err_log_file = log_dir / "errors" / "error.log"

    assert sys_log_file.exists()
    assert ai_log_file.exists()
    assert err_log_file.exists()

    assert "SYSTEM_EVENT_101" in sys_log_file.read_text()
    assert "AI_EVENT_202" in ai_log_file.read_text()
    assert "ERROR_EVENT_303" in err_log_file.read_text()

    # Verify domain isolation
    assert "AI_EVENT_202" not in sys_log_file.read_text()
    assert "ERROR_EVENT_303" not in ai_log_file.read_text()

    # --------------------------------------------------------------------------
    # Pillar 2: LRU Workspace Pruning (Strict 5-directory ceiling)
    # --------------------------------------------------------------------------
    ai_workplace = tmp_path / "Ai_Workplace"
    ai_workplace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "AI_WORKPLACE_DIR", ai_workplace)

    # Create 8 session directories with simulated timestamps
    session_dirs = []
    base_time = 1700000000.0
    for i in range(8):
        s_dir = ai_workplace / f"user_{i}_task_{i}"
        s_dir.mkdir()
        (s_dir / f"output_{i}.mp4").write_text(f"content {i}")
        # Set mtime staggered: directory 0 is oldest, directory 7 is newest
        os.utime(s_dir, (base_time + i * 100, base_time + i * 100))
        session_dirs.append(s_dir)

    # Protect directory 0 (oldest) from pruning because user has an active session
    active_protected = {"user_0_task_0"}
    pruned = prune_ai_workspaces(max_workspaces=5, protected_dirs=active_protected)

    remaining_dirs = [p.name for p in ai_workplace.iterdir() if p.is_dir()]
    assert len(remaining_dirs) == 5
    assert "user_0_task_0" in remaining_dirs  # Protected
    assert "user_7_task_7" in remaining_dirs  # Newest
    assert "user_6_task_6" in remaining_dirs
    assert "user_5_task_5" in remaining_dirs
    assert "user_4_task_4" in remaining_dirs
    # Unprotected oldest directories (1, 2, 3) must be pruned
    assert "user_1_task_1" not in remaining_dirs
    assert "user_2_task_2" not in remaining_dirs
    assert "user_3_task_3" not in remaining_dirs

    # --------------------------------------------------------------------------
    # Pillar 3: Deliverable Promotion (Ai_Workplace -> downloads -> file_cache)
    # --------------------------------------------------------------------------
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", downloads_dir)

    db_path = tmp_path / "test_overhaul.db"
    monkeypatch.setattr(settings, "DB_PATH", db_path)
    await init_db()

    # User produces a deliverable in active workplace
    active_dir = ai_workplace / "user_0_task_0"
    deliverable_src = active_dir / "final_1080p_vibe.mp4"
    deliverable_src.write_bytes(b"Simulated high-quality promoted video")

    promoted_dest = await promote_ai_deliverable(
        user_id=101,
        task_id="task_e2e_promo",
        file_path=deliverable_src,
        custom_name="promoted_highlight.mp4"
    )

    assert promoted_dest.exists()
    assert promoted_dest.parent == downloads_dir
    assert promoted_dest.name == "promoted_highlight.mp4"

    # Verify file_cache record registration
    promoted_task_id = f"ai_dl_task_e2e_promo_{promoted_dest.stem}"
    cache_record = await get_file_cache(promoted_task_id)
    assert cache_record is not None
    assert cache_record["user_id"] == 101
    assert Path(cache_record["output_path"]).name == "promoted_highlight.mp4"
    assert cache_record["storage_type"] == "download"

    # Verify /files listing includes promoted deliverable
    cached_files = await get_user_cached_files(101)
    assert len(cached_files) == 1
    assert cached_files[0]["name"] == "promoted_highlight.mp4"
    assert cached_files[0]["storage_type"] == "download"

    # --------------------------------------------------------------------------
    # Pillar 4: SQLite WAL Pragmas & Startup Crash Recovery
    # --------------------------------------------------------------------------
    # 1. Verify WAL & synchronous pragmas
    pragmas = await get_database_pragmas()
    assert pragmas["journal_mode"].upper() == "WAL"
    assert pragmas["synchronous"].upper() in ("1", "NORMAL")

    # 2. Simulate in-flight tasks interrupted by abrupt bot process termination
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("""
            INSERT INTO tasks (task_id, user_id, file_name, input_size, status, action)
            VALUES 
                ('crash_task_1', 101, 'in1.mp4', 1000, 'optimizing', 'Optimizing 1080p'),
                ('crash_task_2', 101, 'in2.mp4', 2000, 'downloading', 'Downloading video'),
                ('crash_task_3', 101, 'in3.mp4', 3000, 'processing', 'AI Transcoding')
        """)
        await db.commit()

    # Ensure output media file exists for crash_task_1 and is registered in file_cache
    crash_out = downloads_dir / "crash_task_1_out.mp4"
    crash_out.write_bytes(b"preserved crash output")
    await register_file_cache("crash_task_1", 101, "/tmp/in1.mp4", str(crash_out), storage_type="download")

    # 3. Startup reconciliation execution
    reconciled_tasks = await reconcile_interrupted_tasks()
    assert len(reconciled_tasks) >= 3

    # Verify task state transitioned to 'interrupted'
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        for tid in ["crash_task_1", "crash_task_2", "crash_task_3"]:
            async with db.execute("SELECT status, action FROM tasks WHERE task_id = ?", (tid,)) as cur:
                row = await cur.fetchone()
                assert row is not None
                assert row["status"] == "interrupted"

    # Verify media and cache were preserved during crash recovery
    assert crash_out.exists()
    recovered_cache = await get_file_cache("crash_task_1")
    assert recovered_cache is not None
    assert Path(recovered_cache["output_path"]).name == "crash_task_1_out.mp4"
