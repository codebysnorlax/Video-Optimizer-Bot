# Spec Delta

## Purpose

Controls the lifecycle and disk retention of temporary AI session workspaces and facilitates the promotion of user-approved deliverables into persistent storage.

## ADDED Requirements

### Requirement: Strict Workspace Containment
The system SHALL limit the number of session directories in `storage/Ai_Workplace/` to a maximum of 5, deleting the oldest unpinned directories when the limit is exceeded.

#### Scenario: Workspace pruning on session creation
- **WHEN** a new AI session directory is created and the total count exceeds 5
- **THEN** the system identifies the oldest completed workspace directories by modification time and removes them until exactly 5 remain

#### Scenario: Post-session cleanup sweep
- **WHEN** an AI processing turn finishes or aborts
- **THEN** the workspace directory list is audited and pruned to adhere to the 5-directory ceiling

### Requirement: Safe In-Use Directory Protection
The system SHALL protect active in-flight processing workspaces from being deleted during pruning sweeps.

#### Scenario: Active task directory evaluation
- **WHEN** a pruning cycle runs while an AI task is actively writing to a workspace directory
- **THEN** the active task's workspace is excluded from deletion candidates regardless of timestamp

### Requirement: AI Deliverable Promotion
The system SHALL copy or move approved media deliverables from the temporary AI workspace to `storage/downloads/` upon user satisfaction.

#### Scenario: User approval of AI result
- **WHEN** a user accepts or expresses satisfaction with an AI-generated media output
- **THEN** the output file is promoted from `storage/Ai_Workplace/<session>/` to `storage/downloads/<filename>`

#### Scenario: Immunity from workspace pruning
- **WHEN** an approved file is promoted to `storage/downloads/`
- **THEN** subsequent workspace pruning of `storage/Ai_Workplace/` does not alter or delete the promoted file

### Requirement: Telegram File Cache Registration
The system SHALL register promoted download files in the database `file_cache` table with storage type `download`.

#### Scenario: Cataloging promoted file
- **WHEN** a file is successfully placed in `storage/downloads/`
- **THEN** a new `file_cache` record is inserted containing file metadata, user ID, and `storage_type = 'download'`

#### Scenario: Discovery via files command
- **WHEN** a user issues the `/files` Telegram command
- **THEN** the promoted files appear in the list with options to view, redownload, or delete
