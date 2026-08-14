"""Web search for term/proper-noun verification (Tavily), cached per term.

Before translation, extract capitalized terms/phrases from the source text and
look each NEW term up once (results are cached for the session, so repeated
terms never re-bill a query). The context is injected into the LLM translation
prompt so proper nouns and memes translate accurately.
"""
import os
import re
import threading

import requests

_CAP_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9.'\u2019\-]{1,}(?:\s+[A-Z][A-Za-z0-9.'\u2019\-]{1,}){0,2}\b"
)
_STOP = {
    "The", "A", "An", "And", "But", "Or", "Nor", "It", "This", "That", "These",
    "Those", "I", "We", "You", "He", "She", "They", "My", "Your", "Our", "His",
    "Her", "Its", "Today", "Tomorrow", "Yesterday", "Now", "One", "Two", "Three",
    "What", "When", "Where", "Who", "Why", "How", "Not", "So", "If", "As", "All",
    "Each", "Every", "Some", "Any", "There", "Here", "Please", "Thank", "Good",
    "Well", "Yes", "No", "Mr", "Mrs", "Ms", "Dr", "Let", "Next", "First", "Last",
    "Hi", "Hello", "Hey", "Actually", "Basically", "Right", "Okay", "Ok",
}


class TavilySearch:
    def __init__(self, cfg):
        s = (cfg.get("llm", {}).get("web_search", {}) or {})
        self.enabled = bool(s.get("enabled", False))
        self.api_key = s.get("api_key") or os.environ.get("TAVILY_API_KEY", "")
        self.max_results = int(s.get("max_results", 2))
        self.search_depth = s.get("search_depth", "basic")
        self.max_context_chars = int(s.get("max_context_chars", 300))
        self.timeout = int(s.get("timeout", 20))
        self._cache = {}
        self._lock = threading.Lock()

    def extract_terms(self, text):
        terms = set()
        for m in _CAP_RE.finditer(text):
            t = m.group(0)
            if t in _STOP or len(t) <= 2:
                continue
            terms.add(t)
        return terms

    def _search(self, query):
        resp = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": self.api_key, "query": query,
                  "max_results": self.max_results, "search_depth": self.search_depth},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        j = resp.json()
        ctx = []
        for r in (j.get("results") or [])[: self.max_results]:
            content = (r.get("content") or "").strip().replace("\n", " ")
            ctx.append(f"{r.get('title', '')}: {content[: self.max_context_chars]}")
        return " | ".join(ctx)

    def lookup(self, text):
        """Return a search-context string for the terms found in `text` (cached)."""
        if not self.enabled or not self.api_key:
            return ""
        terms = self.extract_terms(text)
        if not terms:
            return ""
        parts = []
        for t in sorted(terms):
            with self._lock:
                cached = self._cache.get(t)
            if cached is None:
                try:
                    cached = self._search(t)
                    if cached:
                        print(f"[search] {t} -> {cached[:80]}...", flush=True)
                except Exception as e:
                    print(f"[search] fail {t}: {e}", flush=True)
                    cached = ""
                with self._lock:
                    self._cache[t] = cached
            if cached:
                parts.append(f"{t} => {cached}")
        return "\n".join(parts)
