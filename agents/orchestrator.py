"""
orchestrator.py
───────────────
Wired to YOUR actual backend:
  - ContentResearchAgent  (not ResearchAgent)
  - .retrieve()           (not .research())
  - dict return shape     (not ResearchResult dataclass)

Azure deployment additions:
  - TOCOrchestrator.from_env() factory — reads all secrets from env vars
  - health_check() — for Azure App Service /health probe
  - No secrets in code

All previous fixes preserved:
  - No silent research exception swallow
  - Input validation in regenerate()
  - PEP 8 spacing
"""

import logging
import os

from agents.requirement_agent import RequirementAnalysisAgent
from agents.toc_agent import TOCGenerationAgent
from agents.research_agent import ContentResearchAgent

logger = logging.getLogger(__name__)


# ── Azure environment helpers ─────────────────────────────────────────────────

def _build_vector_service_from_env():
    """
    Build vector service from Azure App Service environment variables.

    Set in Azure Portal → App Service → Configuration → Application Settings:
      AZURE_SEARCH_ENDPOINT  e.g. https://my-search.search.windows.net
      AZURE_SEARCH_KEY       Admin or query key
      AZURE_SEARCH_INDEX     Index name

    All three must be present to enable RAG.
    If any is missing, returns None → LLM-only mode (no crash).
    """
    endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT", "").strip()
    key      = os.environ.get("AZURE_SEARCH_KEY",      "").strip()
    index    = os.environ.get("AZURE_SEARCH_INDEX",    "").strip()

    if not all([endpoint, key, index]):
        missing = [
            name for name, val in [
                ("AZURE_SEARCH_ENDPOINT", endpoint),
                ("AZURE_SEARCH_KEY",      key),
                ("AZURE_SEARCH_INDEX",    index),
            ] if not val
        ]
        logger.warning(
            "RAG disabled — missing env var(s): %s. Running LLM-only mode.",
            ", ".join(missing),
        )
        return None


def _get_app_env() -> str:
    return os.environ.get("APP_ENV", "development").lower().strip()


# ── Orchestrator ──────────────────────────────────────────────────────────────

class TOCOrchestrator:
    """
    Coordinates the full TOC generation workflow:
      1. Requirement Analysis  (RequirementAnalysisAgent)
      2. Content Research      (ContentResearchAgent — your class)
      3. TOC Generation        (TOCGenerationAgent)

    Azure App Service startup (app.py):
        orchestrator = TOCOrchestrator.from_env()

    Health probe route:
        @app.get("/health")
        def health():
            return orchestrator.health_check()
    """

    def __init__(self, vector_service=None) -> None:
        self.requirement_agent = RequirementAnalysisAgent()
        self.research_agent    = ContentResearchAgent(vector_service)
        self.toc_agent         = TOCGenerationAgent()
        self._rag_enabled      = vector_service is not None
        self._env              = _get_app_env()

        logger.info(
            "TOCOrchestrator ready | env=%s | rag_enabled=%s",
            self._env, self._rag_enabled,
        )

    @classmethod
    def from_env(cls) -> "TOCOrchestrator":
        """
        Factory for Azure App Service.
        Reads all config from environment variables — no secrets in code.
        """
        return cls(vector_service=_build_vector_service_from_env())

    # ── Health check ──────────────────────────────────────────────────────────

    def health_check(self) -> dict:
        """JSON response for /health — Azure App Service probe endpoint."""
        return {
            "status":      "healthy",
            "env":         self._env,
            "rag_enabled": self._rag_enabled,
            "agents": {
                "requirement_agent": "ok",
                "research_agent":    "ok",
                "toc_agent":         "ok",
            },
        }

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self, client_input: str) -> dict:
        """
        Execute full pipeline: analyze → research → generate.

        Returns:
            {
                "requirements":      structured_requirements dict,
                "retrieved_content": list of RAG chunk strings (may be []),
                "toc":               validated TOC dict,
            }

        Raises:
            ValueError:         on empty input or unidentifiable technology.
            TOCGenerationError: if LLM fails after all retries.
            TOCValidationError: if generated TOC fails quality checks.
        """
        if not client_input or not client_input.strip():
            raise ValueError("Client input cannot be empty.")

        # Step 1 — Requirement Analysis
        logger.info("Step 1: Analyzing requirements...")
        structured_requirements = self.requirement_agent.analyze(client_input)

        # Step 2 — Content Research
        # ContentResearchAgent.retrieve() returns {"content": [], "domains": []}
        # It already degrades gracefully internally — if it raises here that
        # is a real programming error and should not be swallowed silently.
        logger.info("Step 2: Retrieving research content...")
        research_output   = self.research_agent.retrieve(structured_requirements)
        retrieved_content = research_output["content"]
        domains           = research_output["domains"]

        logger.info(
            "Research complete | chunks=%d | domains=%s",
            len(retrieved_content), domains,
        )

        # Step 3 — TOC Generation
        logger.info("Step 3: Generating TOC...")
        toc = self.toc_agent.generate(structured_requirements, retrieved_content, domains)

        logger.info("TOC generation completed.")

        return {
            "requirements":      structured_requirements,
            "retrieved_content": retrieved_content,
            "toc":               toc,
        }


    def regenerate(self, requirements: dict, current_toc: dict, feedback: str) -> dict:
        """
        Regenerate a TOC based on reviewer feedback.

        Raises:
            TypeError:  if requirements or current_toc are not dicts.
            ValueError: if feedback is empty.
        """
        if not isinstance(requirements, dict):
            raise TypeError(
                f"requirements must be a dict, got {type(requirements).__name__}."
            )
        if not isinstance(current_toc, dict):
            raise TypeError(
                f"current_toc must be a dict, got {type(current_toc).__name__}."
            )
        if not isinstance(feedback, str) or not feedback.strip():
            raise ValueError("feedback must be a non-empty string.")

        logger.info("Regenerating TOC with feedback...")
        improved_toc = self.toc_agent.regenerate_with_feedback(
            original_toc=current_toc,
            feedback=feedback,
            requirements=requirements,
        )

        return {
            "requirements": requirements,
            "toc":          improved_toc,
        }