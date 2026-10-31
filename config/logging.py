"""
Structured Multi-Directory Production Logging Configuration
Provides isolated domain loggers:
- system: logs/system/system.log
- ai: logs/ai/ai.log
- errors: logs/errors/error.log

Configures RotatingFileHandler (5MB, 5 backups) and synchronous flush capabilities.
"""

import os
import sys
import atexit
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional

# Default configuration
DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
DEFAULT_BACKUP_COUNT = 5

# Root logs path
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = BASE_DIR / "logs"

# Formatter
DEFAULT_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_log_paths(base_log_dir: Optional[Path] = None) -> Dict[str, Path]:
    """Returns directory paths for each logging domain."""
    root = Path(base_log_dir) if base_log_dir else DEFAULT_LOG_DIR
    return {
        "root": root,
        "system": root / "system",
        "ai": root / "ai",
        "errors": root / "errors",
    }


def setup_logging(
    base_log_dir: Optional[Path] = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    enable_console: bool = False,
    formatter: Optional[logging.Formatter] = None
) -> Dict[str, logging.Logger]:
    """
    Initializes multi-directory logging hierarchy and configures isolated domain loggers.
    Creates logs/system/, logs/ai/, and logs/errors/ if they do not exist.
    """
    paths = get_log_paths(base_log_dir)

    # Automatically verify and create log directories
    for domain in ("system", "ai", "errors"):
        paths[domain].mkdir(parents=True, exist_ok=True)

    log_formatter = formatter or logging.Formatter(DEFAULT_LOG_FORMAT, DEFAULT_DATE_FORMAT)

    domain_configs = {
        "system": {
            "file": paths["system"] / "system.log",
            "level": logging.INFO
        },
        "ai": {
            "file": paths["ai"] / "ai.log",
            "level": logging.DEBUG
        },
        "errors": {
            "file": paths["errors"] / "error.log",
            "level": logging.ERROR
        }
    }

    loggers: Dict[str, logging.Logger] = {}

    for domain, config in domain_configs.items():
        logger = logging.getLogger(domain)
        logger.setLevel(config["level"])
        logger.propagate = False

        # Check if RotatingFileHandler already attached to avoid duplicate handlers
        existing_file_handlers = [
            h for h in logger.handlers
            if isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == str(config["file"])
        ]

        if not existing_file_handlers:
            # Remove any existing handlers for a clean setup
            logger.handlers.clear()

            file_handler = RotatingFileHandler(
                filename=str(config["file"]),
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8"
            )
            file_handler.setFormatter(log_formatter)
            file_handler.setLevel(config["level"])
            logger.addHandler(file_handler)

            if enable_console:
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setFormatter(log_formatter)
                console_handler.setLevel(config["level"])
                logger.addHandler(console_handler)

        loggers[domain] = logger

    return loggers


def get_logger(domain: str) -> logging.Logger:
    """Returns domain logger or a sublogger under a domain."""
    return logging.getLogger(domain)


# Domain loggers
system_logger = logging.getLogger("system")
ai_logger = logging.getLogger("ai")
error_logger = logging.getLogger("errors")


def flush_all_logs() -> None:
    """
    Synchronously flushes all pending log buffers to disk across all domains and root.
    Ensures zero loss of diagnostic and crash data on unexpected exit.
    """
    loggers_to_flush = [
        logging.getLogger(),
        logging.getLogger("system"),
        logging.getLogger("ai"),
        logging.getLogger("errors"),
    ]

    for logger in loggers_to_flush:
        for handler in logger.handlers:
            try:
                handler.flush()
                stream = getattr(handler, "stream", None)
                if stream and hasattr(stream, "fileno"):
                    try:
                        os.fsync(stream.fileno())
                    except (OSError, ValueError):
                        pass
            except Exception:
                pass


# Register synchronous flush on process exit
atexit.register(flush_all_logs)
