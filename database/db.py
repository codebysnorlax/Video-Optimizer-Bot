"""
Asynchronous SQLite Database Management
Configured with Write-Ahead Logging (WAL) and synchronous=NORMAL for power-loss resilience.
Includes startup task reconciliation for crash recovery.
"""

import aiosqlite
import logging
from typing import List, Dict, Any, Tuple
from pathlib import Path
from config.settings import settings

logger = logging.getLogger(__name__)


async def get_database_pragmas(db_path: Path = None) -> Dict[str, str]:
    """Inspects SQLite connection pragmas (journal_mode, synchronous)."""
    target = db_path or settings.DB_PATH
    async with aiosqlite.connect(target) as db:
        await db.execute("PRAGMA synchronous=NORMAL")
        async with db.execute("PRAGMA journal_mode") as c1:
            j_mode = (await c1.fetchone())[0]
        async with db.execute("PRAGMA synchronous") as c2:
            sync = (await c2.fetchone())[0]
        return {
            "journal_mode": str(j_mode).upper(),
            "synchronous": str(sync)
        }


async def init_db() -> None:
    """Initialize database tables with WAL mode and power-loss resilience pragmas."""
    db_path = settings.DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(db_path) as db:
        # Configure WAL journal mode and normal synchronous durability
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")

        # Users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_tasks INTEGER DEFAULT 0
            )
        """)

        # Tasks history table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                user_id INTEGER,
                file_name TEXT,
                input_size INTEGER,
                output_size INTEGER DEFAULT 0,
                duration REAL DEFAULT 0.0,
                action TEXT,
                status TEXT,
                elapsed_time REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)

        # File cache table (keeps track of server files to prompt user for separate deletion)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS file_cache (
                task_id TEXT PRIMARY KEY,
                user_id INTEGER,
                input_path TEXT,
                output_path TEXT,
                input_deleted INTEGER DEFAULT 0,
                output_deleted INTEGER DEFAULT 0,
                deleted INTEGER DEFAULT 0,
                storage_type TEXT DEFAULT 'converted',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migration: ensure storage_type column exists if table was previously created
        try:
            await db.execute("ALTER TABLE file_cache ADD COLUMN storage_type TEXT DEFAULT 'converted'")
        except Exception:
            pass

        # User preferences table (e.g. AI model selection)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                ai_model TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # AI session persistence table (conversation history, assets, agent config)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ai_sessions (
                user_id INTEGER,
                task_id TEXT,
                history_json TEXT,
                assets_json TEXT,
                preferred_model TEXT,
                input_path TEXT,
                is_active INTEGER DEFAULT 1,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, task_id)
            )
        """)

        # Backward compatibility column check
        async with db.execute("PRAGMA table_info(file_cache)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
            if "input_deleted" not in cols:
                await db.execute("ALTER TABLE file_cache ADD COLUMN input_deleted INTEGER DEFAULT 0")
            if "output_deleted" not in cols:
                await db.execute("ALTER TABLE file_cache ADD COLUMN output_deleted INTEGER DEFAULT 0")

        await db.commit()
    logger.info(f"Database initialized successfully with WAL mode at {db_path}")


async def reconcile_interrupted_tasks() -> List[Dict[str, Any]]:
    """
    Scans for in-flight tasks left in active status ('processing', 'optimizing', 'downloading')
    due to sudden process crashes or power-loss, transitions them to 'interrupted',
    and preserves their cached file records for user inspection via /files.
    """
    reconciled: List[Dict[str, Any]] = []
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        db.row_factory = aiosqlite.Row

        # 1. Query unfinished tasks from prior run
        async with db.execute("""
            SELECT task_id, user_id, file_name, status, created_at
            FROM tasks
            WHERE status IN ('processing', 'optimizing', 'downloading')
        """) as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                reconciled.append(dict(r))

        if reconciled:
            # 2. Transition them to 'interrupted'
            await db.execute("""
                UPDATE tasks
                SET status = 'interrupted',
                    completed_at = CURRENT_TIMESTAMP
                WHERE status IN ('processing', 'optimizing', 'downloading')
            """)
            await db.commit()

            system_logger = logging.getLogger("system")
            system_logger.info(
                f"Startup task reconciliation: transitioned {len(reconciled)} orphaned task(s) to 'interrupted'"
            )
            for t in reconciled:
                system_logger.info(
                    f"Reconciled orphaned task {t['task_id']} (was '{t['status']}', user {t['user_id']})"
                )

    return reconciled


async def get_connection() -> aiosqlite.Connection:
    """Returns an active SQLite connection configured with WAL and synchronous=NORMAL."""
    db = await aiosqlite.connect(settings.DB_PATH)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA synchronous=NORMAL")
    return db
