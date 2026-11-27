#!/usr/bin/env bash
# List everything that can bill in a project. Every section should be empty after ./teardown.sh.
set -uo pipefail
PROJECTS=("$@")
[ ${#PROJECTS[@]} -eq 0 ] && PROJECTS=("$(grep -E '^project_id' "$(dirname "$0")/terraform/terraform.tfvars" | cut -d'"' -f2)")
for P in "${PROJECTS[@]}"; do
  echo "===== $P"
  g() { gcloud "$@" --project "$P" --quiet 2>/dev/null; }
  echo "clusters:";          g container clusters list --format='value(name,location,status)'
  echo "instances:";         g compute instances list --format='value(name,zone,status)'
  echo "disks:";             g compute disks list --format='value(name,sizeGb)'
  echo "forwarding rules:";  g compute forwarding-rules list --format='value(name)'
  echo "backend services:";  g compute backend-services list --format='value(name)'
  echo "addresses:";         g compute addresses list --format='value(name,address,status)'
  echo "NEGs:";              g compute network-endpoint-groups list --format='value(name)'
  echo "routers/NAT:";       g compute routers list --format='value(name,region)'
  echo "networks:";          g compute networks list --format='value(name)'
  echo "artifact repos:";    g artifacts repositories list --format='value(name)'
  echo "delivery pipelines:"; g deploy delivery-pipelines list --region=us-central1 --format='value(name)'
  echo "firestore:";         g firestore databases list --format='value(name)'
  echo "bigquery datasets:"; bq ls --project_id="$P" 2>/dev/null | tail -n +3
  echo "buckets:";           gcloud storage ls --project "$P" 2>/dev/null
  echo "cloud run:";         g run services list --format='value(metadata.name)'; g run jobs list --format='value(metadata.name)'
  echo "alert policies:";    g alpha monitoring policies list --format='value(displayName)' 2>/dev/null
done
