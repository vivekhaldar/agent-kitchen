# ABOUTME: Disk-based JSON cache for LLM-generated session summaries.
# ABOUTME: Uses file mtime for invalidation and atomic writes for safety.

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class SummaryCache:
    """Cache for LLM-generated session summaries, keyed by session ID."""

    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.entries: dict[str, dict] = {}
        self._load()
        logger.info("Cache loaded: %d entries from %s", len(self.entries), self.cache_path)

    def _load(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            with open(self.cache_path) as f:
                data = json.load(f)
            self.entries = data.get("entries", {})
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning("Failed to load cache from %s: %s", self.cache_path, e)
            self.entries = {}

    def save(self) -> None:
        """Write cache to disk atomically (write to temp file, then rename)."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "entries": self.entries}
        fd, tmp_path = tempfile.mkstemp(dir=self.cache_path.parent, suffix=".tmp")
        try:
            with open(fd, "w") as f:
                json.dump(data, f, indent=2)
            Path(tmp_path).replace(self.cache_path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def get(self, session_id: str) -> dict | None:
        entry = self.entries.get(session_id)
        return entry if entry else None

    def set(self, session_id: str, summary: str, status: str, file_mtime: float) -> None:
        self.entries[session_id] = {
            "summary": summary,
            "status": status,
            "file_mtime": file_mtime,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def needs_refresh(self, session_id: str, file_mtime: float) -> bool:
        entry = self.entries.get(session_id)
        if entry is None:
            return True
        if file_mtime > entry["file_mtime"]:
            return True
        return False
