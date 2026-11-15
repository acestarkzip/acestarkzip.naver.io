import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from exporter import date_window


def test_date_window_includes_two_previous_days_and_tomorrow():
    assert date_window(date(2026, 11, 15)) == (
        date(2026, 11, 13),
        date(2026, 11, 16),
    )


def test_date_window_crosses_month_and_year_boundaries():
    assert date_window(date(2027, 1, 1)) == (
        date(2026, 12, 30),
        date(2027, 1, 2),
    )
