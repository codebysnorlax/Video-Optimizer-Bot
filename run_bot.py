#!/usr/bin/env python3
"""
Main Application Entry Point: Video Optimizer & Converter Telegram Bot
Dual Support: Ubuntu LTS (Intel VAAPI GPU) & Nokia 6.1 Plus Snapdragon 636 (Termux Android 10)
"""

import sys
import logging
import asyncio
import threading
import signal
from typing import Optional
from config.settings import settings
from config.logging import setup_logging, flush_all_logs, error_logger, system_logger
from core.hardware import current_hardware
from core.crypto_patch import apply_crypto_patch, get_crypto_info
from core.terminal import ColoredLogFormatter, InteractiveStreamHandler, print_banner_box, BOLD_GREEN, RESET
from database.db import init_db, reconcile_interrupted_tasks
from bot.client import create_bot_client

# Configure colorful structured logging and isolated domain loggers
setup_logging(
    base_log_dir=settings.LOG_DIR,
    max_bytes=settings.LOG_MAX_BYTES,
    backup_count=settings.LOG_BACKUP_COUNT,
    enable_console=False
)

handler = InteractiveStreamHandler(sys.stdout)
handler.setFormatter(ColoredLogFormatter())
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers = [handler]

logger = logging.getLogger("OptimizerBot")


def handle_uncaught_exception(exc_type, exc_value, exc_traceback) -> None:
    """Synchronously captures and flushes unhandled exceptions to logs/errors/error.log."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    error_logger.critical("Uncaught fatal exception encountered:", exc_info=(exc_type, exc_value, exc_traceback))
    flush_all_logs()


def handle_thread_exception(args: threading.ExceptHookArgs) -> None:
    """Synchronously captures and flushes thread exceptions to logs/errors/error.log."""
    if issubclass(args.exc_type, KeyboardInterrupt):
        return
    error_logger.critical(
        f"Uncaught thread exception in {args.thread}:",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
    )
    flush_all_logs()


def handle_async_exception(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Synchronously captures and flushes asyncio loop exceptions to logs/errors/error.log."""
    msg = context.get("message", "Unhandled asyncio exception")
    exc = context.get("exception")
    error_logger.critical(f"Asyncio loop exception: {msg}", exc_info=exc)
    flush_all_logs()


def install_exception_hooks(loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """Installs sys, threading, and asyncio exception hooks for crash-resilient logging."""
    sys.excepthook = handle_uncaught_exception
    threading.excepthook = handle_thread_exception
    if loop:
        loop.set_exception_handler(handle_async_exception)


def print_startup_banner() -> None:
    """Prints a clear, colorful system diagnostic box."""
    disk = settings.get_disk_usage()
    crypto = get_crypto_info()
    items = [
        ("OS Platform", f"{current_hardware.os_name} ({current_hardware.arch})"),
        ("Mobile Termux", "YES (Android 10 SDM636)" if current_hardware.is_termux else "NO (Standard Linux)"),
        ("CPU Model", f"{current_hardware.cpu_model} ({current_hardware.cpu_cores} threads)"),
        ("Memory Status", f"{current_hardware.available_ram_mb} MB free / {current_hardware.total_ram_mb} MB total"),
        ("Hardware Accel", current_hardware.description),
        ("Primary Encoder", current_hardware.video_encoder),
        ("MTProto Crypto", f"{crypto['engine']} ({crypto['speed_estimate']})"),
        ("Parallel Engine", "Multi-connection MTProto chunk streaming"),
        ("Host Disk Space", f"{disk['free_gb']} GB free / {disk['total_gb']} GB total"),
        ("Storage Temp Dir", str(settings.DOWNLOAD_DIR)),
        ("Database File", str(settings.DB_PATH))
    ]
    print_banner_box("TELEGRAM VIDEO OPTIMIZER & CONVERTER BOT", items)


async def main() -> None:
    loop = asyncio.get_running_loop()
    install_exception_hooks(loop)

    # 1. Apply hardware / C crypto patches
    apply_crypto_patch()

    # 2. Print colorful system diagnostic banner
    print_startup_banner()

    # 2. Validate configuration
    if not settings.BOT_TOKEN or not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
        logger.error("Missing credentials in .env file! Ensure BOT_TOKEN, TELEGRAM_API_ID, and TELEGRAM_API_HASH are set.")
        sys.exit(1)

    # 3. Ensure directories & initialize SQLite database with crash recovery
    settings.ensure_directories()
    logger.info("Initializing SQLite database (WAL mode)...")
    await init_db()
    reconciled = await reconcile_interrupted_tasks()
    if reconciled:
        logger.info(f"Crash recovery reconciled {len(reconciled)} interrupted tasks from prior run.")

    # 4. Instantiate Telethon Bot Client
    logger.info("Connecting to Telegram MTProto network...")
    client = create_bot_client()

    # Telethon's start() returns a coroutine when the loop is active, but is statically typed as TelegramClient
    await client.start(bot_token=settings.BOT_TOKEN)  # type: ignore
    me = await client.get_me()
    logger.info(f"Bot successfully authenticated as @{me.username} (ID: {me.id})")

    # Register Telegram native command menu in chat input box
    from bot.client import register_bot_menu_commands
    await register_bot_menu_commands(client)

    print(f"\n{BOLD_GREEN}>>> Bot is online and listening for videos as @{me.username} <<<{RESET}\n", flush=True)

    # 6. Start background idle session reaper
    from bot.task_manager import task_manager
    task_manager.start_idle_reaper(bot_client=client)

    # 7. Run bot loop until disconnected
    await client.run_until_disconnected()


if __name__ == "__main__":
    install_exception_hooks()
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        system_logger.info("Bot stopped gracefully.")
    except Exception as e:
        error_logger.critical(f"Fatal unhandled exception in main application: {e}", exc_info=True)
        flush_all_logs()
        sys.exit(1)
    finally:
        flush_all_logs()
