from datetime import date

import pytest

from app.franchise.routes import parse_date


def test_parse_date_accepts_iso_date():
    assert parse_date("2025-03-01", "Agreement Start Date") == date(2025, 3, 1)


def test_parse_date_rejects_more_than_four_year_digits():
    with pytest.raises(ValueError, match="four-digit year"):
        parse_date("262025-03-01", "Agreement Start Date")


def test_parse_date_rejects_invalid_calendar_date():
    with pytest.raises(ValueError, match="valid calendar date"):
        parse_date("2025-02-30", "Agreement Start Date")


def test_parse_date_rejects_year_outside_supported_range():
    with pytest.raises(ValueError, match="between 1900 and 2100"):
        parse_date("2200-03-01", "Agreement Start Date")
