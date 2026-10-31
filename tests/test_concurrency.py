"""
Unit tests for Snapdragon 636 6-worker concurrency bounds in bot/task_manager.py
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock

from bot.task_manager import TaskManager, TaskContext
from core.hardware import HardwareProfile


@pytest.mark.asyncio
async def test_snapdragon_termux_concurrency_ceiling():
    """Verifies that Snapdragon / Termux environments cap max_concurrent strictly to 6."""
    mock_snapdragon_hw = HardwareProfile(
        os_name="Android (Termux)",
        is_termux=True,
        arch="aarch64",
        cpu_cores=8,
        total_ram_mb=3800,
        available_ram_mb=1600,
        cpu_model="Qualcomm Technologies, Inc SDM636",
        video_encoder="libx264",
        hevc_encoder="libx265",
        hwaccel_type="cpu",
        hwaccel_device=None,
        description="Snapdragon aarch64 (Termux 8-Core NEON)"
    )

    with patch("bot.task_manager.current_hardware", mock_snapdragon_hw):
        # Requesting 10 workers on Snapdragon must be capped to 6
        tm_10 = TaskManager(max_concurrent=10)
        assert tm_10.max_concurrent == 6

        # Requesting 2 workers on Snapdragon is preserved
        tm_2 = TaskManager(max_concurrent=2)
        assert tm_2.max_concurrent == 2


@pytest.mark.asyncio
async def test_desktop_concurrency_uncapped():
    """Verifies that non-Termux x86_64 environments respect requested higher concurrency."""
    mock_desktop_hw = HardwareProfile(
        os_name="Linux 6.8.0",
        is_termux=False,
        arch="x86_64",
        cpu_cores=16,
        total_ram_mb=32000,
        available_ram_mb=24000,
        cpu_model="Intel Core i9",
        video_encoder="h264_vaapi",
        hevc_encoder="hevc_vaapi",
        hwaccel_type="vaapi",
        hwaccel_device="/dev/dri/renderD128",
        description="Intel/AMD GPU (VAAPI)"
    )

    with patch("bot.task_manager.current_hardware", mock_desktop_hw):
        tm_desktop = TaskManager(max_concurrent=8)
        assert tm_desktop.max_concurrent == 8


@pytest.mark.asyncio
async def test_task_queue_concurrency_at_capacity():
    """Verifies that when 7 tasks attempt to acquire encoding slots with max_concurrent=6,
    the first 6 acquire immediately and the 7th waits in queue until a slot is released."""
    mock_hw = HardwareProfile(
        os_name="Android (Termux)",
        is_termux=True,
        arch="aarch64",
        cpu_cores=8,
        total_ram_mb=3800,
        available_ram_mb=1600,
        cpu_model="SDM636",
        video_encoder="libx264",
        hevc_encoder="libx265",
        hwaccel_type="cpu",
        hwaccel_device=None,
        description="Snapdragon aarch64 (Termux 8-Core NEON)"
    )

    with patch("bot.task_manager.current_hardware", mock_hw):
        tm = TaskManager(max_concurrent=8)  # Capped to 6
        assert tm.max_concurrent == 6

        release_events = [asyncio.Event() for _ in range(6)]
        acquired_flags = [False] * 6

        async def worker(idx: int):
            async with tm.acquire_encoding_slot(f"task_{idx}"):
                acquired_flags[idx] = True
                await release_events[idx].wait()

        # Start 6 workers
        tasks = [asyncio.create_task(worker(i)) for i in range(6)]
        await asyncio.sleep(0.05)

        # Confirm all 6 acquired immediately
        assert all(acquired_flags)
        assert tm.get_active_encoder_count() == 6
        assert tm.get_queue_length() == 0

        # Attempt 7th task: should be queued
        task_7_acquired = False
        release_event_7 = asyncio.Event()

        async def worker_7():
            nonlocal task_7_acquired
            async with tm.acquire_encoding_slot("task_7"):
                task_7_acquired = True
                await release_event_7.wait()

        task_7 = asyncio.create_task(worker_7())
        await asyncio.sleep(0.05)

        # Confirm task 7 is waiting in queue at position 1
        assert task_7_acquired is False
        assert tm.get_queue_length() == 1
        assert tm.get_queue_position("task_7") == 1
        assert tm.get_active_encoder_count() == 6

        # Release task 0 -> task 7 should acquire immediately
        release_events[0].set()
        await asyncio.sleep(0.05)

        assert task_7_acquired is True
        assert tm.get_queue_position("task_7") == 0
        assert tm.get_queue_length() == 0
        assert tm.get_active_encoder_count() == 6

        # Clean up remaining workers
        for ev in release_events[1:]:
            ev.set()
        release_event_7.set()
        await asyncio.gather(*tasks, task_7)

        assert tm.get_active_encoder_count() == 0
