from datetime import datetime
import pytest
from freezegun import freeze_time

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

def test_when_noFunctionName_then_return400():
    event = {"deadline": "2023-10-27T12:00:00Z"}

    response = dispatcher.handler(event)

    assert response.get("statusCode") == 400, response.get("error")


def test_when_delayFalse_then_executeDirectly():
    event = {"function_name": "dummy", "delay": "False"}

    response = dispatcher.handler(event)

    assert response.get("delay") == "false", response.get("error")


def test_when_delayInvalid_then_return400():
    event = {"function_name": "dummy", "delay": "arldsfja"}

    response = dispatcher.handler(event)

    assert response.get("statusCode") == 400, response.get("error")


def test_when_delayTrueAndDeadlineIsNone_then_return400():
    event = {"function_name": "dummy", "delay": "True"}

    response = dispatcher.handler(event)

    assert response.get("statusCode") == 400, response.get("error")


def test_when_delayTrueAndInvalidDeadline_then_return400():
    event = {
        "function_name": "dummy",
        "delay": "true",
        "deadline": "2025:11:11T12:12:12",
    }

    response = dispatcher.handler(event)

    assert response.get("statusCode") == 400, response.get("error")

def test_when_delayTrueAndValidDeadline_then_scheduleFunction(mocker):
    optimal_slot = {
        "region": "se",
        "datetime": "2025-12-10T22:12:12",
        "priority": 3,
        "delay": "true",
    }

    mocker.patch(
        "src.dispatcher.dispatcher.find_optimal_slot",
        return_value=optimal_slot,
    )
    event = {
        "function_name": "dummy",
        "delay": "true",
        "deadline": "2025-12-10T22:12:12",
    }

    expected = {
        "statusCode": 200,
        "status": "scheduled",
        "function": event["function_name"],
        "delay": "true",
        "target_region": optimal_slot["region"],
        "target_time": optimal_slot["datetime"],
        "priority": optimal_slot["priority"],
        "url": "function.test",
    }

    response = dispatcher.handler(event)

    assert response == expected

@freeze_time("2025-12-10T16:35:00+00:00")
def test_when_delayTrueAndValidDeadline_then_scheduleFunction():
    event = {
        "function_name": "dummy",
        "delay": "true",
        "deadline": "2025-12-10T22:12:12",
    }

    expected = {
        "statusCode": 200,
        "status": "scheduled",
        "function": event["function_name"],
        "delay": "true",
        "target_region": "REGION-1",
        "target_time": datetime.fromisoformat("2025-12-10T22:00:00+00:00"),
        "priority": 1,
        "url": "function.test",
    }

    response = dispatcher.handler(event)

    assert response == expected

@freeze_time("2025-12-10T17:35:00+00:00")
def test_when_delayFalse_then_scheduleDirectly():
    event = {
        "function_name": "dummy",
        "delay": "false",
        "deadline": "2025-12-10T22:12:12",
    }

    expected = {
        "statusCode": 200,
        "status": "scheduled",
        "function": event["function_name"],
        "delay": "false",
        "target_region": "REGION-6",
        "target_time": datetime.fromisoformat("2025-12-10T17:00:00+00:00"),
        "priority": 6,
        "url": "function.test",
    }

    response = dispatcher.handler(event)

    assert response == expected