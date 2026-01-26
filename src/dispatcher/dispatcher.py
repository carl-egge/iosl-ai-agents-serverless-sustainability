import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from google.cloud import storage, tasks_v2
from google.protobuf import timestamp_pb2


class ScheduleLoader(ABC):
    """Abstract Base Class for loading schedules."""

    @abstractmethod
    def load_schedule(self, function_name: str) -> Dict[str, List[Dict[str, Any]]]:
        pass


class LocalFileScheduleLoader(ScheduleLoader):
    """Loads schedule from a local JSON file (good for local testing)."""

    def __init__(self, filepath: str):
        self.filepath = filepath

    def load_schedule(self, function_name: str) -> Dict[str, List[Dict[str, Any]]]:
        try:
            with open(self.filepath + "schedule_" + function_name + ".json", "r") as f:
                return json.load(f)
        except FileNotFoundError:
            logging.error(f"Schedule file not found at {self.filepath}")
            return {}


class GoogleCloudStorageScheduleLoader(ScheduleLoader):
    def __init__(self, bucket_name: str):
        self.bucket_name = bucket_name

    def load_schedule(self, function_name: str):
        storage_client = storage.Client()
        bucket = storage_client.bucket(self.bucket_name)
        blob = bucket.blob("schedule_" + function_name + ".json")
        return json.loads(blob.download_as_string())


def get_loader() -> ScheduleLoader:
    mode = os.environ.get("SCHEDULE_LOCATION", "CLOUD")

    if mode == "CLOUD":
        bucket_name = os.environ.get("GCS_BUCKET_NAME", "faas-scheduling-us-east1")
        return GoogleCloudStorageScheduleLoader(bucket_name)
    else:
        path = os.environ.get(
            "SCHEDULE_FILE_PATH", "./local_bucket/"
        )
        return LocalFileScheduleLoader(path)

def add_to_task_queue(function_url: str, function_param: dict, target_time: datetime):
    client = tasks_v2.CloudTasksClient()

    PROJECT_ID = os.environ.get("PROJECT_ID")
    REGION = os.environ.get("REGION")
    QUEUE_NAME = os.environ.get("QUEUE_NAME")

    parent = client.queue_path(PROJECT_ID, REGION, QUEUE_NAME)

    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": function_url,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(function_param).encode("utf-8"),
        }
    }

    timestamp = timestamp_pb2.Timestamp()
    timestamp.FromDatetime(target_time)

    task["schedule_time"] = timestamp

    response = client.create_task(request={"parent": parent, "task": task})

    logging.info(f"Created task {response.name}")
    logging.info(f"Function will execute at {response.schedule_time}")

def normalize_to_utc(dt_str: str) -> datetime:
    dt = datetime.fromisoformat(dt_str)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt

def filter_schedule(function_name: str, deadline: datetime) -> dict:
    schedule = get_loader().load_schedule(function_name)

    recommendations = schedule["recommendations"]

    for rec in recommendations:
        rec["datetime"] = normalize_to_utc(rec["datetime"])

    recommendations.sort(key=lambda r: r["datetime"])

    if deadline < recommendations[0]["datetime"]:
        recommendations[0]["datetime"] = deadline
        return recommendations[0]
    
    filtered_recommendations = list(filter(lambda r: r["datetime"] <= deadline and r["datetime"] >= datetime.now(timezone.utc).replace(microsecond=0, second=0, minute=0), recommendations))

    if filtered_recommendations == []:
        recommendations[-1]["datetime"] = deadline.replace(microsecond=0, second=0, minute=0)
        return recommendations[-1]

    filtered_recommendations.sort(key = lambda r: r["priority"])
    
    return filtered_recommendations[0]

def find_optimal_slot(function_name: str, deadline: datetime | None) -> dict:
    if deadline is None:
        optimal_slot = filter_schedule(function_name, datetime.now(timezone.utc))
        optimal_slot["delay"] = "false"
        return optimal_slot
    
    if deadline < datetime.now(timezone.utc):
        deadline = datetime.now(timezone.utc)
        try:
            optimal_slot = filter_schedule(function_name, deadline)
            optimal_slot["delay"] = "false"
            return optimal_slot
        except Exception as e:
            return {"statusCode": 404, "status": "failed", "message": e}
    else:
        optimal_slot = filter_schedule(function_name, deadline)
        optimal_slot["delay"] = "true"
        return optimal_slot


def handler(event: dict) -> dict:
    """
    Entry Point.

    Expected Event JSON:
    {
        "function_name": "image_processor",
        "function_param": {...},
        "delay": "false",
        "deadline": "2023-10-27T12:00:00Z",
    }

    Delay takes precedence over the deadline.
    """
    logging.basicConfig(level=os.environ.get("LOG_LEVEL"))

    logging.info(f"Received event: {event}")

    function_name = event.get("function_name")
    if not function_name:
        return {"statusCode": 400, "error": "Missing 'function_name'"}

    delay = event.get("delay")

    if delay is not None:
        if str.lower(delay) == "false":
            result = find_optimal_slot(function_name, None)
            return schedule_function(result, function_name, event.get("function_param"))
        if str.lower(delay) == "true":
            pass
        else:
            return {
                "statusCode": 400,
                "error": "Delay must be 'true' or 'false', but was " + delay,
            }

    deadline_str = event.get("deadline")

    if deadline_str is None:
        return {"statusCode": 400, "error": "Delay was true but no deadline was given"}

    deadline_dt: datetime

    try:
        # Parse deadline (assuming ISO 8601 format, e.g., "2023-12-31T23:59:59Z")
        deadline_dt = datetime.fromisoformat(deadline_str).replace(tzinfo=timezone.utc)
    except ValueError:
        logging.error(f"Deadline {deadline_str} has invalid format")
        return {
            "statusCode": 400,
            "error": "Deadline " + deadline_str + " has invalid format. Use ISO 8601.",
        }

    if deadline_dt < datetime.now(timezone.utc)+ timedelta(minutes=1):
        logging.info(f"{datetime.now(timezone.utc)} Deadline {deadline_dt} is suspiciously early")

    result = find_optimal_slot(function_name, deadline_dt)

    return schedule_function(result, function_name, event.get("function_param"))

def schedule_function(slot: dict, function_name: str, function_param: dict) -> dict:
    logging.info("done")
    if slot:
        logging.info(f"Dispatching to {(slot['region'])} at {slot['datetime']}")
        if os.environ.get("SCHEDULE_MODE", "NONE") == "CLOUD":
            add_to_task_queue(
                slot["function_url"],
                function_param,
                slot["datetime"].replace(tzinfo=timezone.utc),
            )
        return {
            "statusCode": 200,
            "status": "scheduled",
            "function": function_name,
            "delay": slot["delay"],
            "target_region": slot["region"],
            "target_time": slot["datetime"],
            "priority": slot["priority"],
            "carbon_intensity": slot["carbon_intensity"],
            "function_url": slot["function_url"],
        }
    else:
        return {
            "statusCode": 404,
            "status": "failed",
            "message": "No suitable slot found before deadline.",
        }


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    event = {"function_name": "crypto_key_gen", "deadline": "2027-01-26T14:01:00", "function_param": {"bits": 4096}}

    logging.info(handler(event))
