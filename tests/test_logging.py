"""
Unit tests for structured multi-directory logging hierarchy in config/logging.py
"""

import logging
from pathlib import Path
from config.logging import setup_logging, get_log_paths, flush_all_logs, get_logger


def test_setup_logging_directory_creation_and_routing(tmp_path: Path):
    """Verifies that setup_logging creates isolated domain directories and routes messages correctly."""
    log_dir = tmp_path / "test_logs"

    loggers = setup_logging(
        base_log_dir=log_dir,
        max_bytes=1024 * 1024,
        backup_count=5,
        enable_console=False
    )

    # 1. Verify directory creation
    paths = get_log_paths(log_dir)
    assert paths["system"].exists() and paths["system"].is_dir()
    assert paths["ai"].exists() and paths["ai"].is_dir()
    assert paths["errors"].exists() and paths["errors"].is_dir()

    system_log = paths["system"] / "system.log"
    ai_log = paths["ai"] / "ai.log"
    error_log = paths["errors"] / "error.log"

    # 2. Log messages to each domain
    loggers["system"].info("System lifecycle event 101")
    loggers["ai"].info("AI prompt trace and tool turn 202")
    loggers["errors"].error("Critical runtime crash 500")

    flush_all_logs()

    # 3. Verify message routing and isolation
    assert system_log.exists()
    assert ai_log.exists()
    assert error_log.exists()

    sys_text = system_log.read_text(encoding="utf-8")
    ai_text = ai_log.read_text(encoding="utf-8")
    err_text = error_log.read_text(encoding="utf-8")

    assert "System lifecycle event 101" in sys_text
    assert "AI prompt trace and tool turn 202" not in sys_text
    assert "Critical runtime crash 500" not in sys_text

    assert "AI prompt trace and tool turn 202" in ai_text
    assert "System lifecycle event 101" not in ai_text
    assert "Critical runtime crash 500" not in ai_text

    assert "Critical runtime crash 500" in err_text
    assert "System lifecycle event 101" not in err_text
    assert "AI prompt trace and tool turn 202" not in err_text


def test_rotating_file_handler_rollover(tmp_path: Path):
    """Verifies that RotatingFileHandler rotates when size limit is exceeded."""
    log_dir = tmp_path / "rotate_logs"

    # Use very small max_bytes to trigger rotation quickly
    loggers = setup_logging(
        base_log_dir=log_dir,
        max_bytes=200,
        backup_count=3,
        enable_console=False
    )

    sys_logger = loggers["system"]
    for i in range(50):
        sys_logger.info(f"Filling up log with line number {i:03d} to trigger size rotation")

    flush_all_logs()

    paths = get_log_paths(log_dir)
    system_dir = paths["system"]
    rotated_files = list(system_dir.glob("system.log*"))
    assert len(rotated_files) > 1


def test_forced_uncaught_exception_flushes_to_error_log(tmp_path: Path):
    """Verifies that forced unhandled exceptions flush immediately to logs/errors/error.log."""
    import sys
    from run_bot import handle_uncaught_exception

    log_dir = tmp_path / "crash_logs"
    setup_logging(
        base_log_dir=log_dir,
        max_bytes=1024 * 1024,
        backup_count=5,
        enable_console=False
    )

    paths = get_log_paths(log_dir)
    error_log = paths["errors"] / "error.log"

    try:
        raise ValueError("FORCED_FATAL_CRASH_TEST_999")
    except ValueError:
        exc_type, exc_val, exc_tb = sys.exc_info()
        handle_uncaught_exception(exc_type, exc_val, exc_tb)

    assert error_log.exists()
    err_text = error_log.read_text(encoding="utf-8")
    assert "FORCED_FATAL_CRASH_TEST_999" in err_text
    assert "ValueError" in err_text
    assert "Uncaught fatal exception" in err_text
