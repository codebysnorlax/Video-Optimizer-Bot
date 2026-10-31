"""
AI Session Debugger & Diagnostic Logger
Routes all AI agent prompt, tool, and diagnostic traces to logs/ai/ai.log.
storage/debug-ai is retired in favor of the centralized structured logging hierarchy.
"""

import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from core.probe import MediaMetadata, format_bytes, format_duration
from config.settings import settings

logger = logging.getLogger("ai")


def serialize_media_metadata(meta: Optional[MediaMetadata]) -> Optional[Dict[str, Any]]:
    """Converts a MediaMetadata object into a JSON-serializable dictionary."""
    if not meta:
        return None
    return {
        "filename": getattr(meta, "file_name", "unknown"),
        "size_bytes": getattr(meta, "file_size_bytes", 0),
        "formatted_size": format_bytes(getattr(meta, "file_size_bytes", 0)),
        "duration_seconds": round(getattr(meta, "duration_seconds", 0.0), 2),
        "formatted_duration": format_duration(getattr(meta, "duration_seconds", 0.0)),
        "container": getattr(meta, "container_format", "unknown"),
        "has_video": getattr(meta, "has_video", False),
        "resolution": getattr(meta, "resolution_label", "Unknown"),
        "dimensions": f"{meta.width}x{meta.height}" if (getattr(meta, "width", None) and getattr(meta, "height", None)) else None,
        "video_codec": getattr(meta, "video_codec", None),
        "video_bitrate_kbps": getattr(meta, "video_bitrate_kbps", None),
        "fps": round(meta.fps, 2) if getattr(meta, "fps", None) else None,
        "has_audio": getattr(meta, "has_audio", False),
        "audio_codec": getattr(meta, "audio_codec", None),
        "audio_bitrate_kbps": getattr(meta, "audio_bitrate_kbps", None),
        "audio_channels": getattr(meta, "audio_channels", None),
        "audio_sample_rate": getattr(meta, "audio_sample_rate", None),
        "overall_bitrate_kbps": getattr(meta, "overall_bitrate_kbps", None)
    }


def serialize_session_assets(assets: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Converts session assets dictionary into JSON-serializable asset metadata."""
    if not assets:
        return {}
    out: Dict[str, Any] = {}
    for aid, a in assets.items():
        out[aid] = {
            "filename": a.get("filename"),
            "media_type": a.get("media_type", "media"),
            "path": str(a.get("path", "")),
            "meta": serialize_media_metadata(a.get("meta"))
        }
    return out


def compute_media_comparison(before_meta: Optional[MediaMetadata], deliverable: Dict[str, Any]) -> Dict[str, Any]:
    """Computes before-vs-after delta metrics between original source and newly generated deliverable."""
    if not before_meta:
        return {}

    before_size = getattr(before_meta, "file_size_bytes", 0)
    after_size = deliverable.get("size_bytes", 0)
    reduction_pct = 0.0
    if before_size > 0 and after_size > 0:
        reduction_pct = round((1 - (after_size / before_size)) * 100, 2)

    after_probe = deliverable.get("ffprobe") or {}
    after_res = after_probe.get("resolution") or deliverable.get("resolution", "Unknown")
    after_vcodec = after_probe.get("video_codec")
    after_acodec = after_probe.get("audio_codec")

    return {
        "size_before": format_bytes(before_size),
        "size_after": format_bytes(after_size),
        "size_reduction_percent": reduction_pct,
        "resolution_before": before_meta.resolution_label,
        "resolution_after": after_res,
        "video_codec_before": before_meta.video_codec,
        "video_codec_after": after_vcodec,
        "audio_codec_before": before_meta.audio_codec,
        "audio_codec_after": after_acodec,
        "duration_before": format_duration(before_meta.duration_seconds),
        "duration_after": after_probe.get("formatted_duration") or deliverable.get("duration", "Unknown")
    }


class AIDebugLogger:
    """
    Manages recording of AI diagnostic and conversational sessions.
    All traces route into logs/ai/ai.log.
    """

    def __init__(self, log_path: Optional[Path] = None):
        if log_path:
            p = Path(log_path)
            self.log_file = p if p.suffix == ".log" else (p / "ai.log")
        else:
            self.log_file = settings.AI_LOG_DIR / "ai.log"
        self._ensure_log_dir()

    def _ensure_log_dir(self) -> None:
        """Ensures the parent log directory exists."""
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Could not create AI log directory for {self.log_file}: {e}")

    def log_turn(
        self,
        user_id: int,
        task_id: str,
        turn_index: int,
        user_input: str,
        model: str,
        media_before: Optional[MediaMetadata],
        assets_before: Optional[Dict[str, Any]],
        system_prompt: str,
        injected_skills: str,
        assistant_raw: Optional[Dict[str, Any]],
        tool_calls: Optional[List[Dict[str, Any]]],
        tool_results: Optional[List[Dict[str, Any]]],
        primary_result: Optional[Dict[str, Any]],
        final_reply: str,
        elapsed_seconds: float
    ) -> Optional[Path]:
        """
        Records a complete turn of AI interaction into logs/ai/ai.log.
        Never raises exceptions to prevent interrupting the main bot workflow.
        """
        try:
            self._ensure_log_dir()
            now_iso = datetime.now(timezone.utc).isoformat()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            before_serialized = serialize_media_metadata(media_before)
            assets_serialized = serialize_session_assets(assets_before)

            deliverables = (primary_result.get("deliverables") if primary_result else []) or []
            comparison = {}
            if deliverables and media_before:
                comparison = compute_media_comparison(media_before, deliverables[0])

            # Extract executed commands from deliverables or primary result
            executed_commands = []
            if primary_result:
                if primary_result.get("executed_commands"):
                    executed_commands.extend(primary_result.get("executed_commands"))
                elif primary_result.get("ffmpeg_command"):
                    executed_commands.append(primary_result.get("ffmpeg_command"))

            for d in deliverables:
                if d.get("ffmpeg_command") and d.get("ffmpeg_command") not in executed_commands:
                    executed_commands.append(d.get("ffmpeg_command"))

            turn_data = {
                "turn_index": turn_index,
                "timestamp": now_iso,
                "user_id": user_id,
                "task_id": task_id,
                "user_input": user_input,
                "active_model": model,
                "elapsed_seconds": round(elapsed_seconds, 2),
                "media_before": {
                    "primary": before_serialized,
                    "session_assets": assets_serialized
                },
                "ai_decision": {
                    "injected_skills_snapshot": injected_skills,
                    "raw_assistant_reply": assistant_raw,
                    "tool_calls": tool_calls or []
                },
                "execution": {
                    "status": primary_result.get("status") if primary_result else "none",
                    "plan_title": primary_result.get("plan_title") if primary_result else None,
                    "executed_commands": executed_commands,
                    "error": primary_result.get("error") if primary_result else None
                },
                "media_after": {
                    "deliverables": deliverables,
                    "comparison": comparison
                },
                "final_reply": final_reply
            }

            status_str = primary_result.get("status", "none") if primary_result else "none"
            commands_str = ", ".join(executed_commands) if executed_commands else "none"

            log_entry = (
                f"[{now_str}] [AI_TURN] Turn {turn_index} | User: {user_id} | Task: {task_id} | "
                f"Model: {model} | Elapsed: {elapsed_seconds:.2f}s | Status: {status_str} | "
                f"Commands: {commands_str}\n"
                f"  Input: {user_input}\n"
                f"  Reply: {final_reply.strip() if final_reply else 'None'}\n"
                f"  Trace: {json.dumps(turn_data, default=str)}\n"
            )

            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(log_entry)

            logger.info(
                f"AI Turn {turn_index} [User: {user_id}, Task: {task_id}] "
                f"Input: \"{user_input}\" -> Status: {status_str} ({elapsed_seconds:.2f}s)"
            )

            return self.log_file

        except Exception as e:
            logger.warning(f"AIDebugLogger error during log_turn: {e}", exc_info=True)
            return None

    def log_session_event(self, user_id: int, task_id: str, event_type: str, details: Dict[str, Any]) -> None:
        """Logs arbitrary session lifecycle events into logs/ai/ai.log."""
        try:
            self._ensure_log_dir()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                "user_id": user_id,
                "task_id": task_id,
                **details
            }
            log_entry = (
                f"[{now_str}] [AI_EVENT] {event_type} | User: {user_id} | Task: {task_id} | "
                f"Details: {json.dumps(record, default=str)}\n"
            )
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(log_entry)

            logger.info(f"AI Event {event_type} [User: {user_id}, Task: {task_id}]: {details}")
        except Exception as e:
            logger.warning(f"Could not record session event {event_type}: {e}")


# Global singleton debug logger instance
ai_debug_logger = AIDebugLogger()
