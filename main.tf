terraform {
  required_providers {
    google = {
      source  = "opentofu/google"
      version = "7.15.0"
    }
    google-beta = {
      source  = "opentofu/google-beta"
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

provider "google-beta" {
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

resource "google_project_iam_member" "compute_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_service_account" "mcp_deployer" {
  account_id   = "mcp-deployer-iac"
  display_name = "MCP Function Deployer"
  project      = var.project_id
}

resource "google_project_iam_member" "mcp_sa_roles" {
  for_each = toset([
    "roles/run.admin",                    # Deploy and manage Cloud Run services
    "roles/storage.objectAdmin",          # Upload source to GCS
    "roles/iam.serviceAccountUser",       # Act as service account
    "roles/cloudbuild.builds.editor",     # Trigger Cloud Build
    "roles/artifactregistry.admin",       # Create repos and push images
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

resource "google_secret_manager_secret_iam_member" "mcp_accessor_agent" {
  secret_id = google_secret_manager_secret.mcp_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

  resource "google_storage_bucket" "source_bucket" {
  name = "${var.project_id}-source-bucket"
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

resource "google_storage_bucket" "scheduler_bucket" {
  name                        = var.gcs_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
}

# Artifact Registry repositories for function container images
# Using multi-regional repositories for better availability
resource "google_artifact_registry_repository" "function_images_us" {
  location      = "us"
  repository_id = "function-images"
  description   = "Container images for dynamically deployed Cloud Run functions (US)"
  format        = "DOCKER"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
}

resource "google_artifact_registry_repository" "function_images_europe" {
  location      = "europe"
  repository_id = "function-images"
  description   = "Container images for dynamically deployed Cloud Run functions (Europe)"
  format        = "DOCKER"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
}

resource "google_artifact_registry_repository" "function_images_asia" {
  location      = "asia"
  repository_id = "function-images"
  description   = "Container images for dynamically deployed Cloud Run functions (Asia)"
  format        = "DOCKER"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
}

# Grant Cloud Build service account permission to deploy to Cloud Run
resource "google_project_iam_member" "cloudbuild_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${data.google_project.current.number}@cloudbuild.gserviceaccount.com"
}

# Grant Cloud Build service account permission to act as compute service account
resource "google_project_iam_member" "cloudbuild_sa_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${data.google_project.current.number}@cloudbuild.gserviceaccount.com"
}

resource "google_storage_bucket_object" "function_metadata" {
  name = "function_metadata.json"
  bucket = google_storage_bucket.scheduler_bucket.name
  source = var.function_metadata_path
}

resource "google_storage_bucket_object" "static_config" {
  name = "static_config.json"
  bucket = google_storage_bucket.scheduler_bucket.name
  source = var.static_config_path
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
    ingress_settings   = "ALLOW_ALL"
    max_instance_count = 1
    available_memory   = "256M"
    timeout_seconds    = 60
    environment_variables = {
      PROJECT_ID        = var.project_id
      REGION            = var.region
      QUEUE_NAME        = google_cloud_tasks_queue.delayedtasks_queue.name
      SCHEDULE_MODE     = var.schedule_mode
      SCHEDULE_LOCATION = var.schedule_location
      GCS_BUCKET_NAME   = google_storage_bucket.scheduler_bucket.name
    }
  }
}

resource "google_cloudfunctions2_function_iam_member" "public_invoker" {
  project        = google_cloudfunctions2_function.dispatcher_function.project
  location       = google_cloudfunctions2_function.dispatcher_function.location
  cloud_function = google_cloudfunctions2_function.dispatcher_function.name
  role           = "roles/cloudfunctions.invoker"
  member         = "allUsers"
}

resource "google_cloud_run_service_iam_member" "public_invoker_run" {
  location = google_cloudfunctions2_function.dispatcher_function.location
  service  = google_cloudfunctions2_function.dispatcher_function.name
  role     = "roles/run.invoker"
  member   = "allUsers"
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
        "python -m pip install -r requirements.txt && python -m gunicorn --bind :8080 --workers 1 --threads 8 --timeout 300 main:app"
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
        value = google_storage_bucket.scheduler_bucket.name
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

      env {
        name = "MCP_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.mcp_secret.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "MCP_SERVER_URL"
        value = google_cloud_run_v2_service.mcp_server.uri
      }
    }
  }

  depends_on = [google_storage_bucket_object.agent_object, google_cloud_run_v2_service.mcp_server]
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
        "python -m pip install -r requirements.txt && python -m gunicorn --bind :8080 --workers 1 --threads 8 --timeout 300 server:app"
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
        value = google_storage_bucket.scheduler_bucket.name
      }

      env {
        name  = "ARTIFACT_REPO"
        value = "function-images"
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

  depends_on = [
    google_storage_bucket_object.mcp_object,
    google_artifact_registry_repository.function_images_us,
    google_artifact_registry_repository.function_images_europe,
    google_artifact_registry_repository.function_images_asia
  ]
}

resource "google_cloud_tasks_queue" "delayedtasks_queue" {
  name     = "delayedtasks-${formatdate("YYYYMMDDHHMMss", timestamp())}"
  location = var.region
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
  value       = google_cloudfunctions2_function.dispatcher_function.url
  description = "Dispatcher URL"
}

output "agent_url" {
  value       = google_cloud_run_v2_service.agent.uri
  description = "Agent URL"
}

output "mcp_url" {
  value       = google_cloud_run_v2_service.mcp_server.uri
  description = "MCP URL"
}
