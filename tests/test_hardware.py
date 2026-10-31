"""
Unit Tests for Hardware & Platform Detection
"""

import pytest
from core.hardware import detect_hardware, HardwareProfile


def test_detect_hardware():
    profile = detect_hardware()
    assert isinstance(profile, HardwareProfile)
    assert profile.cpu_cores > 0
    assert profile.total_ram_mb > 0
    assert profile.video_encoder in ["h264_vaapi", "h264_nvenc", "libx264"]
    assert profile.hwaccel_type in ["vaapi", "nvenc", "cpu"]


def test_hardware_ffmpeg_args_generation():
    profile = detect_hardware()
    args = profile.get_ffmpeg_video_args(target_codec="h264", crf=22, maxrate_kbps=4000)
    assert isinstance(args, list)
    assert len(args) > 0
    # Must have video codec flag
    assert "-c:v" in args


def test_procfs_meminfo_parsing():
    """Verifies that detect_hardware parses /proc/meminfo accurately."""
    from unittest.mock import patch, mock_open

    mock_meminfo = (
        "MemTotal:        3840000 kB\n"
        "MemFree:          500000 kB\n"
        "MemAvailable:    2150000 kB\n"
        "Buffers:          100000 kB\n"
    )

    with patch("os.path.exists", side_effect=lambda p: True if p == "/proc/meminfo" else False):
        with patch("builtins.open", mock_open(read_data=mock_meminfo)):
            profile = detect_hardware()
            assert profile.total_ram_mb == 3840000 // 1024
            assert profile.available_ram_mb == 2150000 // 1024


def test_procfs_meminfo_fallback_when_absent():
    """Verifies that detect_hardware falls back gracefully when /proc/meminfo does not exist."""
    from unittest.mock import patch

    with patch("os.path.exists", return_value=False):
        profile = detect_hardware()
        assert profile.total_ram_mb > 0
        assert profile.available_ram_mb > 0


def test_zero_psutil_dependency():
    """Verifies that hardware detection functions without psutil."""
    import sys
    from unittest.mock import patch

    with patch.dict(sys.modules, {"psutil": None}):
        profile = detect_hardware()
        assert profile.total_ram_mb > 0
        assert profile.cpu_cores > 0

