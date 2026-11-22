import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import notion_reader


def _capture_query(monkeypatch):
    calls = []
    monkeypatch.setattr(notion_reader, "DB", "test-database")

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        return {"results": [], "has_more": False}

    monkeypatch.setattr(notion_reader, "request", fake_request)
    return calls


def test_query_database_today_uses_exact_date_filter(monkeypatch):
    calls = _capture_query(monkeypatch)
    notion_reader.query_database(date_property="날짜", start_date="2026-11-15", end_date="2026-11-15")
    assert calls[0][2]["filter"] == {"property": "날짜", "date": {"equals": "2026-11-15"}}


def test_query_database_range_uses_inclusive_bounds(monkeypatch):
    calls = _capture_query(monkeypatch)
    notion_reader.query_database(date_property="날짜", start_date="2026-11-09", end_date="2026-11-15")
    assert calls[0][2]["filter"] == {
        "property": "날짜",
        "date": {"on_or_after": "2026-11-09", "on_or_before": "2026-11-15"},
    }


def test_query_database_all_has_no_date_filter(monkeypatch):
    calls = _capture_query(monkeypatch)
    notion_reader.query_database()
    assert "filter" not in calls[0][2]


def test_query_database_rejects_mixed_exact_and_range_filters(monkeypatch):
    _capture_query(monkeypatch)
    with pytest.raises(ValueError, match="함께 사용할 수 없습니다"):
        notion_reader.query_database(date_property="날짜", on_date="2026-11-15", start_date="2026-11-01")


def test_query_database_requires_date_property_for_date_filter(monkeypatch):
    _capture_query(monkeypatch)
    with pytest.raises(ValueError, match="date_property"):
        notion_reader.query_database(start_date="2026-11-01")


def test_query_database_last_edited_after_uses_timestamp_filter(monkeypatch):
    calls = _capture_query(monkeypatch)
    notion_reader.query_database(last_edited_after="2026-11-08T00:00:00Z")
    assert calls[0][2]["filter"] == {
        "timestamp": "last_edited_time",
        "last_edited_time": {"on_or_after": "2026-11-08T00:00:00Z"},
    }


def test_query_database_rejects_combining_edit_time_and_date_filters(monkeypatch):
    _capture_query(monkeypatch)
    with pytest.raises(ValueError, match="함께 사용할 수 없습니다"):
        notion_reader.query_database(
            date_property="날짜",
            start_date="2026-11-01",
            last_edited_after="2026-11-08T00:00:00Z",
        )
