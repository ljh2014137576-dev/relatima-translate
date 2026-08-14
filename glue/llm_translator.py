"""DeepSeek LLM translator (OpenAI-compatible chat completions API).

Replaces WhisperLiveKit's built-in NLLB translation with a higher-quality
DeepSeek translation. Input: source sentences; output: Chinese sentences.
Batching joins sentences with numbered lines so the response can be mapped
back reliably.
"""
import os
import re

import requests

_NUM_PREFIX_RE = re.compile(r"^\s*\d+\.\s*")


class DeepSeekTranslator:
    def __init__(self, cfg):
        llm = cfg["llm"]
        # Config key takes precedence; fall back to the DEEPSEEK_API_KEY env var
        # so the API key is never committed to the repository.
        self.api_key = llm.get("api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "DeepSeek API key missing: set DEEPSEEK_API_KEY env var or llm.api_key in config.yaml"
            )
        self.base_url = llm.get("base_url", "https://api.deepseek.com")
        self.model = llm.get("model", "deepseek-chat")
        self.timeout = llm.get("timeout", 30)
        self.glossary = llm.get("glossary", [])
        self.system_prompt = self._build_prompt()
        # optional web search for term verification
        try:
            from web_search import TavilySearch
            self.search = TavilySearch(cfg)
        except Exception as e:
            print(f"[llm] web_search disabled: {e}", flush=True)
            self.search = None

    def _build_prompt(self):
        prompt = (
            "You are a professional simultaneous interpreter. Translate the user's "
            "text into fluent, natural Simplified Chinese. Keep proper nouns, "
            "technical terms, numbers and brand names accurate and consistent. "
            "The user may send several numbered lines; translate each line "
            "separately, keep the numbering, and output ONLY the numbered "
            "translations with no explanations."
        )
        if self.glossary:
            terms = "\n".join(f"{g['from']} -> {g['to']}" for g in self.glossary)
            prompt += f"\n\nGlossary (use these translations):\n{terms}"
        return prompt

    def _call(self, user_text):
        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": user_text},
                ],
                "temperature": 0.3,
                "max_tokens": 2048,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    def translate_batch(self, sentences):
        """Translate a list of sentences -> list of Chinese sentences.

        Falls back to a single whole-batch translation if the line mapping
        does not match.
        """
        if not sentences:
            return []
        numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))

        # Optional web search: verify proper nouns/terms before translating.
        if self.search is not None:
            ctx = self.search.lookup(numbered)
            if ctx:
                numbered = numbered + "\n\n[Web search reference]\n" + ctx

        out = self._call(numbered)
        lines = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            line = _NUM_PREFIX_RE.sub("", line)
            if line:
                lines.append(line)
        if len(lines) == len(sentences):
            return lines
        # Mapping mismatch: return the whole response as a single translation.
        return [out]
