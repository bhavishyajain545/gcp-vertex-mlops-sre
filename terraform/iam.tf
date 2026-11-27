# Three identities, least privilege each:
#   sa-pipeline  runs the Vertex pipeline components (BigQuery read, GCS, Model Registry, endpoint deploy)
#   sa-drift     the drift job (BigQuery read, Monitoring write, endpoint traffic update for the rollback)
#   sa-github-ci submits pipeline runs and builds the drift image from GitHub Actions, keyless through WIF

resource "google_service_account" "pipeline" {
  account_id   = "sa-pipeline"
  display_name = "Vertex pipeline runtime"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "pipeline_roles" {
  for_each = toset(["roles/aiplatform.user", "roles/bigquery.jobUser", "roles/bigquery.dataViewer", "roles/logging.logWriter", "roles/monitoring.metricWriter"])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.pipeline.member
}

resource "google_storage_bucket_iam_member" "pipeline_bucket" {
  bucket = google_storage_bucket.mlops.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.pipeline.member
}

# the endpoint deploy needs actAs on the service account Vertex uses for the deployed model (itself here)
resource "google_service_account_iam_member" "pipeline_actas_self" {
  service_account_id = google_service_account.pipeline.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.pipeline.member
}

# Vertex AI's service agent reads model artifacts from the bucket and writes the prediction log to BigQuery
resource "google_project_service_identity" "aiplatform" {
  provider = google-beta
  service  = "aiplatform.googleapis.com"
}

resource "google_storage_bucket_iam_member" "vertex_agent_bucket" {
  bucket = google_storage_bucket.mlops.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_project_service_identity.aiplatform.email}"
}

resource "google_bigquery_dataset_iam_member" "vertex_agent_bq" {
  dataset_id = google_bigquery_dataset.mlops.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_project_service_identity.aiplatform.email}"
}

resource "google_service_account" "drift" {
  account_id   = "sa-drift"
  display_name = "drift check job"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "drift_roles" {
  for_each = toset(["roles/bigquery.jobUser", "roles/bigquery.dataViewer", "roles/monitoring.metricWriter", "roles/aiplatform.user", "roles/logging.logWriter"])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.drift.member
}

resource "google_storage_bucket_iam_member" "drift_bucket" {
  bucket = google_storage_bucket.mlops.name
  role   = "roles/storage.objectViewer"
  member = google_service_account.drift.member
}

# scheduler invokes the drift job
resource "google_service_account" "scheduler" {
  account_id   = "sa-scheduler"
  display_name = "Cloud Scheduler -> drift job"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "scheduler_run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = google_service_account.scheduler.member
}

# ---------------------------------------------------------------- CI (GitHub Actions via WIF)
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == \"${var.github_repo}\""
  oidc { issuer_uri = "https://token.actions.githubusercontent.com" }
}

resource "google_service_account" "ci" {
  account_id   = "sa-github-ci"
  display_name = "GitHub Actions CI (submit pipeline, build drift image)"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "ci_roles" {
  for_each = toset(["roles/aiplatform.user", "roles/artifactregistry.writer", "roles/run.developer"])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.ci.member
}

resource "google_storage_bucket_iam_member" "ci_bucket" {
  bucket = google_storage_bucket.mlops.name
  role   = "roles/storage.objectAdmin"
  member = google_service_account.ci.member
}

# submitting a pipeline run as sa-pipeline needs actAs on it
resource "google_service_account_iam_member" "ci_actas_pipeline" {
  service_account_id = google_service_account.pipeline.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.ci.member
}

resource "google_service_account_iam_member" "ci_actas_drift" {
  service_account_id = google_service_account.drift.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.ci.member
}

resource "google_service_account_iam_member" "ci_wif" {
  service_account_id = google_service_account.ci.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# Cloud Build (deploy.sh builds the drift image once)
resource "google_service_account" "build" {
  account_id   = "sa-build"
  display_name = "Cloud Build"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "build_roles" {
  for_each = toset(["roles/artifactregistry.writer", "roles/logging.logWriter", "roles/storage.objectViewer"])
  project  = var.project_id
  role     = each.value
  member   = google_service_account.build.member
}
