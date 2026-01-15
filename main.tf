terraform {
  required_providers {
    google = {
      source  = "opentofu/google"
      version = "7.15.0"
    }
    archive = {
      source  = "opentofu/archive"
      version = "2.7.1"
    }
  }
}

provider "google" {
  project = var.project_id
}

resource "google_secret_manager_secret" "gemini_secret" {
  secret_id = "gemini_secret"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "gemini_secret" {
  secret      = google_secret_manager_secret.gemini_secret.secret_id
  secret_data = var.gemini_api_key
}

resource "google_secret_manager_secret" "electricitymaps_secret" {
  secret_id = "electricitymaps_secret"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "electricitymaps_secret" {
  secret      = google_secret_manager_secret.electricitymaps_secret.secret_id
  secret_data = var.electricitymaps_api_key
}

data "google_project" "current" {}

resource "google_secret_manager_secret_iam_member" "gemini_accessor" {
  secret_id = google_secret_manager_secret.gemini_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_secret_manager_secret_iam_member" "electricitymaps_accessor" {
  secret_id = google_secret_manager_secret.electricitymaps_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_storage_bucket" "scheduler_bucket" {
  name                        = "iosl-scheduler-bucket"
  location                    = "US"
  uniform_bucket_level_access = true
}

resource "google_storage_bucket_object" "dispatcher_object" {
  name   = "dispatcher-${data.archive_file.dispatcher_archiver.output_md5}.zip"
  bucket = google_storage_bucket.scheduler_bucket.name
  source = data.archive_file.dispatcher_archiver.output_path
}

resource "google_storage_bucket_object" "agent_object" {
  name   = "agent-${data.archive_file.agent_archiver.output_md5}.tar.gz"
  bucket = google_storage_bucket.scheduler_bucket.name
  source = data.archive_file.agent_archiver.output_path
}

resource "google_cloudfunctions2_function" "dispatcher_function" {
  name     = "dispatcher-function"
  location = var.agent_region

  build_config {
    runtime     = "python314"
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
    available_memory   = "256M"
    timeout_seconds    = 60
    environment_variables = {
      PROJECT_ID        = "iosl-faas-scheduling"
      REGION            = "us-east1"
      QUEUE_NAME        = "delayedtasks"
      SCHEDULE_MODE     = "CLOUD"
      SCHEDULE_LOCATION = "CLOUD"
    }
  }
}

resource "google_artifact_registry_repository" "agent_repo" {
  location      = "us-east1"
  repository_id = "agent-repo"
  format        = "DOCKER"
}

resource "google_cloud_run_v2_service" "agent" {
  provider = google-beta
  project  = data.google_project.current.project_id
  name     = "agent-function"
  location = "us-east1"
  ingress  = "INGRESS_TRAFFIC_ALL"

  deletion_protection = false

  template {
    timeout = "300s"

    containers {
      image          = "scratch"
      base_image_uri = "us-central1-docker.pkg.dev/serverless-runtimes/google-22/runtimes/python313"
      command        = ["/bin/sh"]
      args = [
        "-c",
        "pip install -r requirements.txt && python -m gunicorn --bind :8080 --workers 1 --threads 8 --timeout 300 main:app"
      ]
      source_code {
        cloud_storage_source {
          bucket     = google_storage_bucket.scheduler_bucket.name
          object     = google_storage_bucket_object.agent_object.name
          generation = google_storage_bucket_object.agent_object.generation
        }
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      # Environment Variables
      env {
        name  = "GCS_BUCKET_NAME"
        value = "faas-scheduling-us-east1"
      }

      # Secret Mappings
      env {
        name = "ELECTRICITYMAPS_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.electricitymaps_secret.secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini_secret.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [google_storage_bucket_object.agent_object]
}

data "archive_file" "dispatcher_archiver" {
  type        = "zip"
  output_path = "out/dispatcher.zip"
  source_dir  = "src/dispatcher/"
  excludes    = [".env", "__pycache__/*", "*.sh", "env.yaml", ".gcloudignore"]
}

data "archive_file" "agent_archiver" {
  type        = "tar.gz"
  output_path = "out/agent.tar.gz"
  source_dir  = "src/agent/gcp_deploy/"
  excludes    = ["__pycache__/*", "*.md"]
}



