"""Future local backend seam. No model, audio download, or backend is shipped in V1."""

from typing import Protocol

from ..models import Transcript, Video


class LocalTranscriber(Protocol):
    def transcribe(self, video: Video) -> Transcript:
        """Return timestamped segments; called only after the YouTube retry budget is exhausted."""
        ...
