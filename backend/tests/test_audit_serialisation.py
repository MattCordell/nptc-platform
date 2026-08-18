"""Offline unit tests for nptc.audit.serialisation (issue #37).

No container, no network - pure value normalisation.
"""

from __future__ import annotations

import ipaddress
import math
import uuid
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum, StrEnum

import pytest

from nptc.audit.serialisation import UnserialisableAuditValueError, normalise_json_value
from nptc_shared.sctid import SCTID


def test_none_bool_int_pass_through() -> None:
    assert normalise_json_value(None) is None
    assert normalise_json_value(True) is True
    assert normalise_json_value(False) is False
    assert normalise_json_value(7) == 7


def test_bool_is_not_normalised_as_int() -> None:
    """bool is an int subclass in Python - it must be recognised as bool
    before the int branch, or True/False would still normalise fine (the
    handling is identical) but the type-ordering rule itself is worth its
    own regression test."""
    assert normalise_json_value(True) is True


def test_float_passes_through() -> None:
    assert normalise_json_value(1.5) == 1.5


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nan_and_inf_are_refused(value: float) -> None:
    with pytest.raises(UnserialisableAuditValueError):
        normalise_json_value(value)


def test_plain_str_round_trips() -> None:
    assert normalise_json_value("hello") == "hello"


def test_nul_byte_in_str_is_refused() -> None:
    with pytest.raises(UnserialisableAuditValueError):
        normalise_json_value("bad\x00value")


def test_nul_byte_nested_in_mapping_is_refused() -> None:
    with pytest.raises(UnserialisableAuditValueError):
        normalise_json_value({"field": "bad\x00value"})


class _Colour(StrEnum):
    RED = "red"


class _Priority(Enum):
    LOW = 1


def test_strenum_normalises_to_its_plain_str_value() -> None:
    result = normalise_json_value(_Colour.RED)
    assert result == "red"
    assert type(result) is str


def test_non_str_enum_recurses_on_its_value() -> None:
    assert normalise_json_value(_Priority.LOW) == 1


def test_decimal_normalises_to_str_never_float() -> None:
    result = normalise_json_value(Decimal("1.50"))
    assert result == "1.50"
    assert isinstance(result, str)


def test_sctid_normalises_to_its_string_value() -> None:
    sctid = SCTID("873871000168106")
    assert normalise_json_value(sctid) == "873871000168106"


def test_int_where_an_sctid_is_expected_is_the_callers_job_to_wrap() -> None:
    """FR-06: a bare int is never silently treated as an SCTID by this
    module - it normalises as a plain int, which is exactly why a caller
    must wrap catalogue codes in SCTID before they ever reach a diff."""
    assert normalise_json_value(123456) == 123456


def test_uuid_normalises_to_str() -> None:
    value = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert normalise_json_value(value) == str(value)


def test_ipv4_address_normalises_to_str() -> None:
    value = ipaddress.ip_address("203.0.113.7")
    assert normalise_json_value(value) == "203.0.113.7"


def test_datetime_normalises_to_utc_fixed_microseconds() -> None:
    value = datetime(2026, 8, 17, 12, 30, 0, 123456, tzinfo=timezone(timedelta(hours=10)))
    result = normalise_json_value(value)
    assert result == value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_date_normalises_to_isoformat() -> None:
    assert normalise_json_value(date(2026, 8, 17)) == "2026-08-17"


def test_time_normalises_to_isoformat() -> None:
    assert normalise_json_value(time(12, 30, 0)) == "12:30:00"


def test_mapping_recurses_and_stringifies_keys() -> None:
    assert normalise_json_value({"a": 1, "b": [1, 2]}) == {"a": 1, "b": [1, 2]}


def test_non_str_mapping_key_is_refused() -> None:
    with pytest.raises(UnserialisableAuditValueError):
        normalise_json_value({1: "a"})


def test_list_and_tuple_recurse() -> None:
    assert normalise_json_value([1, "a", None]) == [1, "a", None]
    assert normalise_json_value((1, "a", None)) == [1, "a", None]


def test_unknown_type_raises_rather_than_stringifying() -> None:
    class Unrecognised:
        pass

    with pytest.raises(UnserialisableAuditValueError):
        normalise_json_value(Unrecognised())


def test_depth_cap_raises_on_pathological_nesting() -> None:
    value: object = "leaf"
    for _ in range(64):
        value = [value]

    with pytest.raises(UnserialisableAuditValueError):
        normalise_json_value(value)


def test_reasonable_nesting_does_not_trip_the_depth_cap() -> None:
    value: object = "leaf"
    for _ in range(5):
        value = [value]

    result = normalise_json_value(value)
    for _ in range(5):
        assert isinstance(result, list)
        result = result[0]
    assert result == "leaf"
