class RAGPipeline:

    def __init__(self, vector_service):
        self.vector_service = vector_service

    def retrieve(self, query: str):

        results = self.vector_service.search(query)

        if not results:
            return []

        filtered = []
        seen = set()

        for r in results:

            text = r.get("text", "").strip()
            distance = r.get("distance", 1)

            # ✅ keep reasonably relevant results
            if distance > 1.5:
                continue

            # ✅ remove very small junk
            if len(text) < 120:
                continue

            # ✅ deduplicate
            if text not in seen:
                filtered.append(text)
                seen.add(text)

        # ✅ fallback if filtering removes everything
        if not filtered:
            print("⚠ No strong matches, using top raw results")
            return [r.get("text", "").strip() for r in results[:3] if r.get("text")]

        # ✅ limit for token safety
        return filtered[:3]