# ABOUTME: Disk-based JSON cache for LLM-generated session summaries and timelines.
# ABOUTME: Uses file mtime for invalidation, atomic writes, entry validation, and size eviction.

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_CACHE_ENTRIES = 10000

_REQUIRED_FIELDS = {"summary", "status", "file_mtime", "generated_at"}


def _is_valid_entry(entry: dict) -> bool:
    """Check that a cache entry has all required fields."""
    return isinstance(entry, dict) and _REQUIRED_FIELDS <= entry.keys()


class SummaryCache:
    """Cache for LLM-generated session summaries and timelines, keyed by session ID."""

    def __init__(self, cache_path: Path, max_entries: int = MAX_CACHE_ENTRIES):
        self.cache_path = cache_path
        self.max_entries = max_entries
        self.entries: dict[str, dict] = {}
        self._load()
        logger.info("Cache loaded: %d entries from %s", len(self.entries), self.cache_path)

    def _load(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            with open(self.cache_path) as f:
                data = json.load(f)
            raw_entries = data.get("entries", {})
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning("Failed to load cache from %s: %s", self.cache_path, e)
            self.entries = {}
            return

        # Validate each entry, dropping malformed ones
        valid = {}
        dropped = 0
        for key, entry in raw_entries.items():
            if _is_valid_entry(entry):
                valid[key] = entry
            else:
                dropped += 1
        if dropped:
            logger.warning(
                "Dropped %d malformed cache entries from %s",
                dropped,
                self.cache_path,
            )
        self.entries = valid

    def _evict_oldest(self) -> None:
        """Remove oldest entries by generated_at until within max_entries."""
        if len(self.entries) <= self.max_entries:
            return
        to_remove = len(self.entries) - self.max_entries
        sorted_keys = sorted(
            self.entries.keys(),
            key=lambda k: self.entries[k].get("generated_at", ""),
        )
        for key in sorted_keys[:to_remove]:
            del self.entries[key]
        logger.info("Evicted %d oldest cache entries", to_remove)

    def save(self) -> None:
        """Write cache to disk atomically, merging with on-disk state first.

        Re-reads the cache file before writing to avoid losing entries added by
        other processes (e.g. concurrent indexer runs or the server's /api/refresh).
        In-memory entries take precedence over on-disk entries for the same key.
        Evicts oldest entries if the cache exceeds max_entries.
        """
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Merge: load current disk state, then overlay our in-memory entries
        disk_entries: dict[str, dict] = {}
        if self.cache_path.exists():
            try:
                with open(self.cache_path) as f:
                    disk_data = json.load(f)
                disk_entries = disk_data.get("entries", {})
            except (json.JSONDecodeError, KeyError, OSError):
                pass  # If unreadable, proceed with just our entries

        merged = {**disk_entries, **self.entries}
        self.entries = merged
        self._evict_oldest()

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
            "type": "summary",
            "summary": summary,
            "status": status,
            "file_mtime": file_mtime,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def set_timeline(self, session_id: str, phases: list[dict], file_mtime: float) -> None:
        """Store a timeline (list of phase dicts) as a cache entry."""
        self.entries[session_id] = {
            "type": "timeline",
            "summary": json.dumps(phases),
            "status": "timeline",
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
