"""DeepSeek LLM translator (OpenAI-compatible chat completions API).

Supports two modes:
  * 1:1 batch  (merge=False) : each input line maps to one output line
                               (used by batch_dub.py for subtitle timing)
  * passage    (merge=True ) : fragments are merged into complete sentences
                               with conversation context (used by streaming glue)

Both modes can inject web-search references and previous-sentence context so
proper nouns, terms and pronoun references stay consistent.
"""
import os
import re

import requests

_NUM_PREFIX_RE = re.compile(r"^\s*\d+\.\s*")


class DeepSeekTranslator:
    def __init__(self, cfg):
        llm = cfg["llm"]
        self.api_key = llm.get("api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "DeepSeek API key missing: set DEEPSEEK_API_KEY env var or llm.api_key in config.yaml"
            )
        self.base_url = llm.get("base_url", "https://api.deepseek.com")
        self.model = llm.get("model", "deepseek-chat")
        self.reasoning_model = llm.get("reasoning_model", "") or os.environ.get("DEEPSEEK_REASONING_MODEL", "")
        self.timeout = llm.get("timeout", 60)
        self.glossary = llm.get("glossary", [])
        try:
            from web_search import TavilySearch
            self.search = TavilySearch(cfg)
        except Exception as e:
            print(f"[llm] web_search disabled: {e}", flush=True)
            self.search = None

    # -- prompt -----------------------------------------------------------
    def _build_system(self, merge):
        base = (
            "You are a professional simultaneous interpreter for real-time video "
            "dubbing. Translate the user's speech into fluent, natural Simplified Chinese."
        )
        rules = [
            "Take your time and think carefully about context, proper nouns, technical terms, "
            "memes and how the fragments connect before you translate.",
            "Keep proper nouns, terms and names accurate and consistent with the earlier context.",
        ]
        if self.glossary:
            terms = "\n".join(f"{g['from']} -> {g['to']}" for g in self.glossary)
            rules.append(f"Glossary (must use these translations):\n{terms}")
        if merge:
            rules.append(
                "If several consecutive fragments together form one complete sentence, "
                "merge them into that single complete sentence."
            )
            rules.append(
                "Output the translation as lines - one complete sentence per line, "
                "no numbering, no explanations."
            )
        else:
            rules.append(
                "The user sends several numbered lines. Translate each line separately, "
                "keep the numbering, and output ONLY the numbered translations."
            )
        return base + "\n\n" + "\n".join(f"- {r}" for r in rules)

    def _call(self, messages, model):
        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages,
                  "temperature": 0.3, "max_tokens": 2048},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    # -- main entry -------------------------------------------------------
    def translate_batch(self, sentences, context=None, merge=False):
        """Translate `sentences` -> list of Chinese sentences.

        context: list of (source, zh) previously translated pairs used as
                 conversation context for coherence.
        merge:   merge fragments into complete sentences (passage mode).
        """
        if not sentences:
            return []

        if merge:
            user = "\n".join(sentences)
            system = self._build_system(True)
            model = self.reasoning_model or self.model
        else:
            numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
            user = numbered
            system = self._build_system(False)
            model = self.model

        # conversation context (previous source -> zh)
        if context:
            ctx_lines = [f"{src} -> {zh}" for src, zh in context[-8:]]
            user = "[Earlier conversation]\n" + "\n".join(ctx_lines) + "\n\n" + user

        # optional web search for proper nouns / terms
        if self.search is not None:
            ref = self.search.lookup(user)
            if ref:
                user = user + "\n\n[Web search reference]\n" + ref

        out = self._call(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model,
        )

        lines = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            line = _NUM_PREFIX_RE.sub("", line)
            if line:
                lines.append(line)

        if merge:
            # one complete sentence per line; fall back to whole passage
            return lines if lines else [out]
        # 1:1 mapping; fall back to single whole-batch translation
        return lines if len(lines) == len(sentences) else [out]
