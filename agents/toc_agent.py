"""
toc_agent.py
────────────
Your backend structure preserved:
  - Class name: TOCGenerationAgent
  - All method names unchanged
  - Same import path: from services.llm_client import GroqLLM
"""

import json
import logging
import re
from typing import Any

from services.llm_client import GroqLLM

logger = logging.getLogger(__name__)

MAX_DURATION_DAYS   = 10
MAX_RAG_CHUNK_CHARS = 500
MAX_RETRIES         = 2
PROMPT_PATH         = "prompts/toc_prompt.txt"

_REQUIRED_FIELDS: dict[str, Any] = {
    "title":                 "",
    "duration":              "",
    "description":           "",
    "objectives":            [],
    "entry_profile":         [],
    "methodology":           [
        "Instructor-led training with live demonstrations",
        "Hands-on labs using real enterprise scenarios",
        "Case studies from production deployments",
        "Q&A and troubleshooting sessions",
    ],
    "hardware_requirements": [
        "Laptop with minimum 16GB RAM, 8-core CPU",
        "Stable broadband internet (minimum 10 Mbps)",
    ],
    "software_requirements": [],
    "course_contents":       [],
}


# ── Custom exceptions ─────────────────────────────────────────────────────────
class TOCGenerationError(RuntimeError):
    """Raised when TOC generation fails after all retries."""

class TOCValidationError(ValueError):
    """Raised when a generated TOC fails structural quality checks."""


# ── Quality validator ─────────────────────────────────────────────────────────
def validate_toc_quality(toc: dict) -> None:
    """
    Structural quality gate — called before handing a TOC to the SME.
    Raises TOCValidationError with a descriptive message on failure.
    """
    contents = toc.get("course_contents")
    if not contents:
        raise TOCValidationError(
            "TOC failed quality check: 'course_contents' is missing or empty."
        )
    if len(contents) < 3:
        raise TOCValidationError(
            f"TOC failed quality check: only {len(contents)} day(s) generated — "
            "minimum is 3. LLM likely truncated output."
        )
    title = toc.get("title", "").strip()
    if not title or len(title) < 5:
        raise TOCValidationError(
            f"TOC failed quality check: title {title!r} is blank or too short."
        )
    duration_str = toc.get("duration", "")
    try:
        expected_days = int(duration_str.split()[0])
    except (IndexError, ValueError, AttributeError):
        raise TOCValidationError(
            f"TOC failed quality check: cannot parse 'duration' field {duration_str!r}."
        )
    actual_days = len(contents)
    if actual_days != expected_days:
        raise TOCValidationError(
            f"TOC failed quality check: duration declares {expected_days} day(s) "
            f"but course_contents has {actual_days} day(s)."
        )
    


# ── Agent ─────────────────────────────────────────────────────────────────────
class TOCGenerationAgent:

    def __init__(self):
        self.llm = GroqLLM()
        self._prompt_template = None

    # ── Prompt loading (cached) ───────────────────────────────────────────────

    def _load_template(self) -> str:
        if self._prompt_template is None:
            with open(PROMPT_PATH, "r", encoding="utf-8") as f:
                self._prompt_template = f.read()
        return self._prompt_template

    # ── Domain rules (kept for prompt compat, content inferred by LLM) ───────

    def build_domain_rules(self, domains: list) -> str:
        """
        Kept for backward compatibility with any callers that pass domains.
        The prompt template no longer uses domain_rules — the LLM infers
        scope directly from the technology name.
        Returns empty string.
        """
        return ""

    # ── RAG context formatter ─────────────────────────────────────────────────

    def _format_rag_context(self, rag_context: list) -> str:
        if not rag_context:
            return (
                "Generate a complete, structured enterprise training TOC with day-wise modules."
            )

        blocks = []
        for i, doc in enumerate(rag_context, 1):
            if not doc:
                continue
            chunk = str(doc).strip()[:MAX_RAG_CHUNK_CHARS]
            blocks.append(
                f"--- REFERENCE {i} ---\n{chunk}\n--- END REFERENCE {i} ---"
            )

        return "\n\n".join(blocks) if blocks else (
            "[NO USABLE REFERENCE MATERIAL]\n"
            "Generate from expert knowledge."
        )

    # ── Main generation (single definition — BUG 1 fixed) ────────────────────

    def generate(self, structured_requirements: dict, retrieved_content: list, domains: list) -> dict:
        """
        Generate a fresh TOC.

        Raises:
            ValueError:         if technology is missing.
            TOCGenerationError: if LLM fails after all retries.
            TOCValidationError: if generated TOC fails quality checks.
        """
        if not isinstance(structured_requirements, dict):
            raise TypeError("structured_requirements must be a dict.")

        technology = structured_requirements.get("technology")
        if not technology:
            raise ValueError(
                "Missing 'technology' in structured_requirements. "
                "Ensure RequirementAnalysisAgent.analyze() ran first."
            )

        duration_days = min(int(structured_requirements.get("duration_days", 5)), MAX_DURATION_DAYS)
        raw_input     = structured_requirements.get("raw_input", "")
        level         = structured_requirements.get("level", "Intermediate")
        secondary     = ", ".join(structured_requirements.get("secondary_technologies", []))

        rag_text = self._format_rag_context(retrieved_content)
        retrieved_content = retrieved_content[:3]
        prompt = self._load_template().format(
            requirements=raw_input,
            technology=technology,
            duration_days=duration_days,
            rag_context=rag_text,
            level=level,
            secondary_technologies=secondary,
            domain_rules="",
        )

        logger.info(
            "Generating TOC | tech=%r | days=%d | level=%s | rag_chunks=%d",
            technology, duration_days, level, len(retrieved_content),
        )

        toc = self._generate_with_retry(prompt)
        toc = self._ensure_required_fields(toc, technology)
        validate_toc_quality(toc)
        return toc

    # ── Regeneration with feedback (BUG 5 fixed) ─────────────────────────────

    def regenerate_with_feedback(self, original_toc: dict, feedback: str, requirements: dict) -> dict:
        """
        Revise an existing TOC based on reviewer feedback.

        BUG 5 fix: original_toc AND feedback are now both injected into the
        prompt. The original version ignored both — it just generated a new
        TOC from scratch each time.
        """
        if not isinstance(original_toc, dict):
            raise TypeError("original_toc must be a dict.")
        if not isinstance(feedback, str) or not feedback.strip():
            raise ValueError("feedback must be a non-empty string.")

        technology    = requirements.get("technology")
        if not technology:
            raise ValueError("Missing 'technology' in requirements dict.")
        duration_days = requirements.get("duration_days", 5)

        prompt = f"""
You are an expert curriculum designer for Synergetics, a corporate training company.
Revise the Training Table of Contents below based on the reviewer feedback.

STRICT OUTPUT RULES:
- Return ONLY valid JSON — no markdown, no code fences, no prose.
- JSON must start with {{ and end with }}.
- All keys from the original must be present in the revised version.

CONTEXT:
  Technology : {technology}
  Duration   : {duration_days} days

REVIEWER FEEDBACK (apply ALL points):
{feedback.strip()}

ORIGINAL TOC TO REVISE:
{json.dumps(original_toc)[:4000]}

Return the revised TOC as JSON now.
""".strip()

        logger.info("Regenerating TOC with feedback | tech=%r", technology)
        toc = self._generate_with_retry(prompt)
        toc = self._ensure_required_fields(toc, technology)
        validate_toc_quality(toc)
        return toc

    # ── JSON parsing (BUG 2 fixed: proper instance method with self) ──────────

    def _safe_json_load(self, raw: str) -> dict:
        """
        3-stage JSON extraction.
        BUG 2 fix: was defined without `self`, called as free function.
        """
        # Stage 1 — direct parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Stage 2 — strip markdown fences
        stripped = re.sub(r"```(?:json)?", "", raw).strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # Stage 3 — greedy regex for outermost { … }
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        raise ValueError(f"No valid JSON in LLM output. First 200 chars:\n{raw[:200]}")

    # ── Retry loop (BUG 3 + BUG 4 fixed) ─────────────────────────────────────

    def _generate_with_retry(self, prompt: str) -> dict:
        """
        BUG 3 fix: self.llm.generate(prompt) not self.llm(prompt)
        BUG 4 fix: self._safe_json_load() not safe_json_load()
        """
        current_prompt = prompt

        for attempt in range(1, MAX_RETRIES + 1):
            logger.debug("LLM call attempt %d/%d", attempt, MAX_RETRIES)

            try:
                raw_output = self.llm.generate(current_prompt)   # BUG 3 fix
            except Exception as exc:
                logger.warning("LLM transport error attempt %d: %s", attempt, exc)
                if attempt == MAX_RETRIES:
                    raise TOCGenerationError(
                        f"LLM call failed after {MAX_RETRIES} attempts: {exc}"
                    ) from exc
                continue

            try:
                return self._safe_json_load(raw_output)           # BUG 4 fix
            except ValueError as exc:
                logger.warning("JSON parse failed attempt %d: %s", attempt, exc)
                if attempt == MAX_RETRIES:
                    raise TOCGenerationError(
                        f"LLM returned invalid JSON after {MAX_RETRIES} attempts.\n"
                        f"Last output (first 200 chars):\n{raw_output[:200]}"
                    ) from exc
                current_prompt += (
                    "\n\n⚠ CRITICAL: Your last response was NOT valid JSON. "
                    "Output ONLY a raw JSON object. "
                    "No markdown. No text. No code fences. Start with { end with }."
                )

        raise TOCGenerationError("Unexpected exit from retry loop.")

    # ── Required fields ───────────────────────────────────────────────────────

    def _ensure_required_fields(self, toc: dict, technology: str) -> dict:
        for field, default in _REQUIRED_FIELDS.items():
            if field not in toc or toc[field] in (None, "", [], {}):
                toc[field] = default.copy() if isinstance(default, list) else default

        if not toc.get("title") or toc["title"].strip() in ("", "Training"):
            toc["title"] = f"{technology} — Enterprise Training"

        return toc