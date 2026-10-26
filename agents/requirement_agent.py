"""
requirement_agent.py
────────────────────
Parses raw free-text client input into a structured requirements dict.

Your backend structure preserved:
  - RequirementAnalysisAgent class name
  - analyze() method signature
  - Same output dict shape

Fixes applied:
  - Removed dependency on core/technologies.json (crashes if file missing)
  - Removed extract_technology() / extract_technologies() duplication
    (two methods doing the same job, only one was called)
  - Removed hardcoded 10-keyword list (misses AZ-104, DP-600, Copilot Studio etc.)
  - Removed "Based on client input Technology" garbage fallback
  - Added cert code detection  (AZ-104, MS-700, DP-203, SC-900 …)
  - Added multi-word product detection (Power BI, Copilot Studio, Azure DevOps …)
  - Added hard stop: raises ValueError if technology cannot be identified
    (fail early > generate wrong TOC silently)
  - Level and audience extraction preserved and extended
"""

import logging
import re

logger = logging.getLogger(__name__)


# ── Level detection ───────────────────────────────────────────────────────────
_LEVEL_MAP: list[tuple[str, list[str]]] = [
    ("Beginner",     ["beginner", "basic", "fundamental", "intro", "introductory",
                      "newcomer", "no experience", "from scratch"]),
    ("Advanced",     ["advanced", "expert", "senior", "deep dive", "deep-dive",
                      "mastery", "specialist", "architect"]),
    ("Intermediate", ["intermediate", "mid", "moderate", "working knowledge",
                      "some experience"]),
]

_DEFAULT_LEVEL    = "Intermediate"
_DEFAULT_AUDIENCE = "Professional Developers"
_MAX_DURATION     = 20
_MIN_TECH_LENGTH  = 3

# ── Known multi-word product names ────────────────────────────────────────────
# Prevents "Power BI" being split into "Power" + "BI"
_MULTIWORD_PRODUCTS = [
    "copilot studio", "power bi", "power automate", "power apps",
    "power platform", "azure devops", "azure openai", "azure synapse",
    "azure data factory", "azure machine learning", "microsoft fabric",
    "microsoft sentinel", "microsoft intune", "microsoft teams",
    "github actions", "github copilot",
    "generative ai", "agentic ai", "machine learning", "deep learning",
    "natural language processing", "large language model",
    "databricks", "snowflake", "google cloud",
]

# ── Noise words to strip when isolating technology name ──────────────────────
_NOISE_RE = re.compile(
    r"\b(\d+\s*-?\s*(days?|weeks?|months?)"
    r"|beginner|intermediate|advanced|expert"
    r"|training|course|certification|cert|workshop|bootcamp|program|programme"
    r"|professional|developer|engineer|analyst|architect"
    r"|for\s+beginners|for\s+professionals)\b",
    re.IGNORECASE,
)

# ── Audience detection ────────────────────────────────────────────────────────
_AUDIENCE_MAP: list[tuple[str, list[str]]] = [
    ("Data Scientists",         ["data scientist", "ml engineer", "data analyst"]),
    ("DevOps Engineers",        ["devops", "sre", "platform engineer"]),
    ("Business Analysts",       ["business analyst", "product manager"]),
    ("Security Professionals",  ["security", "cybersecurity", "soc analyst"]),
    ("Professional Developers", ["developer", "engineer", "programmer", "software"]),
]


class RequirementAnalysisAgent:
    """
    Parses free-text client input into a structured requirements dict.

    Output schema (unchanged from your backend):
    {
        "raw_input":              str,
        "domain":                 "Technology Training",
        "target_audience":        str,
        "level":                  str,
        "duration_days":          int,
        "technology":             str,
        "secondary_technologies": list[str],
    }
    """

    def analyze(self, client_input: str) -> dict:
        """
        Parse a free-text training request.

        Raises:
            ValueError: if input is empty or technology cannot be identified.
        """
        if not isinstance(client_input, str) or not client_input.strip():
            raise ValueError("client_input must be a non-empty string.")

        text  = client_input.strip()
        lower = text.lower()

        duration  = self._extract_duration(lower) or 5
        level     = self._extract_level(lower)
        audience  = self._extract_audience(lower)
        primary, secondary = self._extract_technology(text, lower)

        result = {
            "raw_input":               text,
            "domain":                  "Technology Training",
            "target_audience":         audience,
            "level":                   level,
            "duration_days":           min(duration, _MAX_DURATION),
            "technology":              primary,
            "secondary_technologies":  secondary,
        }

        logger.info(
            "Parsed | tech=%r | secondary=%s | days=%d | level=%s",
            primary, secondary, result["duration_days"], level,
        )
        return result

    # ── Duration ──────────────────────────────────────────────────────────────

    def _extract_duration(self, lower: str) -> int | None:
        m = re.search(r"\b(\d+)\s*-?\s*days?\b", lower)
        if m:
            return int(m.group(1))
        m = re.search(r"\b(\d+)\s*-?\s*weeks?\b", lower)
        if m:
            return int(m.group(1)) * 5
        m = re.search(r"\b(\d+)\s*-?\s*months?\b", lower)
        if m:
            return int(m.group(1)) * 20
        return None

    # ── Level ─────────────────────────────────────────────────────────────────

    def _extract_level(self, lower: str) -> str:
        for level, keywords in _LEVEL_MAP:
            if any(kw in lower for kw in keywords):
                return level
        return _DEFAULT_LEVEL

    # ── Audience ──────────────────────────────────────────────────────────────

    def _extract_audience(self, lower: str) -> str:
        for audience, keywords in _AUDIENCE_MAP:
            if any(kw in lower for kw in keywords):
                return audience
        return _DEFAULT_AUDIENCE

    # ── Technology ────────────────────────────────────────────────────────────

    def _extract_technology(self, original: str, lower: str) -> tuple[str, list[str]]:
        """
        Priority:
          1. Cert codes   (AZ-104, MS-700, DP-203 …)
          2. Multi-word products  (Power BI, Copilot Studio …)
          3. Noise-stripped remainder

        Raises ValueError if nothing meaningful is found.
        """

        # 1. Certification codes
        certs = re.findall(r"\b([A-Z]{1,4}-\d{3,4})\b", original)
        if certs:
            full_match = re.search(
                r"\b([A-Z]{1,4}-\d{3,4}(?:\s*:\s*[A-Za-z][A-Za-z ]{2,40})?)\b",
                original,
            )
            primary   = full_match.group(1).strip() if full_match else certs[0]
            secondary = certs[1:]
            return primary, secondary

        # 2. Multi-word products
        for phrase in _MULTIWORD_PRODUCTS:
            if phrase in lower:
                remaining = lower.replace(phrase, "")
                secondary = self._tokens_from(remaining)
                return phrase.title(), secondary

        # 3. Strip noise
        cleaned = _NOISE_RE.sub("", original)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

        if not cleaned or len(cleaned.strip()) < _MIN_TECH_LENGTH:
            raise ValueError(
                f"Technology not identified from input: {original!r}. "
                "Please include a specific technology, product, or certification code."
            )

        tokens = [
            t.strip()
            for t in re.split(r"[,/&+]|\s+and\s+|\s+with\s+|\s+using\s+", cleaned)
            if t.strip() and len(t.strip()) > 1
        ]

        primary   = tokens[0] if tokens else cleaned
        secondary = tokens[1:] if len(tokens) > 1 else []
        return primary, secondary

    def _tokens_from(self, text: str) -> list[str]:
        cleaned = _NOISE_RE.sub("", text)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return [
            t.strip()
            for t in re.split(r"[,/&+]|\s+and\s+|\s+with\s+|\s+using\s+", cleaned)
            if t.strip() and len(t.strip()) > 1
        ]