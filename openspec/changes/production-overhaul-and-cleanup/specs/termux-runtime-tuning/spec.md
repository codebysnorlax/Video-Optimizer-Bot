# Spec Delta

## Purpose

Optimizes hardware detection, process concurrency, and cryptography for Android Termux environments while removing unused code and obsolete function aliases.

## ADDED Requirements

### Requirement: Zero-Dependency Hardware Inspection
The hardware detection module SHALL parse system metrics directly from `/proc/cpuinfo` and `/proc/meminfo` in Linux and Android environments without importing `psutil`.

#### Scenario: Memory inspection on Termux
- **WHEN** the engine queries available RAM before initiating a transcoding job on Android
- **THEN** it parses `/proc/meminfo` and returns accurate memory values without raising import or native binary errors

#### Scenario: Fallback for non-procfs platforms
- **WHEN** the engine runs in an environment where `/proc/meminfo` is absent
- **THEN** it falls back gracefully to standard library mechanisms without failing task execution

### Requirement: Snapdragon Concurrency Bound
The task scheduler SHALL cap simultaneous active media processing tasks to a maximum of 6 concurrent workers when running on 8-core Snapdragon mobile chipsets.

#### Scenario: High load task scheduling
- **WHEN** multiple users enqueue transcoding or AI tasks exceeding 6 simultaneous requests
- **THEN** additional tasks remain in queue rather than executing simultaneously, preventing Android Low Memory Killer terminations

### Requirement: Dead Code and Legacy Alias Elimination
The codebase SHALL remove all unreferenced dead code, redundant wrappers, and deprecated function aliases.

#### Scenario: Database model cleanup
- **WHEN** inspecting `database/models.py`
- **THEN** unreferenced function `mark_file_cache_deleted()` is absent from the module

#### Scenario: AI output handler simplification
- **WHEN** delivering AI results in `bot/ai_handler.py`
- **THEN** the redundant single-line pass-through function `deliver_ai_output()` is removed in favor of direct `deliver_ai_results()` invocation

#### Scenario: Optimizer alias removal
- **WHEN** importing `core/optimizer.py`
- **THEN** legacy aliases `build_720p_args`, `build_1080p_args`, and `get_optimal_scale_filter` are removed

### Requirement: Cryptographic Transfer Acceleration
The client initialization SHALL verify `cryptg` C-extension acceleration for Pyrogram transfers to ensure maximum throughput in Termux.

#### Scenario: Startup crypto verification
- **WHEN** the Telegram bot starts up
- **THEN** it logs whether `cryptg` is successfully accelerating MTProto packet encryption and decryption
