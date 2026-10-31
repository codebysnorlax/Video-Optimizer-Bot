"""
Unit tests for LRU AI workspace retention, containment, and active task protection
"""

import time
import os
from pathlib import Path
from core.ai.agent import prune_ai_workspaces, MAX_AI_WORKSPACES


def test_prune_ai_workspaces_lru_ceiling(tmp_path: Path):
    """Verifies that prune_ai_workspaces enforces the 5-directory ceiling removing oldest workspaces."""
    workplace = tmp_path / "Ai_Workplace"
    workplace.mkdir()

    # Create 8 directories with staggered mtimes
    dirs = []
    base_time = time.time() - 1000
    for i in range(8):
        d = workplace / f"user_{i}_task_{i}"
        d.mkdir()
        # Set mtime explicitly
        mtime = base_time + (i * 10)
        os.utime(d, (mtime, mtime))
        dirs.append(d)

    assert len(list(workplace.iterdir())) == 8

    # Prune
    pruned = prune_ai_workspaces(workplace_dir=workplace, max_workspaces=5)

    remaining = sorted([d.name for d in workplace.iterdir()])
    assert len(remaining) == 5
    assert len(pruned) == 3

    # The 3 oldest (user_0, user_1, user_2) should have been pruned
    assert "user_0_task_0" not in remaining
    assert "user_1_task_1" not in remaining
    assert "user_2_task_2" not in remaining

    # The 5 newest should remain
    for i in range(3, 8):
        assert f"user_{i}_task_{i}" in remaining


def test_prune_ai_workspaces_protects_active_sessions(tmp_path: Path):
    """Verifies that in-flight active tasks/sessions are immune from LRU pruning."""
    workplace = tmp_path / "Ai_Workplace"
    workplace.mkdir()

    # Create 7 directories; user_0 is the oldest but is active
    base_time = time.time() - 1000
    dirs = []
    for i in range(7):
        d = workplace / f"100_{i}"
        d.mkdir()
        mtime = base_time + (i * 10)
        os.utime(d, (mtime, mtime))
        dirs.append(d)

    # Protect the oldest session: 100_0
    protected = {"100_0"}

    pruned = prune_ai_workspaces(workplace_dir=workplace, max_workspaces=5, protected_dirs=protected)

    remaining = [d.name for d in workplace.iterdir()]
    assert len(remaining) == 5

    # 100_0 must be preserved because it's protected!
    assert "100_0" in remaining

    # 100_1 and 100_2 (the next oldest unprotected) should be pruned
    assert "100_1" not in remaining
    assert "100_2" not in remaining


async def test_promote_ai_deliverable_to_downloads_and_file_cache(tmp_path: Path):
    """Verifies deliverable promotion moves file to storage/downloads/ and registers in file_cache with storage_type='download'."""
    import aiosqlite
    from config.settings import settings
    from database.db import init_db
    from bot.ai_handler import promote_ai_deliverable

    # Setup isolated dirs
    db_file = tmp_path / "test.db"
    dl_dir = tmp_path / "downloads"
    wp_dir = tmp_path / "Ai_Workplace"

    settings.DB_PATH = db_file
    settings.DOWNLOAD_DIR = dl_dir
    settings.AI_WORKPLACE_DIR = wp_dir

    await init_db()

    # Create deliverable inside workspace
    session_dir = wp_dir / "999_task123"
    session_dir.mkdir(parents=True)
    src_file = session_dir / "approved_cut.mp4"
    src_file.write_bytes(b"DELIVERABLE_BINARY_DATA_42")

    promoted_path = await promote_ai_deliverable(
        user_id=999,
        task_id="task123",
        file_path=src_file
    )

    # 1. Verify promoted file exists in downloads
    assert promoted_path.exists()
    assert promoted_path.parent == dl_dir
    assert promoted_path.read_bytes() == b"DELIVERABLE_BINARY_DATA_42"

    # 2. Verify registered in file_cache with storage_type = 'download'
    async with aiosqlite.connect(db_file) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM file_cache WHERE user_id = 999") as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert dict(row)["storage_type"] == "download"
            assert dict(row)["output_path"] == str(promoted_path)

    # 3. Verify immunity from workspace pruning and cleanup
    settings.clean_ai_workplace(999, "task123")
    assert not session_dir.exists()
    assert promoted_path.exists()
    assert promoted_path.read_bytes() == b"DELIVERABLE_BINARY_DATA_42"
