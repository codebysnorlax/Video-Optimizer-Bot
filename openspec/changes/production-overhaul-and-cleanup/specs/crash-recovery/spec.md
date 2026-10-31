# Spec Delta

## Purpose

Guarantees transactional database consistency under sudden power loss or process crashes, and reconciles interrupted task records upon application restart.

## ADDED Requirements

### Requirement: Database WAL Mode Pragmas
The database initialization SHALL configure SQLite with Write-Ahead Logging (`PRAGMA journal_mode=WAL`) and `PRAGMA synchronous=NORMAL`.

#### Scenario: Database connection establishment
- **WHEN** the application opens an aiosqlite connection pool
- **THEN** it executes `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` before any schema or table queries

#### Scenario: Power-loss resilience
- **WHEN** the process terminates abruptly during a transaction
- **THEN** on subsequent startup SQLite cleanly rolls forward or rolls back WAL frames without corrupting table structures

### Requirement: Startup Task Reconciliation
The system SHALL scan the task queue at startup and reconcile any tasks marked with an active status (`processing`, `optimizing`, `downloading`).

#### Scenario: Interrupted tasks transition
- **WHEN** the bot launches and detects tasks with unfinished active statuses from a prior run
- **THEN** the system updates their status to `interrupted` and appends an explanation to the task log

#### Scenario: Prevention of phantom task execution
- **WHEN** tasks are transitioned to `interrupted`
- **THEN** the active task manager worker loop does not attempt to resume execution without explicit user re-trigger

### Requirement: File Cache Pointer Preservation
The system SHALL keep cached media records and disk files intact during crash recovery transitions.

#### Scenario: Media retention after unexpected reboot
- **WHEN** a task is transitioned to `interrupted` after a reboot
- **THEN** its associated file in `storage/` and row in `file_cache` remain accessible for user inspection via `/files`
