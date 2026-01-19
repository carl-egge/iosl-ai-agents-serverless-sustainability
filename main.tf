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

data "google_project" "current" {}


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

resource "google_service_account" "mcp_deployer" {
  account_id   = "mcp-deployer-iac"
  display_name = "MCP Function Deployer"
  project      = var.project_id
}

resource "google_project_iam_member" "mcp_sa_roles" {
  for_each = toset([
    "roles/cloudfunctions.developer",
    "roles/storage.objectAdmin",
    "roles/iam.serviceAccountUser"
  ])

  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.mcp_deployer.email}"
}

resource "google_secret_manager_secret" "mcp_secret" {
  secret_id = "mcp_secret"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "mcp_api_key_version" {
  secret      = google_secret_manager_secret.mcp_secret.id
  secret_data = var.mcp_api_key
}

resource "google_secret_manager_secret_iam_member" "mcp_accessor" {
  secret_id = google_secret_manager_secret.mcp_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.mcp_deployer.email}"
}

resource "google_storage_bucket" "source_bucket" {
  name                        = "iosl-source-bucket"
  location                    = var.region
  uniform_bucket_level_access = true
}

resource "google_storage_bucket_object" "dispatcher_object" {
  name   = "dispatcher-${data.archive_file.dispatcher_archiver.output_md5}.zip"
  bucket = google_storage_bucket.source_bucket.name
  source = data.archive_file.dispatcher_archiver.output_path
}

resource "google_storage_bucket_object" "agent_object" {
  name   = "agent-${data.archive_file.agent_archiver.output_md5}.tar.gz"
  bucket = google_storage_bucket.source_bucket.name
  source = data.archive_file.agent_archiver.output_path
}

resource "google_storage_bucket_object" "mcp_object" {
  name   = "mcp-${data.archive_file.mcp_archiver.output_md5}.tar.gz"
  bucket = google_storage_bucket.source_bucket.name
  source = data.archive_file.mcp_archiver.output_path
}

resource "google_cloudfunctions2_function" "dispatcher_function" {
  name     = "dispatcher-function"
  location = var.region

  

  build_config {
    runtime     = "python314"
    entry_point = "event"
    source {
      storage_source {
        bucket = google_storage_bucket.source_bucket.name
        object = google_storage_bucket_object.dispatcher_object.name
      }
    }
  }

  service_config {
    ingress_settings = "ALLOW_ALL"
    max_instance_count = 1
    available_memory   = "256M"
    timeout_seconds    = 60
    environment_variables = {
      PROJECT_ID        = var.project_id
      REGION            = var.region
      QUEUE_NAME        = google_cloud_tasks_queue.delayedtasks_queue.name
      SCHEDULE_MODE     = var.schedule_mode
      SCHEDULE_LOCATION = var.schedule_location
    }
  }
}

resource "google_cloud_run_v2_service" "agent" {
  provider = google-beta
  project  = data.google_project.current.project_id
  name     = "agent-function"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  invoker_iam_disabled = true


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
          bucket     = google_storage_bucket.source_bucket.name
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

resource "google_cloud_run_v2_service" "mcp_server" {
  provider = google-beta
  project  = data.google_project.current.project_id
  name     = "mcp-function-deployer-iac"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  invoker_iam_disabled = true


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
          bucket     = google_storage_bucket.source_bucket.name
          object     = google_storage_bucket_object.mcp_object.name
          generation = google_storage_bucket_object.mcp_object.generation
        }
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "GCS_BUCKET"
        value = "faas-scheduling-us-east1" #TODO make variable
      }

      env {
        name = "MCP_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.mcp_secret.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [google_storage_bucket_object.mcp_object]
}

resource "google_cloud_tasks_queue" "delayedtasks_queue" {
  name     = "delayedtasks-${formatdate("YYYYMMDDHHMMss", timestamp())}"
  location = var.region
}

resource "google_storage_bucket" "scheduler_bucket" {
  name                        = "iosl-scheduler-bucket"
  location                    = var.region
  uniform_bucket_level_access = true
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

data "archive_file" "mcp_archiver" {
  type        = "tar.gz"
  output_path = "out/mcp_server.tar.gz"
  source_dir  = "src/mcp_server/"
  excludes    = ["__pycache__/*", "*.md", "*.sh"]
}

output "dispatcher_url" {
  value = google_cloudfunctions2_function.dispatcher_function.url
  description = "Dispatcher URL"
}

output "agent_url" {
  value = google_cloud_run_v2_service.agent.uri
  description = "Agent URL"
}

output "mcp_url" {
  value = google_cloud_run_v2_service.mcp_server.uri
  description = "MCP URL"
}

