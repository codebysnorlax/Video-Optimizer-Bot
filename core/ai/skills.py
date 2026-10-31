"""
Modular Skills Registry & BM25 Dynamic Retrieval Engine
Discovers, indexes, and selectively retrieves specialized skill markdown instructions
based on user intent and BM25 relevance scoring.
"""

import os
import re
import math
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set
from collections import Counter

logger = logging.getLogger(__name__)

# Base skills directory (defaults to project root / skills)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_SKILLS_DIR = BASE_DIR / "skills"


@dataclass
class Skill:
    name: str
    description: str
    content: str
    triggers: List[str] = field(default_factory=list)
    references: Dict[str, str] = field(default_factory=dict)


class BM25Index:
    """Zero-dependency, pure Python BM25 ranking index."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_ids: List[str] = []
        self.doc_lengths: List[int] = []
        self.avgdl: float = 0.0
        self.doc_freqs: Dict[str, int] = {}
        self.term_freqs: List[Counter] = []
        self.num_docs: int = 0

    @staticmethod
    def tokenize(text: str) -> List[str]:
        """Extracts normalized lowercase tokens from text."""
        return [w.lower() for w in re.findall(r"[a-zA-Z0-9_\-]+", text) if len(w) > 1]

    def build(self, documents: Dict[str, str]) -> None:
        """Indexes a dictionary of {doc_id: document_text}."""
        self.doc_ids = list(documents.keys())
        self.num_docs = len(self.doc_ids)
        self.doc_lengths = []
        self.term_freqs = []
        self.doc_freqs = {}

        if self.num_docs == 0:
            self.avgdl = 0.0
            return

        total_tokens = 0
        for doc_id in self.doc_ids:
            tokens = self.tokenize(documents[doc_id])
            self.doc_lengths.append(len(tokens))
            total_tokens += len(tokens)
            tf = Counter(tokens)
            self.term_freqs.append(tf)
            for term in tf.keys():
                self.doc_freqs[term] = self.doc_freqs.get(term, 0) + 1

        self.avgdl = total_tokens / self.num_docs

    def score(self, query: str) -> List[Tuple[str, float]]:
        """Scores documents against query string using BM25."""
        tokens = self.tokenize(query)
        if not tokens or self.num_docs == 0:
            return [(doc_id, 0.0) for doc_id in self.doc_ids]

        scores: List[float] = [0.0] * self.num_docs
        for token in tokens:
            df = self.doc_freqs.get(token, 0)
            if df == 0:
                continue
            # Robertson-Spärck Jones IDF formula
            idf = math.log((self.num_docs - df + 0.5) / (df + 0.5) + 1.0)
            for idx in range(self.num_docs):
                f = self.term_freqs[idx].get(token, 0)
                dl = self.doc_lengths[idx]
                denom = f + self.k1 * (1.0 - self.b + self.b * (dl / (self.avgdl or 1.0)))
                scores[idx] += idf * (f * (self.k1 + 1.0)) / (denom or 1.0)

        results = list(zip(self.doc_ids, scores))
        results.sort(key=lambda x: x[1], reverse=True)
        return results


class SkillRegistry:
    """Discovers, indexes, and dynamically retrieves skill documentation for the AI agent."""

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = Path(skills_dir) if skills_dir else DEFAULT_SKILLS_DIR
        self._skills: Dict[str, Skill] = {}
        self._bm25 = BM25Index()
        self.load_skills()

    def load_skills(self) -> None:
        """Loads and indexes all skills in the skills directory dynamically."""
        self._skills.clear()
        if not self.skills_dir.exists():
            logger.warning(f"Skills directory does not exist: {self.skills_dir}")
            return

        docs_to_index: Dict[str, str] = {}

        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            try:
                raw_text = skill_md.read_text(encoding="utf-8")
                name, description, triggers, body = self._parse_frontmatter(raw_text, skill_dir.name)

                # Also load any reference markdown files in references/ subdirectory
                refs = {}
                ref_dir = skill_dir / "references"
                if ref_dir.exists() and ref_dir.is_dir():
                    for ref_file in ref_dir.glob("*.md"):
                        try:
                            refs[ref_file.stem] = ref_file.read_text(encoding="utf-8")
                        except Exception as e:
                            logger.warning(f"Failed to load reference {ref_file}: {e}")

                skill = Skill(
                    name=name,
                    description=description,
                    content=body,
                    triggers=triggers,
                    references=refs
                )
                self._skills[name] = skill

                # Document text for BM25 indexing (name, description, triggers, references, body)
                doc_text = f"{name} {description} {' '.join(triggers)} {' '.join(refs.values())} {body}"
                docs_to_index[name] = doc_text
                logger.debug(f"Loaded skill: {name} ({len(triggers)} triggers, {len(refs)} references)")
            except Exception as e:
                logger.error(f"Error loading skill at {skill_dir}: {e}")

        # Build in-memory BM25 index
        self._bm25.build(docs_to_index)

    @staticmethod
    def _parse_frontmatter(text: str, default_name: str) -> Tuple[str, str, List[str], str]:
        """Extracts YAML frontmatter (name, description, triggers) and body."""
        name = default_name
        description = ""
        triggers: List[str] = []
        body = text

        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1]
                body = parts[2].strip()

                for line in frontmatter.splitlines():
                    line = line.strip()
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip()

                # Extract trigger words from description
                match = re.search(r"triggers on\s+([^\.]+)", description, re.IGNORECASE)
                if match:
                    raw_triggers = match.group(1)
                    extracted = re.findall(r"'([^']+)'|\"([^\"]+)\"|([a-zA-Z0-9_\-]{3,})", raw_triggers)
                    for item in extracted:
                        val = (item[0] or item[1] or item[2]).strip().lower()
                        if val and len(val) > 2:
                            triggers.append(val)

        return name, description, triggers, body

    def get_skill(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def list_skills(self) -> List[str]:
        return list(self._skills.keys())

    def match_skills(self, user_prompt: str) -> List[str]:
        """
        Matches relevant skills using keyword heuristics and BM25 scoring.
        Selectively isolates targeted skills:
        - Pure diagnostic requests -> 'video_diagnosis'
        - Creative filters / aspect ratios -> 'creative_fx'
        - General video conversion / audio operations -> 'ffmpeg'
        """
        if not self._skills:
            return []

        lower_prompt = user_prompt.lower()
        bm25_scores = dict(self._bm25.score(user_prompt))

        # Check domain-specific keywords and triggers
        diag_keywords = {
            "lag", "stutter", "freeze", "diagnos", "why so big", "too much size",
            "cannot play", "choppy", "battery drain", "overheating", "codec issue",
            "dropped frames", "drops frames", "corruption", "corrupt"
        }
        creative_keywords = {
            "reverse", "play backwards", "aspect ratio", "change ratio", "9:16", "16:9",
            "1:1", "4:5", "crop", "vertical video", "tiktok", "reels", "shorts", "pad",
            "slow motion", "speed up", "black and white", "b&w", "grayscale", "greyscale",
            "monochrome", "sepia", "vintage", "saturation", "brightness", "contrast",
            "mirror", "flip", "rotate", "blur", "sharp", "negative", "invert", "aloop", "loop"
        }
        ffmpeg_keywords = {
            "convert", "mp4", "mkv", "compress", "extract audio", "separate audio",
            "mute", "remove audio", "replace audio", "soundtrack", "audio only",
            "video only", "strip audio", "combine", "merge", "join", "stitch", "codec", "crf"
        }

        is_diag = any(k in lower_prompt for k in diag_keywords) or any(
            t in lower_prompt for t in self._skills.get("video_diagnosis", Skill("", "", "")).triggers
        )
        is_creative = any(k in lower_prompt for k in creative_keywords) or any(
            t in lower_prompt for t in self._skills.get("creative_fx", Skill("", "", "")).triggers
        )
        is_ffmpeg = any(k in lower_prompt for k in ffmpeg_keywords) or any(
            t in lower_prompt for t in self._skills.get("ffmpeg", Skill("", "", "")).triggers
        )

        matched: List[str] = []

        if is_diag and not is_creative and not is_ffmpeg:
            # Pure diagnostic query: retrieve only video_diagnosis
            matched.append("video_diagnosis")
        elif is_creative and not is_diag:
            # Pure creative query: retrieve creative_fx (and ffmpeg only if conversion requested)
            matched.append("creative_fx")
            if is_ffmpeg:
                matched.append("ffmpeg")
        elif is_ffmpeg and not is_diag and not is_creative:
            # General conversion query: retrieve only ffmpeg, suppressing specialized skills
            matched.append("ffmpeg")
        else:
            # Use BM25 ranked top matches
            top_ranked = sorted(bm25_scores.items(), key=lambda x: x[1], reverse=True)
            for skill_name, score in top_ranked:
                if score > 0.05 or skill_name in lower_prompt:
                    matched.append(skill_name)

        # Fallback to ffmpeg base skill if nothing matched
        if not matched:
            matched = ["ffmpeg"] if "ffmpeg" in self._skills else list(self._skills.keys())[:1]

        return matched

    def get_relevant_skills(self, user_prompt: str, max_chars: int = 5000) -> str:
        """
        Dynamically retrieves and compiles relevant skill sections based on intent.
        Suppresses unrelated skills to preserve prompt tokens.
        """
        matched_names = self.match_skills(user_prompt)
        snippets: List[str] = []

        for name in matched_names:
            skill = self._skills.get(name)
            if not skill:
                continue
            snippets.append(f"### Skill: {skill.name.upper()}\n{skill.content}")
            for ref_name, ref_content in skill.references.items():
                snippets.append(f"### Reference ({skill.name}/{ref_name}):\n{ref_content}")

        if not snippets:
            return "### Standard FFmpeg Guidance Available."

        compiled = "\n\n---\n\n".join(snippets)
        if len(compiled) > max_chars:
            compiled = compiled[:max_chars] + "\n\n[... truncated for prompt efficiency ...]"
        return compiled

    @staticmethod
    def check_memory_safety(
        duration_sec: float,
        width: Optional[int],
        height: Optional[int],
        is_reverse: bool = False,
        is_termux: bool = True
    ) -> Tuple[bool, str]:
        """
        Enforces mobile host memory safety rules (especially for Nokia 6.1 Plus Snapdragon 636).
        Returns (is_safe, warning_or_error_message).
        """
        if not is_reverse:
            return True, ""

        w = width or 1920
        h = height or 1080
        total_pixels = w * h

        if is_termux or total_pixels >= 1920 * 1080:
            if duration_sec > 30.0:
                return (
                    False,
                    f"Video duration is {int(duration_sec)}s. Inverting/reversing frames uncompresses all frames into system RAM, "
                    f"which requires ~{int(duration_sec * 0.15)} GB RAM and will crash your device. "
                    f"Please trim the video to under 30 seconds before reversing."
                )
            if total_pixels > 1280 * 720 and duration_sec > 15.0:
                return (
                    True,
                    f"High resolution ({w}x{h}) reverse requires automatic downscaling to 720p HD to prevent device Out-Of-Memory."
                )

        return True, ""


# Global singleton instance
skill_registry = SkillRegistry()
