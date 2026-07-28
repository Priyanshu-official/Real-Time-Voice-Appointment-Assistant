from datetime import datetime, timezone
import pytest
from app.api.services.scheduling import normalize_datetime_utterance


def test_normalize_datetime_explicit_full():
    ref_dt = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
    iso_str, reason = normalize_datetime_utterance("November 28th at 10 AM", reference_datetime=ref_dt)
    assert reason is None
    assert iso_str == "2026-11-28T10:00:00Z"


def test_normalize_datetime_relative_date():
    ref_dt = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)  # Tuesday
    iso_str, reason = normalize_datetime_utterance("next Tuesday at 10 in the morning", reference_datetime=ref_dt)
    assert reason is None
    assert iso_str is not None
    assert "T10:00:00Z" in iso_str


def test_normalize_datetime_missing_time():
    ref_dt = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
    iso_str, reason = normalize_datetime_utterance("November 28th", reference_datetime=ref_dt)
    assert iso_str is None
    assert reason is not None
    assert "missing time" in reason.lower()


def test_normalize_datetime_missing_date():
    ref_dt = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc)
    iso_str, reason = normalize_datetime_utterance("at 10 AM", reference_datetime=ref_dt)
    assert iso_str is None
    assert reason is not None
    assert "missing date" in reason.lower()


def test_normalize_datetime_ambiguous_year_rollover():
    # If today is Dec 28, 2026 and caller asks for "January 5th at 10 AM"
    ref_dt = datetime(2026, 12, 28, 10, 0, 0, tzinfo=timezone.utc)
    iso_str, reason = normalize_datetime_utterance("January 5th at 10 AM", reference_datetime=ref_dt)
    assert reason is None
    assert iso_str == "2027-01-05T10:00:00Z"


def test_year_normalization_defaults_to_reference_year():
    # Test year missing from utterance defaults to reference datetime year (2026), never 2023
    ref_dt = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)
    iso_str, reason = normalize_datetime_utterance("July 28th at 10 AM", reference_datetime=ref_dt)
    assert reason is None
    assert iso_str == "2026-07-28T10:00:00Z"
    assert "2023" not in iso_str


def test_year_normalization_roll_forward_past_date():
    # If today is Dec 31, 2026 and caller says "Jan 1st at 10 AM" (missing year, past date in 2026)
    ref_dt = datetime(2026, 12, 31, 23, 0, 0, tzinfo=timezone.utc)
    iso_str, reason = normalize_datetime_utterance("Jan 1st at 10 AM", reference_datetime=ref_dt)
    assert reason is None
    assert iso_str == "2027-01-01T10:00:00Z"

