import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from exporter import date_window


def test_date_window_is_today_only():
    assert date_window(date(2026, 11, 15)) == (date(2026, 11, 15), date(2026, 11, 15))


def test_date_window_is_today_only_at_month_and_year_boundaries():
    assert date_window(date(2027, 1, 1)) == (date(2027, 1, 1), date(2027, 1, 1))
