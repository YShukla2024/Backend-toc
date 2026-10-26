"""
research_agent.py  (your file: memory/content_research_agent.py or similar)
────────────────────────────────────────────────────────────────────────────
Your backend structure preserved:
  - Class name  : ContentResearchAgent
  - Method name : .retrieve(structured_requirements)
  - Return shape: {"content": [], "domains": []}
  - RAGPipeline import path: from memory.rag_pipeline import RAGPipeline

Fixes applied:
  - Removed infer_technology(): technology is already set by RequirementAnalysisAgent.
    Re-detecting it here and overwriting the structured dict is wrong — it bypasses
    cert detection and multi-word product logic from the upstream agent.
  - Removed hardcoded keyword domain detection from RAG text
    ("pipeline" -> devops, "model" -> machine_learning etc.)
    These broad string matches produce wrong domain tags and are not needed —
    the TOC prompt already infers domain from the technology name directly.
  - Added minimum chunk length filter (< 150 chars = junk, discard)
  - Removed fail-fast on empty RAG: empty results are valid (LLM-only mode).
    Raising ValueError here forces callers to catch it and set content=[],
    which is exactly what an empty return already does cleanly.
  - Soft keyword filter kept but fixed: uses technology from structured_requirements
    (already correct), not re-inferred from raw_input.
  - Query builder kept, print replaced with logger.
"""

import logging
from memory.rag_pipeline import RAGPipeline

logger = logging.getLogger(__name__)

MIN_CHUNK_LENGTH = 150   # discard fragments shorter than this
MAX_FINAL_DOCS   = 3     # max chunks passed to TOC prompt

# ── Domain signal map ─────────────────────────────────────────────────────────
# Used ONLY to produce domain tags that hint the prompt.
# These are signals, not content restrictions — the LLM infers scope from
# the technology name independently.
_DOMAIN_SIGNALS: dict[str, list[str]] = {
    "devops":               ["devops", "ci/cd", "pipeline", "kubernetes", "docker",
                             "terraform", "ansible", "jenkins"],
    "machine_learning":     ["machine learning", "deep learning", "generative ai",
                             "llm", "mlops", "pytorch", "tensorflow", "model training"],
    "business_intelligence":["power bi", "tableau", "dax", "data warehouse",
                             "reporting", "dashboard"],
    "testing":              ["testing", "selenium", "pytest", "junit", "qa"],
    "java":                 ["java", "spring", "maven", "gradle"],
    ".net":                 [".net", "c#", "asp.net", "blazor"],
    "aws":                  ["aws", "amazon web services", "s3", "ec2", "lambda"],
    "azure":                ["azure", "microsoft cloud"],
    "security":             ["security", "sentinel", "defender", "zero trust",
                             "compliance", "identity"],
    "data_engineering":     ["databricks", "synapse", "data factory", "spark",
                             "delta lake", "snowflake"],
}


class ContentResearchAgent:
    """
    Retrieves RAG chunks and produces domain tags for the TOC prompt.

    Args:
        vector_service: Any object that RAGPipeline accepts.
    """

    def __init__(self, vector_service) -> None:
        self.rag = RAGPipeline(vector_service)

    # ── Public API ────────────────────────────────────────────────────────────

    def retrieve(self, structured_requirements: dict) -> dict:
        """
        Retrieve relevant chunks + detect domain tags.

        Args:
            structured_requirements: Output of RequirementAnalysisAgent.analyze()

        Returns:
            {"content": list[str], "domains": list[str]}
            content may be [] — callers must treat this as LLM-only mode.

        Never raises — degrades to empty content on retrieval failure.
        """
        if not isinstance(structured_requirements, dict):
            raise TypeError("structured_requirements must be a dict.")

        technology = structured_requirements.get("technology", "")

        # Technology must already be set by RequirementAnalysisAgent.
        # Do NOT re-infer it here — that bypasses cert detection and
        # multi-word product logic already handled upstream.
        if not technology:
            logger.warning(
                "ContentResearchAgent.retrieve(): technology is empty. "
                "Returning empty content — TOC will use LLM-only mode."
            )
            return {"content": [], "domains": []}

        query = self._build_query(structured_requirements)
        logger.info("RAG query: %r", query)

        # Fetch from vector DB — never raises
        rag_results = self._safe_retrieve(query)
        logger.info("RAG raw results: %d chunk(s)", len(rag_results))

        if not rag_results:
            logger.info("No RAG results — LLM-only mode.")
            return {"content": [], "domains": []}

        # Filter: keep chunks that mention the technology AND meet min length
        tech_lower = technology.lower()
        filtered   = [
            doc for doc in rag_results
            if len(doc) >= MIN_CHUNK_LENGTH 
        ]

        if filtered:
            logger.info("Using %d filtered chunk(s)", len(filtered))
            final_docs = filtered[:MAX_FINAL_DOCS]
        else:
            # Fallback: min-length filter only (no keyword match required)
            length_filtered = [doc for doc in rag_results if len(doc) >= MIN_CHUNK_LENGTH]
            if length_filtered:
                logger.info(
                    "No keyword match — using %d length-filtered chunk(s)",
                    len(length_filtered),
                )
                final_docs = length_filtered[:MAX_FINAL_DOCS]
            else:
                # All chunks are too short — return empty, use LLM-only mode
                logger.info(
                    "All %d chunk(s) below MIN_CHUNK_LENGTH (%d) — LLM-only mode.",
                    len(rag_results), MIN_CHUNK_LENGTH,
                )
                return {"content": [], "domains": []}

        domains = self._detect_domains(structured_requirements, final_docs)

        return {
            "content": final_docs,
            "domains": domains,
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _build_query(self, req: dict) -> str:
        parts = []

        tech = req.get("technology", "")
        if tech:
            parts.append(tech)

        for sec in req.get("secondary_technologies", []):
            parts.append(sec)

        level = req.get("level", "")
        if level:
            parts.append(f"{level} level")

        days = req.get("duration_days")
        if days:
            parts.append(f"{days} days")

        raw = req.get("raw_input", "")
        if raw:
            parts.append(raw[:150])

        return " ".join(parts).strip()

    def _safe_retrieve(self, query: str) -> list[str]:
        """Call RAGPipeline — returns [] on any failure."""
        try:
            results = self.rag.retrieve(query)
            return [str(r).strip() for r in (results or []) if r]
        except Exception as exc:
            logger.error("RAG retrieval error (continuing without RAG): %s", exc)
            return []

    def _detect_domains(self, req: dict, chunks: list[str]) -> list[str]:
        """
        Detect domain tags from requirements + retrieved chunks.
        Tags are hints for the prompt — not restrictions on content.
        """
        haystack = " ".join(filter(None, [
            req.get("technology", ""),
            " ".join(req.get("secondary_technologies", [])),
            req.get("raw_input", ""),
            " ".join(chunks),
        ])).lower()

        matched: list[str] = []
        for domain, signals in _DOMAIN_SIGNALS.items():
            if any(signal in haystack for signal in signals):
                matched.append(domain)

        return list(set(matched))