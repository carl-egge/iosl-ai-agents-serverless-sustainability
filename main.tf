terraform {
  required_providers {
    google = {
      source = "opentofu/google"
      version = "7.15.0"
    }
    archive = {
      source = "opentofu/archive"
      version = "2.7.1"
    }
  }
}

provider "google" {
  project = var.project_id
}

resource "google_storage_bucket" "scheduler_bucket" {
    name = "iosl-scheduler-bucket"
    location = "US"
    uniform_bucket_level_access = true
}

resource "google_storage_bucket_object" "dispatcher_object" {
  name = "dispatcher.zip"
  bucket = google_storage_bucket.scheduler_bucket.name
  source = "out/dispatcher.zip"
}

resource "google_cloudfunctions2_function" "dispatcher_function" {
    name = "dispatcher-function"
    location = var.agent_region

    build_config {
        runtime = "python314"
        entry_point = "event"
        source {
            storage_source {
                bucket = google_storage_bucket.scheduler_bucket.name
                object = google_storage_bucket_object.dispatcher_object.name
            }
        }
    }

    service_config {
        max_instance_count = 1
        available_memory = "256M"
        timeout_seconds = 60
        environment_variables = {
            PROJECT_ID = "iosl-faas-scheduling"
            REGION = "us-east1"
            QUEUE_NAME = "delayedtasks"
            SCHEDULE_MODE = "CLOUD"
            SCHEDULE_LOCATION = "CLOUD"
        }
    }
}

data "archive_file" "dispatcher_archiver" {
    type = "zip"
    output_path = "out/dispatcher.zip"
    source_dir = "src/dispatcher/"
    excludes = [".env", "__pycache__/*", "*.sh", "env.yaml", ".gcloudignore"]
}



