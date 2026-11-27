# Prediction SLOs on the endpoint's own metrics, a drift alert on the custom PSI metric, and one dashboard.
resource "google_monitoring_notification_channel" "email" {
  display_name = "fraud-detector alerts"
  type         = "email"
  labels       = { email_address = var.alert_email }
  depends_on   = [google_project_service.apis]
}

resource "google_monitoring_custom_service" "fraud" {
  service_id   = "fraud-detector-online"
  display_name = "fraud-detector online prediction"
  depends_on   = [google_project_service.apis]
}

locals {
  ep_filter   = "resource.type=\"aiplatform.googleapis.com/Endpoint\" resource.labels.location=\"${var.region}\""
  pred_count  = "metric.type=\"aiplatform.googleapis.com/prediction/online/prediction_count\" ${local.ep_filter}"
  err_count   = "metric.type=\"aiplatform.googleapis.com/prediction/online/error_count\" ${local.ep_filter}"
  latency_dis = "metric.type=\"aiplatform.googleapis.com/prediction/online/prediction_latencies\" ${local.ep_filter}"
}

resource "google_monitoring_slo" "availability" {
  service             = google_monitoring_custom_service.fraud.service_id
  slo_id              = "prediction-availability"
  display_name        = "99.5% of predictions succeed"
  goal                = 0.995
  rolling_period_days = 28
  request_based_sli {
    good_total_ratio {
      good_service_filter = local.pred_count
      bad_service_filter  = local.err_count
    }
  }
}

resource "google_monitoring_slo" "latency" {
  service             = google_monitoring_custom_service.fraud.service_id
  slo_id              = "prediction-latency-p95-300ms"
  display_name        = "95% of predictions under 300 ms (model latency)"
  goal                = 0.95
  rolling_period_days = 28
  request_based_sli {
    distribution_cut {
      distribution_filter = local.latency_dis
      range {
        min = 0
        max = 300
      }
    }
  }
}

resource "google_monitoring_alert_policy" "burn" {
  for_each = {
    availability = google_monitoring_slo.availability.name
    latency      = google_monitoring_slo.latency.name
  }
  display_name = "fraud-detector ${each.key} SLO burn rate"
  combiner     = "OR"
  conditions {
    display_name = "fast burn (1h > 14.4x)"
    condition_threshold {
      filter          = "select_slo_burn_rate(\"${each.value}\", \"3600s\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 14.4
      duration        = "0s"
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
}

# Drift: the custom metric the job writes. Above the threshold the job also rolls traffic back; this tells a human.
resource "google_monitoring_alert_policy" "drift" {
  display_name = "fraud-detector feature drift (PSI)"
  combiner     = "OR"
  conditions {
    display_name = "psi_max above ${var.psi_threshold}"
    condition_threshold {
      filter          = "metric.type=\"custom.googleapis.com/ml/psi_max\" resource.type=\"global\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.psi_threshold
      duration        = "0s"
      aggregations {
        alignment_period   = "900s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }
  notification_channels = [google_monitoring_notification_channel.email.id]
  depends_on            = [google_monitoring_metric_descriptor.psi_max]
  documentation {
    content   = "Live prediction requests have drifted from the champion's training baseline (PSI > ${var.psi_threshold}). The drift job rolls traffic back to the champion if a challenger is serving. Inspect: `drills/drift.sh report`, the request log in BigQuery `mlops.request_response_logging`, and retrain."
    mime_type = "text/markdown"
  }
}

locals {
  tiles = [
    { title = "Predictions/s by deployed model", filter = local.pred_count, group = ["metric.label.deployed_model_id"], align = "ALIGN_RATE", red = "REDUCE_SUM", type = "STACKED_AREA" },
    { title = "Prediction errors/s", filter = local.err_count, group = ["metric.label.deployed_model_id"], align = "ALIGN_RATE", red = "REDUCE_SUM", type = "STACKED_AREA" },
    { title = "p95 model latency (ms)", filter = local.latency_dis, group = ["metric.label.deployed_model_id"], align = "ALIGN_PERCENTILE_95", red = "REDUCE_MEAN", type = "LINE" },
    { title = "Replicas by deployed model", filter = "metric.type=\"aiplatform.googleapis.com/prediction/online/replicas\" ${local.ep_filter}", group = ["metric.label.deployed_model_id"], align = "ALIGN_MAX", red = "REDUCE_SUM", type = "LINE" },
    { title = "Feature drift: PSI max (last hour of requests)", filter = "metric.type=\"custom.googleapis.com/ml/psi_max\" resource.type=\"global\"", group = [], align = "ALIGN_MAX", red = "REDUCE_MAX", type = "LINE" },
    { title = "PSI by feature (top)", filter = "metric.type=\"custom.googleapis.com/ml/psi_feature\" resource.type=\"global\"", group = ["metric.label.feature"], align = "ALIGN_MAX", red = "REDUCE_MAX", type = "LINE" },
  ]
}

resource "google_monitoring_dashboard" "mlops" {
  dashboard_json = jsonencode({
    displayName = "fraud-detector MLOps"
    mosaicLayout = {
      columns = 12
      tiles = [
        for i, t in local.tiles : {
          xPos = (i % 3) * 4, yPos = floor(i / 3) * 4, width = 4, height = 4
          widget = {
            title = t.title
            xyChart = { dataSets = [{ plotType = t.type, timeSeriesQuery = { timeSeriesFilter = { filter = t.filter, aggregation = { alignmentPeriod = "60s", perSeriesAligner = t.align, crossSeriesReducer = t.red, groupByFields = t.group } } } }] }
          }
        }
      ]
    }
  })
}
