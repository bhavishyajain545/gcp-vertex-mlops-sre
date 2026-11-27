# The drift job's custom metrics, declared up front so the alert policy can reference them before the first write.
resource "google_monitoring_metric_descriptor" "psi_max" {
  description  = "Max Population Stability Index over all features: last hour of prediction requests vs the champion's training baseline"
  display_name = "ml/psi_max"
  type         = "custom.googleapis.com/ml/psi_max"
  metric_kind  = "GAUGE"
  value_type   = "DOUBLE"
  unit         = "1"
  labels {
    key         = "endpoint"
    value_type  = "STRING"
    description = "Vertex endpoint display name"
  }
  depends_on = [google_project_service.apis]
}

resource "google_monitoring_metric_descriptor" "psi_feature" {
  description  = "Population Stability Index per feature"
  display_name = "ml/psi_feature"
  type         = "custom.googleapis.com/ml/psi_feature"
  metric_kind  = "GAUGE"
  value_type   = "DOUBLE"
  unit         = "1"
  labels {
    key        = "endpoint"
    value_type = "STRING"
  }
  labels {
    key        = "feature"
    value_type = "STRING"
  }
  depends_on = [google_project_service.apis]
}

resource "google_monitoring_metric_descriptor" "logged_requests" {
  description  = "Prediction requests found in the request/response log for the drift window"
  display_name = "ml/logged_requests"
  type         = "custom.googleapis.com/ml/logged_requests"
  metric_kind  = "GAUGE"
  value_type   = "DOUBLE"
  unit         = "1"
  labels {
    key        = "endpoint"
    value_type = "STRING"
  }
  depends_on = [google_project_service.apis]
}
