# Design

## Context

See [proposal.md](proposal.md) for background and motivation. The system currently executes inside Linux and Android Termux environments (Snapdragon 636, 8 cores). Inspection of the codebase revealed several architectural bottlenecks:
- Logging is fragmented: system logs use standard logging while AI traces dump unstructured markdown and JSON files into `storage/debug-ai/`.
- `storage/Ai_Workplace/` accumulates unbounded temporary workspaces (over 30 directories discovered during exploration).
- Tasks terminated abruptly by crashes or Android Low Memory Killer (LMK) remain permanently in `processing` state in the SQLite database.
- `core/ai/agent.py` hardcodes over 60 lines of video recipes and calculations in the system prompt, despite those instructions existing in `skills/`.
- `core/hardware.py` imports `psutil`, which requires native compilation and can crash or fail in Termux environments without C toolchains.

## Goals / Non-Goals

**Goals:**
- Provide a unified, production-grade logging architecture under `logs/` (`logs/ai/`, `logs/system/`, `logs/errors/`) with rotating file handlers and immediate error flushing.
- Enforce a strict ceiling of at most 5 directories in `storage/Ai_Workplace/`, safely pruning older completed sessions by modification time.
- Provide deliverable promotion from `storage/Ai_Workplace/` to `storage/downloads/` with registration in `file_cache` for `/files` command management.
- Harden database persistence with SQLite WAL mode and a startup reconciliation routine that detects orphaned tasks and marks them `interrupted`.
- Replace hardcoded recipes in `core/ai/agent.py` with an in-memory keyword/BM25 skill retrieval engine in `core/ai/skills.py` (<800 base prompt tokens).
- Optimize for Termux on Snapdragon 636 with direct `/proc/meminfo` inspection, 6-worker concurrency limit, and elimination of dead code and obsolete aliases.

**Non-Goals:**
- Heavy external RAG frameworks (e.g. LangChain, LlamaIndex, ChromaDB, SentenceTransformers) requiring massive dependencies or PyTorch.
- Modifying underlying FFmpeg command structures or filter chains in `core/converter.py` or `core/optimizer.py`.
- Touching or modifying any prototypes in the `idea/` folder.

## Decisions

### 1. Multi-Directory Rotating Logging vs. Monolithic Log
- **Decision**: Establish three isolated domains: `logs/system/system.log`, `logs/ai/ai.log`, and `logs/errors/error.log`, each with `RotatingFileHandler` (5MB max size, 5 backup files).
- **Rationale**: AI interactions generate extensive prompt and tool turn logs that drown out critical system lifecycle events and errors in a single log file. Separating domains allows clean inspection and immediate triage.
- **Alternatives Considered**: Monolithic single `bot.log` (rejected due to noise) or syslog/remote logging (rejected to keep Termux offline and self-contained).

### 2. In-Memory Zero-Dependency Skill Indexing vs. Heavy Vector DB
- **Decision**: Implement a pure Python keyword and BM25 scoring index in `core/ai/skills.py` that parses skill frontmatter and capability sections at startup.
- **Rationale**: The skill catalog contains 3–10 targeted markdown files. A vector database adds 200MB+ in dependencies and native binaries that frequently break in Termux. A 60-line pure Python BM25 index executes in <2ms, requires 0 dependencies, and accurately injects relevant skills into the prompt while keeping base tokens under 800.
- **Alternatives Considered**: SentenceTransformers with SQLite-vss (rejected due to compilation failures on Android Termux) and full static prompt embedding (rejected due to excessive context window consumption).

### 3. LRU Workspace Pruning with Active Task Protection vs. Fixed TTL
- **Decision**: Maintain a strict upper bound of 5 directories in `storage/Ai_Workplace/` using an LRU policy based on directory `mtime`, with an explicit exclusion for directories currently bound to active `TaskManager` sessions.
- **Rationale**: A time-to-live (TTL) approach does not prevent disk exhaustion during burst usage. Strict count bounding guarantees a predictable storage footprint on mobile devices with limited internal flash storage.
- **Alternatives Considered**: 24-hour TTL cron job (rejected because 10 large video jobs in 1 hour can fill all available storage before TTL expires).

### 4. Promotion to `storage/downloads/` with Telegram `file_cache` Integration
- **Decision**: When the user approves an AI deliverable, copy/move the output file to `storage/downloads/` and insert a record into SQLite `file_cache` with `storage_type = 'download'`.
- **Rationale**: Reuses the existing `/files` interactive keyboard and cleanup infrastructure, giving users full access to download, inspect, or delete their approved AI creations alongside converted videos.
- **Alternatives Considered**: Keeping deliverables in `Ai_Workplace/` permanently (rejected because workspace pruning would delete them).

### 5. SQLite WAL Mode and Startup Task Sweep
- **Decision**: Execute `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` on connection establishment, coupled with an asynchronous startup hook in `run_bot.py` that queries for `status IN ('processing', 'optimizing', 'downloading')` and transitions them to `interrupted`.
- **Rationale**: Termux processes can be killed at any moment by Android's Low Memory Killer. WAL mode ensures transactional durability without file corruption, while the startup sweep prevents orphaned jobs from remaining permanently locked.
- **Alternatives Considered**: In-memory task management only (rejected because state is completely lost upon crash).

### 6. Zero-Dependency Linux/Android `/proc/meminfo` Reading vs. `psutil`
- **Decision**: Parse `/proc/meminfo` directly in `core/hardware.py` to obtain `MemAvailable` and total RAM, falling back to Python `os.sysconf` where applicable.
- **Rationale**: `psutil` requires C headers and compilation tools that are not always available on standard Termux installations. Direct procfs parsing is native, instantaneous, and 100% reliable on Android/Linux.
- **Alternatives Considered**: Keeping `psutil` (rejected due to user crashes and setup complexity on mobile).

## Risks / Trade-offs

- **[Risk]** A user generates more than 5 AI videos without approving them, causing the earliest workspace to be pruned.
  - **Mitigation**: Deliverable notification includes a direct prompt/button to save to downloads, and pruning explicitly logs pruned directories in `logs/system/system.log`.
- **[Risk]** Keyword/BM25 retrieval misses a nuanced creative video filter request.
  - **Mitigation**: The core `ffmpeg` skill is always included as a durable baseline, and the retrieval threshold has a generous fallback to ensure relevant skills are loaded whenever in doubt.
- **[Risk]** Log files consume storage if rotation fails.
  - **Mitigation**: Standard library `logging.handlers.RotatingFileHandler` with explicit `maxBytes=5_242_880` (5 MB) and `backupCount=5` per stream ensures total log disk consumption never exceeds ~75 MB across all domains.

## Migration Plan

1. **Directories**: Ensure startup routine verifies and creates `logs/ai/`, `logs/system/`, `logs/errors/`, and `storage/downloads/`.
2. **Legacy Cleanup**: Safe retirement of `storage/debug-ai/` upon initial startup.
3. **Database**: Existing SQLite tables (`tasks`, `file_cache`) remain compatible without destructive schema alterations.
4. **Validation**: Execute automated test suite (`pytest tests/`) ensuring zero regression across all core transcoding, optimization, AI agent, and database operations.
