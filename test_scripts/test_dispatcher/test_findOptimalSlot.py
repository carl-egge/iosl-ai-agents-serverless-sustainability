from freezegun import freeze_time
from datetime import datetime
import pytest

import src.dispatcher.dispatcher as dispatcher

@pytest.fixture(autouse=True)
def env_setup(monkeypatch):
    monkeypatch.setenv("SCHEDULE_LOCATION", "LOCAL")
    monkeypatch.setenv(
        "SCHEDULE_FILE_PATH",
        "test_scripts/test_dispatcher/resources/test_schedule_10122513_11122512.json",
    )
    monkeypatch.setenv("LOG_LEVEL", "CRITICAL")
    monkeypatch.setenv("SLOT_SIZE_MINUTES", "60")


@freeze_time("2025-12-05")
@pytest.mark.parametrize(
    "deadline, expected_delay, expected_datetime, expected_region",
    [
        (
            None,
            "false",
            datetime.fromisoformat("2025-12-05T00:00:00+00:00"),
            "REGION-10",
        ),
        (
            datetime.fromisoformat("2025-12-04T12:00:00+00:00"),
            "false",
            datetime.fromisoformat("2025-12-05T00:00:00+00:00"),
            "REGION-10",
        ),
        (
            datetime.fromisoformat("2025-12-06T12:00:00+00:00"),
            "true",
            datetime.fromisoformat("2025-12-06T12:00:00+00:00"),
            "REGION-10",
        ),
        (
            datetime.fromisoformat("2025-12-13T12:00:00+00:00"),
            "true",
            datetime.fromisoformat("2025-12-10T22:00:00+00:00"),
            "REGION-1",
        ),
        (
            datetime.fromisoformat("2025-12-10T21:00:00+00:00"),
            "true",
            datetime.fromisoformat("2025-12-10T19:00:00+00:00"),
            "REGION-2",
        ),
    ],
    ids=[
        "test_when_deadlineIsNone_then_executeImmediatelyInEarliestRegion",
        "test_when_deadlineBeforeNowAndBeforeAllSlots_then_executeImmediatelyInEarliestRegion",
        "test_when_deadlineAfterNowAndBeforeAllSlots_then_findFirstSlot",
        "test_when_deadlineAfterAllSlots_then_findOptiomalSlot",
        "test_when_deadlineInBetweenSlots_then_findOptimalSlot",
    ],
)
def test_timeBeforeSchedule(
    deadline,
    expected_delay,
    expected_datetime,
    expected_region,
):
    result = dispatcher.find_optimal_slot("dummy", deadline)

    assert result["delay"] == expected_delay
    assert result["datetime"] == expected_datetime
    assert result["region"] == expected_region
    assert result["url"] == "function.test"




@freeze_time("2025-12-10T16:35:00+00:00")
@pytest.mark.parametrize(
    "deadline, expected_delay, expected_datetime, expected_region",
    [
        (
            None,
            "false",
            datetime.fromisoformat("2025-12-10T16:00:00+00:00"),
            "REGION-7",
        ),
        (
            datetime.fromisoformat("2025-12-09T12:00:00+00:00"),
            "false",
            datetime.fromisoformat("2025-12-10T16:00:00+00:00"),
            "REGION-7",
        ),
        (
            datetime.fromisoformat("2025-12-13T12:00:00+00:00"),
            "true",
            datetime.fromisoformat("2025-12-10T22:00:00+00:00"),
            "REGION-1",
        ),
        (
            datetime.fromisoformat("2025-12-10T21:00:00+00:00"),
            "true",
            datetime.fromisoformat("2025-12-10T19:00:00+00:00"),
            "REGION-2",
        ),
    ],
    ids=[
        "test_when_deadlineIsNone_then_executeImmediatelyInOptimalRegion",
        "test_when_deadlineBeforeNowAndBeforeAllSlots_then_executeImmediatelyInEarliestRegion",
        "test_when_deadlineAfterNowAndAfterAllSlots_then_executeInOptimalRegion",
        "test_when_deadlineInBetweenSlots_then_findOptimalSlot",
    ],
)
def test_timeDuringSchedule(
        deadline,
        expected_delay,
        expected_datetime,
        expected_region,
):
    result = dispatcher.find_optimal_slot("dummy", deadline)

    assert result["delay"] == expected_delay
    assert result["datetime"] == expected_datetime
    assert result["region"] == expected_region
    assert result["url"] == "function.test"


@freeze_time("2025-12-13T16:35:00+00:00")
@pytest.mark.parametrize(
    "deadline, expected_delay, expected_datetime, expected_region",
    [
        (
            None,
            "false",
            datetime.fromisoformat("2025-12-13T16:00:00+00:00"),
            "REGION-24",
        ),
        (
            datetime.fromisoformat("2025-12-09T12:00:00+00:00"),
            "false",
            datetime.fromisoformat("2025-12-13T16:00:00+00:00"),
            "REGION-24",
        ),
        (
            datetime.fromisoformat("2025-12-14T12:45:00+00:00"),
            "true",
            datetime.fromisoformat("2025-12-14T12:00:00+00:00"),
            "REGION-24",
        ),
        (
            datetime.fromisoformat("2025-12-10T21:00:00+00:00"),
            "false",
            datetime.fromisoformat("2025-12-13T16:00:00+00:00"),
            "REGION-24",
        ),
    ],
    ids=[
        "test_when_deadlineIsNone_then_executeImmediatelyInLastRegion",
        "test_when_deadlineBeforeNowAndBeforeAllSlots_then_executeImmediatelyInLastRegion",
        "test_when_deadlineAfterNowAndAfterAllSlots_then_executeDelayedInLastRegion",
        "test_when_deadlineInBetweenSlots_then_executeImmediatelyInLastRegion",
    ],
)
def test_timeAfterSchedule(
        deadline,
        expected_delay,
        expected_datetime,
        expected_region,
):
    result = dispatcher.find_optimal_slot("dummy", deadline)

    assert result["delay"] == expected_delay
    assert result["datetime"] == expected_datetime
    assert result["region"] == expected_region
    assert result["url"] == "function.test"