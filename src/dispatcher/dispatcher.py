import json
import os
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from google.cloud import storage
from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2

FUNCTION_URL = "https://sample-bucket-writer-eu-north-2-752774698672.europe-north2.run.app"

logging.basicConfig(level=logging.INFO)

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
            with open(self.filepath, 'r') as f:
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
    mode = os.environ.get('SCHEDULE_MODE', 'CLOUD')

    if mode == 'CLOUD':
        bucket_name = os.environ.get('BUCKET_NAME', "faas-scheduling-us-east1")
        return GoogleCloudStorageScheduleLoader(bucket_name)
    else:
        path = os.environ.get('SCHEDULE_FILE_PATH', "./data/sample/execution_schedule.json")
        return LocalFileScheduleLoader(path)

def find_best_slot(deadline: datetime, schedule: dict) -> Optional[dict]:
    """
    Finds the region/time with the highest priority before the deadline.
    """
    valid_slots = []

    for slot in schedule['recommendations']:
        try:
            slot_time = datetime.fromisoformat(slot['datetime']).astimezone(timezone.utc)
            now = datetime.now(timezone.utc)
            
            # Constraint: Must be before deadline
            if slot_time < deadline and slot_time > now:
                valid_slots.append({
                    **slot,
                    'parsed_time': slot_time
                })
        except ValueError:
            continue

    if not valid_slots:
        return None

    # If priorities are equal, pick the one that runs earliest.
    valid_slots.sort(key=lambda x: (x['priority'], x['parsed_time']))

    best_slot = valid_slots[0]
    
    del best_slot['parsed_time']
    
    return best_slot
    
def schedule_function(function_url: str, target_time: datetime):
    client = tasks_v2.CloudTasksClient()

    PROJECT_ID = os.environ.get("PROJECT_ID")
    REGION = os.environ.get("REGION")
    QUEUE_NAME = os.environ.get("QUEUE_NAME")

    parent = client.queue_path(PROJECT_ID, REGION, QUEUE_NAME)

    task = {
        'http_request': { 
            'http_method': tasks_v2.HttpMethod.GET,
            'url': function_url,
        }
    }

    timestamp = timestamp_pb2.Timestamp()
    timestamp.FromDatetime(target_time)
        
    task['schedule_time'] = timestamp

    response = client.create_task(request={"parent": parent, "task": task})
    
    logging.info(f"Created task {response.name}")
    logging.info(f"Function will execute at {response.schedule_time}")

def to_aware(dt: datetime) -> datetime:
    # If dt has no timezone, assume UTC
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

def find_optimal_region(deadline: datetime, schedule: dict):
    # Ensure deadline is timezone aware
    deadline = to_aware(deadline)

    # Parse, convert to aware, and sort ascending
    recs = sorted(
        schedule['recommendations'],
        key=lambda r: to_aware(datetime.fromisoformat(r["datetime"]))
    )

    latest = None
    for slot in recs:
        slot_dt = to_aware(datetime.fromisoformat(slot["datetime"]))
        if slot_dt <= deadline:
            latest = slot
        else:
            break

    # If we found a matching previous slot, return that region
    if latest:
        return latest["region"]

    # Otherwise: return the *oldest* entry
    return recs[0]["region"]





def handler(event):
    """
    Entry Point.
    
    Expected Event JSON:
    {
        "function_name": "image_processor",
        "deadline": "2023-10-27T12:00:00Z"
    }
    """
    logging.info(f"Received event: {event}")

    fn_name = event.get('function_name')
    deadline_str = event.get('deadline')

    if not fn_name or not deadline_str:
        return {"statusCode": 400, "body": "Missing 'function_name' or 'deadline'"}

    loader = get_loader()
    schedule = loader.load_schedule(fn_name)

    deadline_dt: datetime

    try:
        # Parse deadline (assuming ISO 8601 format, e.g., "2023-12-31T23:59:59Z")
        deadline_dt = datetime.fromisoformat(deadline_str).replace(tzinfo=timezone.utc)
    except ValueError:
        logging.error("Invalid deadline format. Use ISO 8601.")
        return None

    if deadline_dt < datetime.now(timezone.utc) + timedelta(minutes=5):
        try:
            optimal_region = find_optimal_region(deadline_dt, schedule)
            result = {
                "datetime": str(datetime.now(timezone.utc)),
                "region": optimal_region,
                "priority": 0
            }
        except Exception as e:
            return {
            "statusCode": 404,
            "status": "failed",
            "message": e
        }
    else:
        result = find_best_slot(deadline_dt, schedule)

    logging.info("done")
    if result:
        logging.info(f"Dispatching to {result['region']} at {result['datetime']}")
        schedule_function(FUNCTION_URL, datetime.fromisoformat(result['datetime']).replace(tzinfo=timezone.utc))
        return {
            "statusCode": 200,
            "status": "scheduled",
            "function": fn_name,
            "target_region": result['region'],
            "target_time": result['datetime'],
            "priority": result['priority']
        }
    else:
        return {
            "statusCode": 404,
            "status": "failed",
            "message": "No suitable slot found before deadline."
        }

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    event = {
        "function_name": "write_to_bucket",
        "deadline": "2025-12-01T20:01:00"
    }

    logging.info(handler(event))