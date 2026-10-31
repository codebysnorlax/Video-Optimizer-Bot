# Spec Delta

## Purpose

Dynamically discovers, indexes, and selectively retrieves specialized skill markdown instructions based on user intent, eliminating prompt bloat from hardcoded recipes.

## ADDED Requirements

### Requirement: Dynamic Skill Catalog Indexing
The AI agent subsystem SHALL load and index skill documentation from the `skills/` directory dynamically without embedding recipes directly in code files.

#### Scenario: Dynamic discovery at startup
- **WHEN** the agent subsystem initializes
- **THEN** it scans the `skills/` directory, extracts metadata and keywords from each skill file, and registers them in an in-memory catalog

#### Scenario: Update without code changes
- **WHEN** a new skill markdown file is added or edited in `skills/`
- **THEN** the skill catalog updates its registered capability triggers without modifying `core/ai/agent.py`

### Requirement: Intent-Based Skill Retrieval
The AI agent subsystem SHALL analyze incoming user requests and inject only the skills relevant to the user query into the conversation context.

#### Scenario: Query matching specific skill
- **WHEN** a user asks to diagnose or inspect video corruption
- **THEN** the system retrieves only the `video_diagnosis` skill and injects its guidance into the model prompt

#### Scenario: Query matching creative filter
- **WHEN** a user asks for artistic styling, hue shifts, or slow-motion loops
- **THEN** the system retrieves only the `creative_fx` skill and injects its guidance into the model prompt

#### Scenario: General video conversion query
- **WHEN** a user submits a standard conversion or compression request
- **THEN** the system injects only the base `ffmpeg` skill and suppresses unrelated specialized skill rules

### Requirement: Base System Prompt Token Bounding
The base system prompt SHALL contain only behavioral rules, safety boundaries, and tool specifications, keeping base prompt size under 800 tokens.

#### Scenario: System prompt token measurement
- **WHEN** the base system prompt is compiled for a new AI turn
- **THEN** total prompt tokens prior to skill injection remain below 800 tokens
