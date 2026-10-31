"""
AI Video Engineer Agent (ReAct Loop)
Manages multi-turn conversation, dynamically injects skills, executes sandboxed tools,
and provides technical diagnosis or video optimization.
"""

import asyncio
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Awaitable, Tuple, Set
from core.probe import MediaMetadata, format_bytes, format_duration
from core.hardware import current_hardware
from core.engine import ProgressCallback
from core.ai.client import ai_client, OpenRouterClient
from core.ai.skills import skill_registry
from core.ai.tools import AI_TOOLS_SCHEMA, SafeToolExecutor
from core.ai.debug_logger import ai_debug_logger
from config.settings import settings

logger = logging.getLogger(__name__)

MAX_AI_WORKSPACES = 5


def prune_ai_workspaces(
    workplace_dir: Optional[Path] = None,
    max_workspaces: int = MAX_AI_WORKSPACES,
    protected_dirs: Optional[Set[str]] = None
) -> List[Path]:
    """
    Enforces a strict LRU ceiling on the number of session directories in storage/Ai_Workplace/.
    Deletes the oldest unpinned directories by modification time (mtime) until at most max_workspaces remain.
    Protects active tasks and sessions in protected_dirs from being pruned.
    """
    root = Path(workplace_dir) if workplace_dir else settings.AI_WORKPLACE_DIR
    if not root.exists() or not root.is_dir():
        return []

    protected = set(protected_dirs) if protected_dirs else set()
    try:
        from bot.task_manager import task_manager
        for task in task_manager._active_tasks.values():
            if getattr(task, "is_processing", False) or getattr(task, "ai_session_active", False) or getattr(task, "current_stage", "idle") != "idle":
                protected.add(f"{task.user_id}_{task.task_id}")
                if getattr(task, "ai_workplace_dir", None):
                    protected.add(task.ai_workplace_dir.name)
    except Exception:
        pass

    dirs = [d for d in root.iterdir() if d.is_dir()]
    if len(dirs) <= max_workspaces:
        return []

    unprotected = [d for d in dirs if d.name not in protected and str(d) not in protected]
    unprotected.sort(key=lambda p: p.stat().st_mtime)

    excess = len(dirs) - max_workspaces
    pruned: List[Path] = []

    for d in unprotected[:excess]:
        try:
            shutil.rmtree(d, ignore_errors=True)
            pruned.append(d)
            sys_logger = logging.getLogger("system")
            sys_logger.info(f"LRU Workspace Pruner: pruned older session workspace {d.name}")
        except Exception as e:
            logger.warning(f"Could not prune workspace directory {d}: {e}")

    return pruned


def sanitize_message_history(history: List[Dict[str, Any]], complete_trailing: bool = True) -> List[Dict[str, Any]]:
    """
    Sanitizes message history for OpenAI / OpenRouter Chat Completion API compliance.
    Ensures that every 'tool' message sequence is properly opened by an 'assistant' message
    with 'tool_calls' and properly closed by an 'assistant' response before any subsequent 'user' message.
    If complete_trailing is True, also appends a synthetic assistant message if the history ends with a tool message.
    When complete_trailing is False (default for follow-up turns in ReAct loop), trailing tool messages are kept
    so the LLM receives the tool response and generates the final assistant response.
    """
    if not history:
        return []

    sanitized: List[Dict[str, Any]] = []
    in_tool_sequence = False

    for msg in history:
        role = msg.get("role")

        if role == "tool":
            sanitized.append(msg)
            in_tool_sequence = True
        elif role == "user":
            if in_tool_sequence:
                # Close the tool sequence with a synthetic assistant message before user message
                sanitized.append({
                    "role": "assistant",
                    "content": "Operation completed."
                })
                in_tool_sequence = False
            sanitized.append(msg)
        elif role == "assistant":
            in_tool_sequence = False
            sanitized.append(msg)
        else:
            in_tool_sequence = False
            sanitized.append(msg)

    if complete_trailing and in_tool_sequence:
        sanitized.append({
            "role": "assistant",
            "content": "Operation completed."
        })

    # Post-sanitization check: if the first message has role == "tool", prepend synthetic assistant
    if sanitized and sanitized[0].get("role") == "tool":
        sanitized.insert(0, {
            "role": "assistant",
            "content": "I performed the previous operation."
        })

    return sanitized


def compress_old_history(history: List[Dict[str, Any]], keep_recent: int = 20) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Splits history into old (everything before the last keep_recent messages) and recent (last keep_recent messages).
    Builds a one-paragraph summary of old turns listing all asset keys, operations performed, and user goals.
    Returns (summary_text, recent_messages).
    """
    if not history:
        return "", []

    if len(history) <= keep_recent:
        return "", list(history)

    old = history[:-keep_recent]
    recent = list(history[-keep_recent:])

    user_goals: List[str] = []
    operations: List[str] = []
    asset_keys: List[str] = []
    seen_assets = set()

    for msg in old:
        role = msg.get("role")
        content = msg.get("content")

        # Extract user goals
        if role == "user" and isinstance(content, str):
            goal = content.strip()
            if goal and goal not in user_goals:
                if len(goal) > 120:
                    goal = goal[:117] + "..."
                user_goals.append(goal)

        # Extract operation names from tool messages
        if role == "tool":
            op_name = None
            if isinstance(content, str):
                try:
                    data = json.loads(content)
                    if isinstance(data, dict):
                        op_name = (
                            data.get("plan_title")
                            or data.get("operation")
                            or data.get("name")
                            or data.get("recommended_action")
                        )
                except Exception:
                    pass
                if not op_name:
                    op_name = msg.get("name") or content[:60].strip()
            elif isinstance(content, dict):
                op_name = (
                    content.get("plan_title")
                    or content.get("operation")
                    or content.get("name")
                    or content.get("recommended_action")
                )
            if not op_name and msg.get("name"):
                op_name = msg.get("name")

            if op_name:
                op_str = str(op_name).strip()
                if op_str and op_str not in operations:
                    operations.append(op_str)

        # Extract asset keys like [v1], [a1], etc. from any message
        msg_str = json.dumps(msg, default=str) if isinstance(msg, dict) else str(msg)
        for match in re.findall(r'\[([a-zA-Z0-9_-]+)\]', msg_str):
            key = f"[{match}]"
            if key not in seen_assets:
                seen_assets.add(key)
                asset_keys.append(key)
        for match in re.findall(r'\b([vas]\d+)\b', msg_str, re.IGNORECASE):
            key = f"[{match.lower()}]"
            if key not in seen_assets:
                seen_assets.add(key)
                asset_keys.append(key)

    summary_parts = []
    if user_goals:
        summary_parts.append(f"User goals: {'; '.join(user_goals)}.")
    if operations:
        summary_parts.append(f"Operations performed: {', '.join(operations)}.")
    if asset_keys:
        summary_parts.append(f"Assets referenced: {', '.join(asset_keys)}.")

    summary_text = " ".join(summary_parts)
    if not summary_text and old:
        summary_text = "Previous conversation turns took place."

    return summary_text, recent


def guard_truncation_boundary(
    full_history: List[Dict[str, Any]],
    recent: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """
    Ensures that a truncated history does not begin with an orphaned 'tool' message.
    If recent begins with a 'tool' message, scans backward in full_history from the cut point
    to include the preceding 'assistant' message (which contains 'tool_calls').
    Supports both signatures:
      guard_truncation_boundary(full_history, recent)
      guard_truncation_boundary(messages)
    """
    if recent is None:
        messages = list(full_history)
        if messages and messages[0].get("role") == "tool":
            return [{
                "role": "assistant",
                "content": "I performed the previous operation."
            }] + messages
        return messages

    if not recent:
        return []

    if recent[0].get("role") != "tool":
        return list(recent)

    cut_point = len(full_history) - len(recent)
    if cut_point < 0 or cut_point >= len(full_history) or full_history[cut_point] != recent[0]:
        found = False
        for k in range(len(full_history) - 1, -1, -1):
            if full_history[k] == recent[0]:
                cut_point = k
                found = True
                break
        if not found:
            cut_point = max(0, len(full_history) - len(recent))

    target_assistant = None
    for i in range(cut_point - 1, -1, -1):
        msg = full_history[i]
        if msg.get("role") == "assistant":
            target_assistant = msg
            if msg.get("tool_calls"):
                break

    if target_assistant:
        return [target_assistant] + list(recent)

    return [{
        "role": "assistant",
        "content": "I performed the previous operation."
    }] + list(recent)


class VideoAIAgent:
    """Autonomous Video Optimization & Diagnostic Agent."""

    def __init__(
        self,
        user_id: int,
        task_id: str,
        input_path: Path,
        meta: Optional[MediaMetadata] = None,
        client: Optional[OpenRouterClient] = None,
        preferred_model: Optional[str] = None,
        assets: Optional[Dict[str, Any]] = None
    ):
        self.user_id = user_id
        self.task_id = task_id
        self.input_path = input_path
        self.meta = meta
        self.client = client or ai_client
        self.preferred_model = preferred_model
        self.assets = assets or {}
        self.history: List[Dict[str, Any]] = []
        self.turn_counter: int = 0
        self.tool_executor = SafeToolExecutor(user_id, task_id, input_path, meta, assets=self.assets)
        # Enforce LRU workspace containment on session initialization
        prune_ai_workspaces(protected_dirs={f"{user_id}_{task_id}"})

    def _build_base_prompt(self) -> str:
        """Constructs the base system prompt containing behavioral rules, safety boundaries, and tool specifications (under 800 tokens)."""
        # Video context summary
        video_info = "No video metadata available."
        if self.meta:
            fps_str = f"{int(self.meta.fps)}fps" if self.meta.fps else "N/A"
            video_info = (
                f"- Filename: {self.meta.file_name}\n"
                f"- Size: {format_bytes(self.meta.file_size_bytes)} ({self.meta.file_size_bytes} bytes)\n"
                f"- Duration: {format_duration(self.meta.duration_seconds)} ({int(self.meta.duration_seconds)}s)\n"
                f"- Resolution: {self.meta.resolution_label} ({self.meta.width}x{self.meta.height})\n"
                f"- Video Codec: {self.meta.video_codec} @ ~{self.meta.video_bitrate_kbps or 'unknown'} kbps, {fps_str}\n"
                f"- Audio Codec: {self.meta.audio_codec or 'None'} @ ~{self.meta.audio_bitrate_kbps or 'unknown'} kbps\n"
                f"- Overall Bitrate: ~{self.meta.overall_bitrate_kbps} kbps"
            )

        # Available session assets
        assets_section = ""
        if self.assets:
            lines = []
            for aid, a in self.assets.items():
                m = a.get("meta")
                res = f"Resolution: {m.resolution_label}, " if (m and m.has_video) else ""
                dur = f"Duration: {int(m.duration_seconds)}s, " if m else ""
                size = f"Size: {format_bytes(m.file_size_bytes)}" if m else ""
                tag = " [ORIGINAL USER SOURCE VIDEO]" if aid == "v1" else ""
                lines.append(f"- [{aid}] {a['filename']} ({a.get('media_type', 'media').upper()}: {res}{dur}{size}){tag}")
            assets_section = "### AVAILABLE SESSION ASSETS:\n" + "\n".join(lines) + "\n\n"
        elif self.meta:
            assets_section = f"### AVAILABLE SESSION ASSETS:\n- [v1] {self.meta.file_name} (VIDEO: {self.meta.resolution_label}, {int(self.meta.duration_seconds)}s) [ORIGINAL USER SOURCE VIDEO]\n\n"

        # Host environment info
        host_info = (
            f"- Host OS: {current_hardware.os_name} ({current_hardware.arch})\n"
            f"- Platform: {current_hardware.description}\n"
            f"- GPU / Video Encoder: {current_hardware.video_encoder}\n"
            f"- CPU Cores: {current_hardware.cpu_cores}\n"
            f"- Available RAM: ~{current_hardware.available_ram_mb} MB"
        )

        return (
            "You are the **AI Video & Audio Engineer** for a high-performance Telegram bot.\n"
            "You diagnose media issues and execute custom optimizations, format conversions, stream extractions, audio replacement, or multi-file edits.\n\n"
            f"{assets_section}"
            "### CURRENT PRIMARY VIDEO METADATA:\n"
            f"{video_info}\n\n"
            "### HOST ENVIRONMENT CONSTRAINTS:\n"
            f"{host_info}\n\n"
            "### OPERATIONAL GUIDELINES (STRICT):\n"
            "1. **Action Requests vs Informational Questions:**\n"
            "   - You ALREADY have verified metadata. Never call `diagnose_video` for editing action requests (e.g. optimize, mute, downscale). Call `execute_ffmpeg_plan` or ask for missing files. `diagnose_video` is STRICTLY for pure questions like 'Why does this video lag on my phone?'.\n"
            "2. **Resolution & Quality Guardrails (DO NOT DROP QUALITY):**\n"
            "   - If source > 1080p (4K UHD/1440p): Downscale ONLY to 1080p FHD (landscape: scale=-2:1080, portrait: scale=1080:-2). NEVER downscale to 720p/480p/360p unless explicitly asked.\n"
            "   - If source <= 1080p: KEEP NATIVE RESOLUTION. Use CRF 20-22 with libx264 for crisp output.\n"
            "   - For any processing on sources > 1080p, always combine filters with downscaling to 1080p FHD.\n"
            "3. **Audio Removal & Missing Audio Requests:**\n"
            "   - If user asks to replace/add audio but new audio asset ([a1]) is missing:\n"
            "     * Deliver optimized muted video immediately if requested (mute_video_only=True) and prompt user to upload the new audio track, OR prompt user to drop the audio file into chat.\n"
            "4. **Post-Execution Logical Response:**\n"
            "   - If waiting for additional files, prompt for them. If complete, ask what they would like to do next.\n"
            "5. **Multi-Asset Referencing & Target Selection ('original' vs 'latest'):**\n"
            "   - If user asks for 'original video', 'source video', 'from scratch', or 'first video': set `inputs: ['v1']`. For follow-ups or deliverables, use latest asset.\n"
            "6. **Memory Safety on Snapdragon / Low RAM:** NEVER buffer unbounded frames into RAM. If reversing video > 30s or > 720p, downscale to 720p.\n"
            "7. **No Emojis:** Do NOT include any emojis or emoji symbols in responses. Use plain text.\n"
            "8. **New Asset Uploaded:** If user sends a new asset after an edit request, immediately call `execute_ffmpeg_plan` to merge/replace.\n"
            "9. **Unrealistic & Impossible Demands (STRICT REFUSAL GUARDRAIL):**\n"
            "   - Never fake upscale or convert 1080p to 4K: Upscaling a 1080p, 720p, 480p, or 360p video to 4K UHD is an unrealistic demand that cannot synthesize optical detail. Refuse in plain conversational text stating that upscaling to 4K is an unrealistic demand.\n"
            "   - Never extract audio from silent video without an audio stream.\n"
            "10. **Output FFprobe Awareness & Post-Execution Response:**\n"
            "   - Acknowledge generated deliverables citing verified ffprobe metadata (resolution, duration, size) and ask next steps."
        )

    def _build_system_prompt(self, user_prompt: str) -> str:
        """Constructs a comprehensive, token-efficient system prompt with video metadata, session assets, and dynamic skills."""
        base_prompt = self._build_base_prompt()
        skills_context = skill_registry.get_relevant_skills(user_prompt)
        if skills_context:
            return (
                f"{base_prompt}\n\n"
                "### RELEVANT KNOWLEDGE BASE & RECIPES:\n"
                f"{skills_context}"
            )
        return base_prompt

    async def process_message(
        self,
        user_text: str,
        progress_callback: Optional[ProgressCallback] = None,
        on_process_started: Optional[Callable[[Any], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes one turn of the ReAct reasoning loop.
        Returns a structured dictionary with agent response, tool execution results, and status.
        Audits and prunes workspace to 5-directory ceiling upon turn completion or abort.
        """
        try:
            self.turn_counter += 1
            turn_idx = self.turn_counter
            start_time = time.time()
            media_before = self.meta
            assets_before = dict(self.assets) if self.assets else {}
            active_model = self.preferred_model or getattr(self.client, "default_model", settings.OPENROUTER_MODEL)

            # Append user message
            self.history.append({"role": "user", "content": user_text})

            # Build payload with dynamic system prompt and sanitized history
            system_prompt = self._build_system_prompt(user_text)
            sanitized_history = sanitize_message_history(self.history)
            summary_text, recent_window = compress_old_history(sanitized_history)
            recent_window = guard_truncation_boundary(sanitized_history, recent_window)
            messages = [{"role": "system", "content": system_prompt}]
            if summary_text:
                messages.append({"role": "system", "content": f"Previous session context: {summary_text}"})
            messages.extend(recent_window)

            try:
                assistant_msg = await self.client.chat_completion(
                    messages=messages,
                    tools=AI_TOOLS_SCHEMA,
                    preferred_model=self.preferred_model
                )
            except Exception as e:
                logger.error(f"OpenRouter API error: {e}")
                err_dict = {
                    "status": "error",
                    "error": f"AI service error: {str(e)}",
                    "text": "Sorry, I encountered an issue connecting to the AI service. Please verify your OpenRouter API key or try again."
                }
                ai_debug_logger.log_turn(
                    user_id=self.user_id,
                    task_id=self.task_id,
                    turn_index=turn_idx,
                    user_input=user_text,
                    model=active_model,
                    media_before=media_before,
                    assets_before=assets_before,
                    system_prompt=system_prompt,
                    injected_skills=skill_registry.get_relevant_skills(user_text),
                    assistant_raw=None,
                    tool_calls=None,
                    tool_results=None,
                    primary_result=err_dict,
                    final_reply=err_dict["text"],
                    elapsed_seconds=time.time() - start_time
                )
                return err_dict

            tool_calls = assistant_msg.get("tool_calls")
            content = (assistant_msg.get("content") or "").strip()

            # If no tool calls, pure conversational reply
            if not tool_calls:
                if not content:
                    content = "I have processed your request. What would you like to do next?"
                self.history.append({"role": "assistant", "content": content})
                text_result = {
                    "status": "success",
                    "type": "text",
                    "text": content
                }
                ai_debug_logger.log_turn(
                    user_id=self.user_id,
                    task_id=self.task_id,
                    turn_index=turn_idx,
                    user_input=user_text,
                    model=active_model,
                    media_before=media_before,
                    assets_before=assets_before,
                    system_prompt=system_prompt,
                    injected_skills=skill_registry.get_relevant_skills(user_text),
                    assistant_raw=assistant_msg,
                    tool_calls=None,
                    tool_results=None,
                    primary_result=text_result,
                    final_reply=content,
                    elapsed_seconds=time.time() - start_time
                )
                try:
                    from database.models import save_ai_session
                    await save_ai_session(
                        self.user_id,
                        self.task_id,
                        self.history,
                        self.assets,
                        self.preferred_model or "",
                        str(self.input_path) if self.input_path else ""
                    )
                except Exception as e:
                    logger.warning(f"Error persisting AI session: {e}")
                return text_result

            # Handle Tool Calls
            self.history.append(assistant_msg)
            execution_results = []

            for call in tool_calls:
                fn_name = call.get("function", {}).get("name")
                raw_args = call.get("function", {}).get("arguments", "{}")
                call_id = call.get("id", "call_default")

                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}

                logger.info(f"AI Agent executing tool: {fn_name} with args: {args}")

                if fn_name == "diagnose_video":
                    res = await self.tool_executor.run_diagnose(
                        explanation=args.get("explanation", ""),
                        recommended_action=args.get("recommended_action", ""),
                        estimated_reduction_percent=args.get("estimated_reduction_percent", 0)
                    )
                    execution_results.append(res)
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": fn_name,
                        "content": json.dumps(res, default=str)
                    })

                elif fn_name == "execute_ffmpeg_plan":
                    plan_title = args.get("plan_title", "Custom AI Optimization")
                    if progress_callback:
                        try:
                            await progress_callback(
                                0.0,
                                "starting",
                                0,
                                0.0,
                                step_label=f"Reasoning complete. Starting {plan_title}..."
                            )
                        except Exception as p_err:
                            logger.debug(f"Initial progress notification error: {p_err}")

                    # Plan preview notification
                    if progress_callback:
                        codec = args.get("video_codec", "libx264")
                        crf_val = args.get("crf", 22)
                        vf = args.get("vf_filter", "")
                        resolution = ""
                        if vf and "scale" in vf:
                            import re as _re
                            scale_match = _re.search(r'scale[=:]([^,;]+)', vf)
                            if scale_match:
                                resolution = scale_match.group(1)

                        est_time = ""
                        if self.meta and self.meta.duration_seconds > 0:
                            # Estimate: input_duration / 1.5x speed factor for libx264 on SDM636
                            est_seconds = int(self.meta.duration_seconds / 1.5)
                            from core.ai.tools import _parse_speed_factor
                            speed_factor = _parse_speed_factor(f"{vf} {args.get('af_filter', '')}")
                            if speed_factor and speed_factor > 0:
                                est_seconds = int(est_seconds * speed_factor)
                            est_time = f"~{est_seconds}s"

                        preview_parts = [f"AI Plan: {plan_title}"]
                        if codec and codec != "none":
                            if codec == "copy":
                                preview_parts.append("Codec: copy (Passthrough)")
                            else:
                                crf_str = f" CRF {crf_val}" if crf_val else ""
                                if current_hardware.hwaccel_type in ("vaapi", "nvenc"):
                                    preview_parts.append(f"Codec: {codec}{crf_str} (GPU: {current_hardware.video_encoder})")
                                else:
                                    preview_parts.append(f"Codec: {codec}{crf_str}")
                        if resolution:
                            preview_parts.append(f"Resolution: {resolution}")
                        if est_time:
                            preview_parts.append(f"Estimated time: {est_time}")

                        preview_msg = " | ".join(preview_parts)
                        try:
                            await progress_callback(0.0, "starting", 0, 0.0, step_label=preview_msg)
                        except Exception:
                            pass

                    res = await self.tool_executor.run_execute_ffmpeg(
                        plan_title=plan_title,
                        output_filename=args.get("output_filename", "ai_optimized.mp4"),
                        operations=args.get("operations"),
                        inputs=args.get("inputs"),
                        extract_audio_only=args.get("extract_audio_only", False),
                        mute_video_only=args.get("mute_video_only", False),
                        vf_filter=args.get("vf_filter"),
                        af_filter=args.get("af_filter"),
                        video_codec=args.get("video_codec", "libx264"),
                        crf=args.get("crf", 22),
                        audio_codec=args.get("audio_codec", "aac"),
                        stream_mapping=args.get("stream_mapping"),
                        extra_args=args.get("extra_args"),
                        progress_callback=progress_callback,
                        on_process_started=on_process_started
                    )
                    execution_results.append(res)
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": fn_name,
                        "content": json.dumps(res, default=str)
                    })

                elif fn_name == "verify_media":
                    res = await self.tool_executor.run_verify_media(args.get("filename", ""))
                    execution_results.append(res)
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": fn_name,
                        "content": json.dumps(res, default=str)
                    })

                elif fn_name == "list_workspace_files":
                    res = await self.tool_executor.run_list_workspace_files()
                    execution_results.append(res)
                    self.history.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": fn_name,
                        "content": json.dumps(res, default=str)
                    })

            # Primary tool result
            primary_result = execution_results[0] if execution_results else {"status": "success", "type": "none"}

            # Follow-up completion turn so the model evaluates tool results and produces assistant response
            # tools MUST be None here so the model provides a final conversational answer instead of an unexpected tool call
            if progress_callback:
                try:
                    await progress_callback(100.0, "done", 0, 0.0, step_label="Transcoding complete. Formulating AI summary...")
                except Exception:
                    pass

            try:
                sanitized_follow_up = sanitize_message_history(self.history, complete_trailing=False)
                fu_summary, fu_recent = compress_old_history(sanitized_follow_up)
                fu_recent = guard_truncation_boundary(sanitized_follow_up, fu_recent)
                follow_up_messages = [{"role": "system", "content": self._build_system_prompt(user_text)}]
                if fu_summary:
                    follow_up_messages.append({"role": "system", "content": f"Previous session context: {fu_summary}"})
                follow_up_messages.extend(fu_recent)
                follow_up_msg = await asyncio.wait_for(
                    self.client.chat_completion(
                        messages=follow_up_messages,
                        tools=None,
                        preferred_model=self.preferred_model
                    ),
                    timeout=30.0
                )
                follow_up_content = (follow_up_msg.get("content") or "").strip()
                if not follow_up_content:
                    out_ffprobe = primary_result.get("ffprobe") or {}
                    res_str = out_ffprobe.get("resolution", primary_result.get("resolution", ""))
                    size_str = out_ffprobe.get("formatted_size", primary_result.get("formatted_size", ""))
                    dur_str = out_ffprobe.get("formatted_duration", primary_result.get("duration", ""))
                    spec_note = f" ({res_str}, {size_str}, {dur_str})" if (res_str and size_str) else ""
                    follow_up_content = (
                        f"I have successfully executed the operation: {primary_result.get('plan_title', 'video processing')}{spec_note}. "
                        "What would you like to do next with this file?"
                    )
                self.history.append({"role": "assistant", "content": follow_up_content})
                primary_result["assistant_text"] = follow_up_content
            except Exception as follow_up_err:
                logger.warning(f"Error in follow-up AI completion: {type(follow_up_err).__name__} ({follow_up_err or 'timeout'})")
                if primary_result.get("status") == "error":
                    fallback_text = (
                        f"The operation could not be completed: {primary_result.get('error', 'an error occurred during FFmpeg execution')}."
                    )
                else:
                    out_ffprobe = primary_result.get("ffprobe") or {}
                    res_str = out_ffprobe.get("resolution", primary_result.get("resolution", ""))
                    size_str = out_ffprobe.get("formatted_size", primary_result.get("formatted_size", ""))
                    dur_str = out_ffprobe.get("formatted_duration", primary_result.get("duration", ""))
                    spec_note = f" ({res_str}, {size_str}, {dur_str})" if (res_str and size_str) else ""
                    fallback_text = (
                        f"I have executed the operation: {primary_result.get('plan_title', 'video processing')}{spec_note}. "
                        "What would you like to do next with this file?"
                    )
                self.history.append({"role": "assistant", "content": fallback_text})
                primary_result["assistant_text"] = fallback_text

            ai_debug_logger.log_turn(
                user_id=self.user_id,
                task_id=self.task_id,
                turn_index=turn_idx,
                user_input=user_text,
                model=active_model,
                media_before=media_before,
                assets_before=assets_before,
                system_prompt=system_prompt,
                injected_skills=skill_registry.get_relevant_skills(user_text),
                assistant_raw=assistant_msg,
                tool_calls=tool_calls,
                tool_results=execution_results,
                primary_result=primary_result,
                final_reply=primary_result.get("assistant_text") or primary_result.get("text", ""),
                elapsed_seconds=time.time() - start_time
            )

            try:
                from database.models import save_ai_session
                await save_ai_session(
                    self.user_id,
                    self.task_id,
                    self.history,
                    self.assets,
                    self.preferred_model or "",
                    str(self.input_path) if self.input_path else ""
                )
            except Exception as e:
                logger.warning(f"Error persisting AI session: {e}")

            return primary_result

        finally:
            prune_ai_workspaces(protected_dirs={f"{self.user_id}_{self.task_id}"})
