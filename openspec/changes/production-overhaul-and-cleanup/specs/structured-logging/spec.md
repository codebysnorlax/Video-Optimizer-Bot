# Spec Delta

## Purpose

Provides centralized, categorized, and rotating logging infrastructure across system, AI, and error domains, ensuring diagnostic clarity and zero loss of operational telemetry across restarts.

## ADDED Requirements

### Requirement: Multi-Directory Hierarchy
The logging subsystem SHALL maintain isolated log subdirectories under `logs/`, specifically `logs/system/`, `logs/ai/`, and `logs/errors/`.

#### Scenario: Initialization of log directories
- **WHEN** the application starts up
- **THEN** it verifies and automatically creates `logs/system/`, `logs/ai/`, and `logs/errors/` if they do not exist

#### Scenario: Categorized message routing
- **WHEN** an event is logged with a specific category
- **THEN** system events write to `logs/system/system.log`, AI interactions write to `logs/ai/ai.log`, and errors write to `logs/errors/error.log`

### Requirement: Rotating Log Streams
The logging subsystem SHALL configure rotating file handlers for each domain with configurable size limits and retention counts between 3 and 5 backups.

#### Scenario: Log file size rollover
- **WHEN** an active log file reaches its maximum size threshold
- **THEN** it rotates to a backup suffix and opens a fresh log file without dropping messages

#### Scenario: Bounded backup retention
- **WHEN** the rotation count exceeds the configured retention limit
- **THEN** the oldest backup file in that domain is automatically purged

### Requirement: Crash-Resilient Exception Logging
The logging subsystem SHALL immediately flush uncaught exceptions and fatal error stack traces synchronously to `logs/errors/`.

#### Scenario: Uncaught runtime exception
- **WHEN** an unhandled exception occurs in any background task or handler
- **THEN** the full traceback is synchronously written and flushed to `logs/errors/error.log`

#### Scenario: Process exit flush
- **WHEN** the process receives a termination signal or enters crash shutdown
- **THEN** all pending log buffers are flushed to disk before exit

### Requirement: Legacy Debug AI Deprecation
The system SHALL discontinue generating files in `storage/debug-ai/` and route all AI agent traces into `logs/ai/`.

#### Scenario: AI conversation turn logging
- **WHEN** an AI turn executes with tool calls and prompt context
- **THEN** the diagnostics are logged to `logs/ai/` and no new artifacts are written to `storage/debug-ai/`
