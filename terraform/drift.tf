# The drift job: Cloud Run job on a 15-minute schedule; the same image is run by drills/drift.sh with AUTO_ROLLBACK.
resource "google_cloud_run_v2_job" "drift" {
  name                = "drift-check"
  location            = var.region
  deletion_protection = false
  template {
    template {
      service_account = google_service_account.drift.email
      max_retries     = 0
      timeout         = "300s"
      containers {
        image = local.drift_image
        env {
          name  = "PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "REGION"
          value = var.region
        }
        env {
          name  = "BUCKET"
          value = google_storage_bucket.mlops.name
        }
        env {
          name  = "LOG_TABLE"
          value = local.log_table
        }
        env {
          name  = "ENDPOINT_DISPLAY_NAME"
          value = var.endpoint_display_name
        }
        env {
          name  = "PSI_THRESHOLD"
          value = tostring(var.psi_threshold)
        }
        env {
          name  = "WINDOW_MINUTES"
          value = "60"
        }
        env {
          name  = "AUTO_ROLLBACK"
          value = "1"
        }
        resources {
          limits = { cpu = "1", memory = "512Mi" }
        }
      }
    }
  }
  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }
  depends_on = [google_project_iam_member.drift_roles]
}

resource "google_cloud_scheduler_job" "drift" {
  name        = "drift-check-every-15m"
  region      = var.region
  schedule    = "*/15 * * * *"
  description = "PSI of the last hour of prediction requests vs the champion's baseline"
  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.drift.name}:run"
    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }
  depends_on = [google_project_service.apis]
}
