# Proposal

## Why

The bot contains accumulated dead code, deprecated aliases, redundant wrapper stubs, and over 60 lines of hardcoded AI recipes in `core/ai/agent.py` that waste context window tokens and duplicate skill definitions. Furthermore, AI workspaces in `storage/Ai_Workplace/` grow unbounded (30+ directories), diagnostic logs accumulate unorganized in `storage/debug-ai/`, tasks interrupted by crashes or restarts are permanently marooned in `processing` status, and heavy dependencies like `psutil` are ill-suited for Snapdragon 636 Termux environments. A comprehensive production overhaul is necessary to harden crash resilience, establish standard multi-directory rotating logging (`logs/ai/`, `logs/system/`, `logs/errors/`), prune workspaces strictly to the 5 most recent, promote approved deliverables to `storage/downloads/` for `/files` retrieval, and streamline the AI agent with dynamic skill retrieval.

## What Changes

- **Dead Code & Deprecated Alias Removal**:
  - Delete unreferenced database function `mark_file_cache_deleted()` in `database/models.py`.
  - Remove redundant single-line pass-through `deliver_ai_output()` in `bot/ai_handler.py`.
  - Remove legacy aliases in `core/optimizer.py` (`build_720p_args`, `build_1080p_args`, `get_optimal_scale_filter`) and `core/ai/agent.py` (`run_turn = process_message`).
  - Streamline duplicated progress tracking and error handling routines in `bot/callbacks.py`.
- **Structured Multi-Directory Production Logging**:
  - Retire `storage/debug-ai/` in favor of a centralized `logs/` directory.
  - Implement a three-tier directory hierarchy: `logs/ai/`, `logs/system/`, and `logs/errors/`.
  - Provide dedicated rotating loggers for each tier with configurable size and retention (e.g. 5 MB x 5 backups per log stream).
  - Ensure all uncaught exceptions, critical system lifecycle events, and AI interaction traces flush immediately to disk for crash post-mortems.
- **Strict AI Workspace Lifecycle & Containment**:
  - Enforce a strict limit of at most 5 directories in `storage/Ai_Workplace/`.
  - Implement an LRU cleanup mechanism triggered before and after every AI session that audits timestamps and prunes directories beyond the 5 newest.
  - Audit pre-unlink operations to safely remove temporary files without blocking background tasks.
- **Approved AI Deliverable Promotion to Downloads**:
  - When the user is satisfied with an AI mode result, promote the output media from `storage/Ai_Workplace/<session>/` to `storage/downloads/`.
  - Register the promoted file in `file_cache` within SQLite with `storage_type = 'download'`, making it instantly viewable, sendable, and cleanable via the `/files` Telegram command.
- **Crash Resilience & Auto-Recovery**:
  - Configure SQLite in Write-Ahead Logging mode (`PRAGMA journal_mode=WAL`) and `PRAGMA synchronous=NORMAL` for safe concurrent access and power-loss resilience.
  - Implement an engine startup hook in `run_bot.py` that scans for in-flight tasks left in `processing` or `optimizing` state after an unexpected shutdown or crash.
  - Transition orphaned tasks to `interrupted` status, log the recovery event, and preserve associated cached files for user recovery.
- **Dynamic Skill Loading & Token Optimization**:
  - Eliminate 60+ lines of hardcoded recipes, aspect ratio calculations, and filter chains from `core/ai/agent.py`.
  - Implement a lightweight, zero-dependency keyword and BM25 index over markdown skill definitions in `skills/`.
  - Inject only matching, relevant skills into the system prompt per user request, reducing base system prompt overhead from ~4,000 tokens to under 800 tokens.
- **Termux Android Performance Tuning (Snapdragon 636)**:
  - Replace `psutil` memory inspection with direct zero-dependency `/proc/meminfo` parsing in `core/hardware.py` to prevent native library crashes and reduce RAM usage.
  - Enforce a 6-thread concurrency cap aligned with the Snapdragon 636 CPU architecture to prevent Android Low Memory Killer (LMK) invocations.
  - Maintain `cryptg` C-extension acceleration for Pyrogram transfers and ensure wake-lock support persists across background jobs.

## Capabilities

### New Capabilities

- `structured-logging`: Multi-directory rotating logging architecture (`logs/ai/`, `logs/system/`, `logs/errors/`) replacing legacy debug folder with standard production log streams.
- `workspace-retention`: Workspace containment capping `storage/Ai_Workplace/` to the 5 most recent directories, and promoting user-approved AI deliverables to `storage/downloads/` with `/files` indexing.
- `crash-recovery`: SQLite WAL journal mode and startup reconciliation hook ensuring database integrity and state recovery for interrupted tasks.
- `ai-skills-engine`: Dynamic skill catalog & keyword/BM25 retrieval engine replacing hardcoded prompt recipes in `core/ai/agent.py`.
- `termux-runtime-tuning`: Native Termux environment optimizations (procfs memory monitoring, concurrency limits, fast crypto) and elimination of dead code and redundant stubs.

### Modified Capabilities

(none)

## Impact

- **Affected Files**:
  - `config/settings.py` (logging paths, workspace cap, WAL settings)
  - `config/logging.py` (new/updated logging setup for multi-tier directories)
  - `core/ai/agent.py` (removal of hardcoded recipes, integration with dynamic skill retrieval)
  - `core/ai/skills.py` (lightweight indexing and keyword matching)
  - `core/ai/debug_logger.py` (redirect from `storage/debug-ai` to `logs/ai/`)
  - `core/hardware.py` (native `/proc/meminfo` reading, removal of `psutil` dependency)
  - `core/optimizer.py` (removal of legacy aliases)
  - `database/db.py` (WAL mode pragmas, startup recovery routine)
  - `database/models.py` (removal of `mark_file_cache_deleted`, addition of `storage_type` filter)
  - `bot/ai_handler.py` (removal of `deliver_ai_output`, integration with deliverable promotion to `storage/downloads/`)
  - `bot/callbacks.py` (deduplication of progress and cleanup logic)
  - `bot/handlers.py` (integration with `/files` listing for promoted downloads)
  - `run_bot.py` (startup task sweep and crash recovery execution)
- **Filesystem & Storage**:
  - Deprecation of `storage/debug-ai/`.
  - Creation of `logs/ai/`, `logs/system/`, `logs/errors/`.
  - Capped growth of `storage/Ai_Workplace/` to exactly 5 session folders.
- **Dependencies**: No external dependencies added. Removal of runtime reliance on `psutil`. Pure Python zero-dependency BM25/keyword retrieval.
- **Breaking Changes**:
  - External inspection tools looking for `storage/debug-ai/` must now read from `logs/ai/`.
  - Removed deprecated functions (`build_720p_args`, `build_1080p_args`, `get_optimal_scale_filter`, `deliver_ai_output`, `mark_file_cache_deleted`).
