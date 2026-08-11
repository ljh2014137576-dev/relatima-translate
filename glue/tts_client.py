"""TTS client facade.

Selects the backend from config `tts.provider` (minimax | elevenlabs | edge | local)
and exposes a uniform synthesize(text) -> wav bytes interface.

Set the provider in glue/config.yaml, e.g.:
    tts:
      provider: minimax
      minimax:
        api_key: ""        # or env MINIMAX_API_KEY
        group_id: ""       # or env MINIMAX_GROUP_ID
        model: speech-02-turbo
"""
import os

from tts_providers import build_tts


class TTSClient:
    def __init__(self, cfg):
        self.config_dir = os.path.dirname(os.path.abspath(__file__))
        self._engine = build_tts(cfg, config_dir=self.config_dir)

    def synthesize(self, text, emo_alpha=None):
        return self._engine.synthesize(text)

    @property
    def engine(self):
        return self._engine
