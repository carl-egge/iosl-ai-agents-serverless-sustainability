import json
import os
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional, Any
from google.cloud import storage
from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2

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

def find_best_slot(deadline_str: str, schedule: dict) -> Optional[dict]:
    """
    Finds the region/time with the highest priority before the deadline.
    """

    try:
        # Parse deadline (assuming ISO 8601 format, e.g., "2023-12-31T23:59:59Z")
        deadline_dt = datetime.fromisoformat(deadline_str).astimezone(timezone.utc)
    except ValueError:
        logging.error("Invalid deadline format. Use ISO 8601.")
        return None

    valid_slots = []

    for slot in schedule['recommendations']:
        try:
            slot_time = datetime.fromisoformat(slot['datetime']).astimezone(timezone.utc)
            now = datetime.now(timezone.utc)
            
            # Constraint: Must be before deadline
            if slot_time < deadline_dt and slot_time > now:
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

def get_loader() -> ScheduleLoader:
    mode = os.environ.get('SCHEDULE_MODE', 'CLOUD')

    if mode == 'CLOUD':
        bucket_name = os.environ.get('BUCKET_NAME', "faas-scheduling-us-east1")
        return GoogleCloudStorageScheduleLoader(bucket_name)
    else:
        path = os.environ.get('SCHEDULE_FILE_PATH', "./data/sample/execution_schedule.json")
        return LocalFileScheduleLoader(path)
    
def schedule_delayed_function(function_url: str, target_time: datetime):
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

    if target_time > datetime.now(timezone.utc):        
        timestamp = timestamp_pb2.Timestamp()
        timestamp.FromDatetime(target_time)
        
        task['schedule_time'] = timestamp

    response = client.create_task(request={"parent": parent, "task": task})
    
    logging.info(f"Created task {response.name}")
    logging.info(f"Function will execute at {response.schedule_time}")


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
    deadline = event.get('deadline')

    if not fn_name or not deadline:
        return {"statusCode": 400, "body": "Missing 'function_name' or 'deadline'"}

    loader = get_loader()
    schedule = loader.load_schedule(fn_name)

    result = find_best_slot(deadline, schedule)

    logging.info("done")
    if result:
        logging.info(f"Dispatching to {result['region']} at {result['datetime']}")
        schedule_delayed_function("https://sample-bucket-writer-eu-north-2-752774698672.europe-north2.run.app", datetime.fromisoformat(result['datetime']).astimezone(timezone.utc))
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
    event = json.loads("""{
        "function_name": "write_to_bucket",
        "deadline": "2025-12-20T13:56:00"
    }""")

    logging.info(handler(event))