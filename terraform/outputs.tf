output "bucket" { value = google_storage_bucket.mlops.name }
output "endpoint" { value = google_vertex_ai_endpoint.fraud.name }
output "endpoint_id" { value = element(split("/", google_vertex_ai_endpoint.fraud.name), length(split("/", google_vertex_ai_endpoint.fraud.name)) - 1) }
output "endpoint_display_name" { value = var.endpoint_display_name }
output "log_table" { value = local.log_table }
output "pipeline_service_account" { value = google_service_account.pipeline.email }
output "drift_service_account" { value = google_service_account.drift.email }
output "ci_service_account" { value = google_service_account.ci.email }
output "workload_identity_provider" { value = google_iam_workload_identity_pool_provider.github.name }
output "drift_image" { value = local.drift_image }
output "pipelines_url" { value = "https://console.cloud.google.com/vertex-ai/pipelines/runs?project=${var.project_id}" }
output "registry_url" { value = "https://console.cloud.google.com/vertex-ai/models?project=${var.project_id}" }
output "endpoints_url" { value = "https://console.cloud.google.com/vertex-ai/online-prediction/endpoints?project=${var.project_id}" }
output "dashboard_url" {
  value = "https://console.cloud.google.com/monitoring/dashboards/builder/${element(split("/", google_monitoring_dashboard.mlops.id), length(split("/", google_monitoring_dashboard.mlops.id)) - 1)}?project=${var.project_id}"
}
output "slo_url" {
  value = "https://console.cloud.google.com/monitoring/services/${data.google_project.this.number}/${google_monitoring_custom_service.fraud.service_id}?project=${var.project_id}"
}
