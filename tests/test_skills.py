"""
Unit tests for BM25 and intent-based skill catalog retrieval in core/ai/skills.py
"""

from pathlib import Path
from core.ai.skills import BM25Index, SkillRegistry, skill_registry


def test_bm25_index_basic_scoring():
    """Verifies that the pure Python BM25Index scores relevant documents highest."""
    idx = BM25Index()
    docs = {
        "diag": "diagnose video playback lag stutter dropped frames overheating",
        "creative": "creative effects reverse aspect ratio black and white sepia filters",
        "ffmpeg": "ffmpeg convert compress video audio stream extraction remux"
    }
    idx.build(docs)

    scores_diag = dict(idx.score("why does my video lag and stutter"))
    assert scores_diag["diag"] > scores_diag["creative"]
    assert scores_diag["diag"] > scores_diag["ffmpeg"]

    scores_creative = dict(idx.score("turn this video black and white"))
    assert scores_creative["creative"] > scores_creative["diag"]
    assert scores_creative["creative"] > scores_creative["ffmpeg"]


def test_skill_registry_intent_matching():
    """Verifies that SkillRegistry.match_skills correctly isolates matching skills per query."""
    # 1. Diagnostic query
    diag_matches = skill_registry.match_skills("why does my video lag on phone")
    assert "video_diagnosis" in diag_matches
    assert "creative_fx" not in diag_matches

    # 2. Creative query
    creative_matches = skill_registry.match_skills("make my video black and white with sepia")
    assert "creative_fx" in creative_matches
    assert "video_diagnosis" not in creative_matches

    # 3. General conversion query (suppresses specialized skills)
    ffmpeg_matches = skill_registry.match_skills("convert this clip to mp4 and compress")
    assert "ffmpeg" in ffmpeg_matches
    assert "creative_fx" not in ffmpeg_matches
    assert "video_diagnosis" not in ffmpeg_matches


def test_skill_registry_content_injection():
    """Verifies that get_relevant_skills injects the matched skill markdown content."""
    prompt = "make my video black and white"
    injected = skill_registry.get_relevant_skills(prompt)

    assert "CREATIVE_FX" in injected
    assert "VIDEO_DIAGNOSIS" not in injected


def test_base_system_prompt_token_bounding(tmp_path):
    """Verifies that VideoAIAgent._build_base_prompt() contains no hardcoded recipes and is under 800 tokens."""
    from core.ai.agent import VideoAIAgent

    agent = VideoAIAgent(user_id=101, task_id="t_prompt_test", input_path=tmp_path / "dummy.mp4")
    base_prompt = agent._build_base_prompt()

    # Verify hardcoded recipes are stripped out
    assert "colorchannelmixer" not in base_prompt
    assert "transpose=1" not in base_prompt
    assert "unsharp=5:5:1.0" not in base_prompt
    assert "aloop=loop=-1" not in base_prompt

    # Verify token estimations remain strictly under 800 tokens
    word_count = len(base_prompt.split())
    estimated_tokens = int(word_count / 0.75)
    char_estimated_tokens = int(len(base_prompt) / 4)

    assert estimated_tokens < 800, f"Word-based estimated tokens {estimated_tokens} exceeds 800"
    assert char_estimated_tokens < 800, f"Char-based estimated tokens {char_estimated_tokens} exceeds 800"


def test_agent_dynamic_skill_injection(tmp_path):
    """Verifies that VideoAIAgent._build_system_prompt selectively injects matched skills."""
    from core.ai.agent import VideoAIAgent

    agent = VideoAIAgent(user_id=101, task_id="t_dynamic_test", input_path=tmp_path / "dummy.mp4")

    # 1. Creative request triggers creative_fx skill injection
    creative_prompt = agent._build_system_prompt("turn this clip black and white with sepia")
    assert "CREATIVE_FX" in creative_prompt
    assert "VIDEO_DIAGNOSIS" not in creative_prompt

    # 2. Diagnostic request triggers video_diagnosis skill injection
    diag_prompt = agent._build_system_prompt("why does this video lag on my phone")
    assert "VIDEO_DIAGNOSIS" in diag_prompt
    assert "CREATIVE_FX" not in diag_prompt

    # 3. Base request has no specialized skills injected
    general_prompt = agent._build_system_prompt("optimize this video to mp4")
    assert "CREATIVE_FX" not in general_prompt
    assert "VIDEO_DIAGNOSIS" not in general_prompt

