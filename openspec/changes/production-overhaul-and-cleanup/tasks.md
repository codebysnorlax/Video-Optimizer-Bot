# Tasks

## 1. Multi-Directory Rotating Logging Infrastructure

- [x] 1.1 Implement logging hierarchy in `config/logging.py` providing isolated domain loggers (`system`, `ai`, `errors`) with `RotatingFileHandler` (5MB, 5 backups), and verify directory creation and routing with unit tests
- [x] 1.2 Refactor `core/ai/debug_logger.py` to route all AI agent prompt, tool, and diagnostic traces to `logs/ai/ai.log` and verify that `storage/debug-ai/` is retired
- [x] 1.3 Add unhandled exception and process exit hooks in `run_bot.py` ensuring fatal errors flush immediately to `logs/errors/error.log`, verified via forced exception test

## 2. Workspace Lifecycle Containment & Promotion to Downloads

- [x] 2.1 Implement LRU workspace pruner in `core/ai/agent.py` enforcing a strict ceiling of 5 directories in `storage/Ai_Workplace/` while protecting active sessions, verified with unit tests
- [x] 2.2 Implement deliverable promotion in `bot/ai_handler.py` moving user-approved AI results to `storage/downloads/` and registering in `file_cache` with `storage_type = 'download'`, verified with mock approval test
- [x] 2.3 Update `/files` handler in `bot/handlers.py` and `database/models.py` to display and manage promoted downloads alongside converted media, verified by asserting file listing output

## 3. Crash Resilience, Database WAL & Startup Task Recovery

- [x] 3.1 Configure SQLite connection pragmas in `database/db.py` for Write-Ahead Logging (`PRAGMA journal_mode=WAL`) and `PRAGMA synchronous=NORMAL`, verified by inspecting database connection pragmas
- [x] 3.2 Implement startup task reconciliation in `database/db.py` and `run_bot.py` transitioning unfinished tasks (`processing`, `optimizing`, `downloading`) to `interrupted`, verified with simulated crash restart test
- [x] 3.3 Ensure media files and `file_cache` records for interrupted tasks are preserved across restarts, verified by asserting database integrity after recovery

## 4. Dynamic Skill Retrieval & Agent Prompt Token Optimization

- [x] 4.1 Build in-memory keyword and BM25 skill catalog and matcher in `core/ai/skills.py` that dynamically parses `skills/*.md`, verified with query matching tests
- [x] 4.2 Strip out 60+ lines of hardcoded recipes and filter chains from `core/ai/agent.py` and verify base system prompt length is under 800 tokens
- [x] 4.3 Integrate dynamic skill injection into `core/ai/agent.py` to selectively inject matched skills into prompt context, verified with mock agent query tests

## 5. Termux Hardware Tuning & Dead Code Removal

- [x] 5.1 Replace `psutil` in `core/hardware.py` with direct `/proc/meminfo` and `/proc/cpuinfo` parsing and graceful fallbacks, verified by running hardware tests without `psutil`
- [x] 5.2 Enforce 6-worker concurrency limit in `bot/task_manager.py` for Snapdragon 636 environments, verified by task queue concurrency tests
- [x] 5.3 Delete dead code and deprecated aliases: `mark_file_cache_deleted()` in `database/models.py`, `deliver_ai_output()` in `bot/ai_handler.py`, and legacy aliases in `core/optimizer.py` and `core/ai/agent.py`, verified with codebase grep search
- [x] 5.4 Verify `cryptg` fast crypto acceleration during client startup in `bot/client.py`, verified by inspecting client startup logs

## 6. End-to-End Verification & Regression Testing

- [x] 6.1 Run full project test suite using `.venv/bin/pytest tests/ -v` and verify all tests pass cleanly
- [x] 6.2 Execute end-to-end integration check covering log rotation, workspace pruning, deliverable promotion, and crash recovery
