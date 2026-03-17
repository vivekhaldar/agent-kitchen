# ABOUTME: Tests for the summary cache layer.
# ABOUTME: Validates load, save, get, set, and invalidation logic.

import json

from agent_kitchen.cache import SummaryCache


def test_load_empty_cache(tmp_path):
    cache = SummaryCache(tmp_path / "summaries.json")
    assert cache.entries == {}


def test_load_nonexistent_file(tmp_path):
    cache = SummaryCache(tmp_path / "nonexistent" / "summaries.json")
    assert cache.entries == {}


def test_save_and_load_roundtrip(tmp_path):
    cache_path = tmp_path / "summaries.json"
    cache = SummaryCache(cache_path)
    cache.set("session-1", "Fix bug in parser", "done", 1710288000.0)
    cache.save()

    cache2 = SummaryCache(cache_path)
    entry = cache2.get("session-1")
    assert entry is not None
    assert entry["summary"] == "Fix bug in parser"
    assert entry["status"] == "done"
    assert entry["file_mtime"] == 1710288000.0


def test_get_missing_session(tmp_path):
    cache = SummaryCache(tmp_path / "summaries.json")
    assert cache.get("nonexistent") is None


def test_set_and_get(tmp_path):
    cache = SummaryCache(tmp_path / "summaries.json")
    cache.set("s1", "Implement retry logic", "in progress", 1234567890.0)
    entry = cache.get("s1")
    assert entry["summary"] == "Implement retry logic"
    assert entry["status"] == "in progress"
    assert entry["file_mtime"] == 1234567890.0
    assert "generated_at" in entry


def test_set_overwrites_existing(tmp_path):
    cache = SummaryCache(tmp_path / "summaries.json")
    cache.set("s1", "Old summary", "in progress", 100.0)
    cache.set("s1", "New summary", "done", 200.0)
    entry = cache.get("s1")
    assert entry["summary"] == "New summary"
    assert entry["status"] == "done"
    assert entry["file_mtime"] == 200.0


def test_needs_refresh_not_cached(tmp_path):
    cache = SummaryCache(tmp_path / "summaries.json")
    assert cache.needs_refresh("unknown-id", 1234567890.0) is True


def test_needs_refresh_stale(tmp_path):
    cache = SummaryCache(tmp_path / "summaries.json")
    cache.set("s1", "Summary", "done", 100.0)
    # File was modified after cache entry
    assert cache.needs_refresh("s1", 200.0) is True


def test_needs_refresh_fresh(tmp_path):
    cache = SummaryCache(tmp_path / "summaries.json")
    cache.set("s1", "Summary", "done", 100.0)
    # File mtime same as cached
    assert cache.needs_refresh("s1", 100.0) is False


def test_needs_refresh_older_mtime(tmp_path):
    cache = SummaryCache(tmp_path / "summaries.json")
    cache.set("s1", "Summary", "done", 200.0)
    # File mtime older than cached (shouldn't happen but handle gracefully)
    assert cache.needs_refresh("s1", 50.0) is False


def test_save_creates_parent_directories(tmp_path):
    cache_path = tmp_path / "deep" / "nested" / "summaries.json"
    cache = SummaryCache(cache_path)
    cache.set("s1", "Test", "done", 100.0)
    cache.save()
    assert cache_path.exists()


def test_save_is_atomic(tmp_path):
    cache_path = tmp_path / "summaries.json"
    cache = SummaryCache(cache_path)
    cache.set("s1", "Test", "done", 100.0)
    cache.save()

    # Verify no .tmp file left behind
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_cache_file_has_version(tmp_path):
    cache_path = tmp_path / "summaries.json"
    cache = SummaryCache(cache_path)
    cache.set("s1", "Test", "done", 100.0)
    cache.save()

    with open(cache_path) as f:
        data = json.load(f)
    assert data["version"] == 1
    assert "entries" in data


def test_load_corrupted_file(tmp_path):
    cache_path = tmp_path / "summaries.json"
    cache_path.write_text("not valid json{{{")
    cache = SummaryCache(cache_path)
    assert cache.entries == {}


def test_load_wrong_version(tmp_path):
    cache_path = tmp_path / "summaries.json"
    cache_path.write_text(json.dumps({"version": 999, "entries": {"s1": {}}}))
    cache = SummaryCache(cache_path)
    # Should still load — version is informational for now
    assert cache.entries == {"s1": {}}


def test_multiple_sessions(tmp_path):
    cache = SummaryCache(tmp_path / "summaries.json")
    cache.set("s1", "First session", "done", 100.0)
    cache.set("s2", "Second session", "in progress", 200.0)
    cache.set("s3", "Third session", "waiting for input", 300.0)
    cache.save()

    cache2 = SummaryCache(tmp_path / "summaries.json")
    assert cache2.get("s1")["summary"] == "First session"
    assert cache2.get("s2")["status"] == "in progress"
    assert cache2.get("s3")["status"] == "waiting for input"


def test_merge_on_save_preserves_concurrent_writes(tmp_path):
    """Simulate concurrent cache writes and verify entries aren't lost.

    Process A loads, adds entries. Then process B writes new entries to disk.
    When process A saves, it should merge B's entries with its own.
    """
    cache_path = tmp_path / "summaries.json"

    # Process A loads cache and adds entries
    cache_a = SummaryCache(cache_path)
    cache_a.set("s1", "Session from A", "done", 100.0)

    # Process B writes to the same file independently
    cache_b = SummaryCache(cache_path)
    cache_b.set("s2", "Session from B", "in progress", 200.0)
    cache_b.save()

    # Process A saves — should merge with B's entries
    cache_a.save()

    # Reload and verify both entries exist
    cache_final = SummaryCache(cache_path)
    assert cache_final.get("s1") is not None
    assert cache_final.get("s1")["summary"] == "Session from A"
    assert cache_final.get("s2") is not None
    assert cache_final.get("s2")["summary"] == "Session from B"


def test_merge_on_save_in_memory_wins_for_same_key(tmp_path):
    """When both processes write the same key, in-memory entry should win."""
    cache_path = tmp_path / "summaries.json"

    # Process A loads and sets s1
    cache_a = SummaryCache(cache_path)
    cache_a.set("s1", "A's version", "done", 200.0)

    # Process B writes s1 with a different value
    cache_b = SummaryCache(cache_path)
    cache_b.set("s1", "B's version", "in progress", 100.0)
    cache_b.save()

    # Process A saves — its entry should win
    cache_a.save()

    cache_final = SummaryCache(cache_path)
    assert cache_final.get("s1")["summary"] == "A's version"


def test_merge_on_save_with_corrupted_disk(tmp_path):
    """If disk cache is corrupted at save time, should proceed with in-memory entries only."""
    cache_path = tmp_path / "summaries.json"

    cache = SummaryCache(cache_path)
    cache.set("s1", "In memory", "done", 100.0)

    # Corrupt the file between load and save
    cache_path.write_text("corrupted{{{not json")

    # Save should succeed without losing in-memory data
    cache.save()

    cache_final = SummaryCache(cache_path)
    assert cache_final.get("s1")["summary"] == "In memory"


def test_merge_preserves_many_entries(tmp_path):
    """Merge should handle many entries from both sources."""
    cache_path = tmp_path / "summaries.json"

    # Process A adds 50 entries
    cache_a = SummaryCache(cache_path)
    for i in range(50):
        cache_a.set(f"a-{i}", f"Summary A-{i}", "done", float(i))

    # Process B adds 50 different entries
    cache_b = SummaryCache(cache_path)
    for i in range(50):
        cache_b.set(f"b-{i}", f"Summary B-{i}", "done", float(i))
    cache_b.save()

    # Process A saves, merging
    cache_a.save()

    cache_final = SummaryCache(cache_path)
    # All 100 entries should be present
    assert len(cache_final.entries) == 100
    for i in range(50):
        assert cache_final.get(f"a-{i}") is not None
        assert cache_final.get(f"b-{i}") is not None
