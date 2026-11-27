terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.30, < 8"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 6.30, < 8"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "github_repo" { type = string }
variable "alert_email" { type = string }
variable "endpoint_display_name" {
  type    = string
  default = "fraud-endpoint"
}
variable "drift_image_tag" {
  type    = string
  default = "v1"
}
variable "psi_threshold" {
  type    = number
  default = 0.25
}

resource "google_project_service" "apis" {
  for_each = toset([
    "aiplatform.googleapis.com", "bigquery.googleapis.com", "storage.googleapis.com", "run.googleapis.com",
    "artifactregistry.googleapis.com", "cloudbuild.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com",
    "sts.googleapis.com", "monitoring.googleapis.com", "logging.googleapis.com", "cloudscheduler.googleapis.com", "pubsub.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

data "google_project" "this" {}

locals {
  bucket      = "${var.project_id}-mlops"
  drift_image = "${var.region}-docker.pkg.dev/${var.project_id}/apps/drift-check:${var.drift_image_tag}"
  log_table   = "${var.project_id}.mlops.request_response_logging"
}

# ---------------------------------------------------------------- storage: pipeline root, artifacts, champion/baseline
resource "google_storage_bucket" "mlops" {
  name                        = local.bucket
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  lifecycle_rule {
    condition { age = 14 }
    action { type = "Delete" }
  }
  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------- BigQuery: the endpoint's request/response log lands here
resource "google_bigquery_dataset" "mlops" {
  dataset_id                  = "mlops"
  location                    = "US"
  default_table_expiration_ms = 7 * 24 * 3600 * 1000
  delete_contents_on_destroy  = true
  depends_on                  = [google_project_service.apis]
}

# ---------------------------------------------------------------- Artifact Registry (drift job image)
resource "google_artifact_registry_repository" "apps" {
  location      = var.region
  repository_id = "apps"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# ---------------------------------------------------------------- Vertex endpoint: one for all versions, traffic split is the canary
resource "google_vertex_ai_endpoint" "fraud" {
  name         = "fraud-endpoint"
  display_name = var.endpoint_display_name
  location     = var.region
  description  = "fraud-detector: champion + challenger behind one endpoint; request/response logging to BigQuery"
  labels       = { app = "fraud-detector" }
  predict_request_response_logging_config {
    enabled       = true
    sampling_rate = 1.0
    bigquery_destination {
      output_uri = "bq://${var.project_id}.mlops.request_response_logging"
    }
  }
  depends_on = [google_project_service.apis, google_bigquery_dataset.mlops]
}
