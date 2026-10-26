from services.llm_client import GroqLLM


class QueryOptimizer:

    def __init__(self):
        self.llm = GroqLLM()

    def optimize(self, structured: dict) -> str:

        raw = structured.get("raw_input", "")
        tech = structured.get("technology", "")
        level = structured.get("level", "")
        duration = structured.get("duration_days", "")

        prompt = f"""
Convert the following training requirement into a SHORT semantic search query.

Input:
{raw}

Rules:
- Keep it under 40 words
- Focus on technology + domain keywords
- Include tools if relevant (e.g., pandas, numpy, ci/cd, docker)
- Remove sentences, keep only keywords
- Output ONLY one line

Example:
Input: Data science with Python intermediate level
Output: python data science training pandas numpy machine learning intermediate 5 days
"""

        return self.llm.generate(prompt).strip()