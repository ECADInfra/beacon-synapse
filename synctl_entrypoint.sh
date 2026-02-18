#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
# © ECAD Infra Inc.
set -eu

# Parse command-line arguments
CONFIG_FILE="/config/homeserver.yaml"
SKIP_TEMPLATING=false

while [ $# -gt 0 ]; do
  case "$1" in
    -c|--config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    --skip-templating)
      SKIP_TEMPLATING=true
      shift
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [-c|--config <path>] [--skip-templating]"
      exit 1
      ;;
  esac
done

echo "Using config file: $CONFIG_FILE"

# Configure metrics bind address based on SYNAPSE_ENABLE_METRICS (official Synapse convention)
# SYNAPSE_ENABLE_METRICS=1 enables metrics listener (port 19090 for main, 19091+ for workers)
# Defaults to 0 (disabled) to match official Synapse Docker behavior
if [ "${SYNAPSE_ENABLE_METRICS:-0}" = "1" ]; then
  export METRICS_BIND_ADDRESS="0.0.0.0"
  echo "Metrics enabled on 0.0.0.0:19090 (main), 19091+ (workers)"
else
  export METRICS_BIND_ADDRESS="127.0.0.1"
  echo "Metrics disabled (set SYNAPSE_ENABLE_METRICS=1 to enable)"
fi

# Perform variable substitution unless --skip-templating is set
ENVSUBST_VARS='${SERVER_NAME} ${DB_HOST} ${DB_USER} ${DB_PASS} ${DB_NAME} ${REGISTRATION_SHARED_SECRET} ${METRICS_BIND_ADDRESS}'

if [ "$SKIP_TEMPLATING" = "false" ]; then
  echo "Performing template variable substitution..."
  envsubst "$ENVSUBST_VARS" < "${CONFIG_FILE}.template" > "$CONFIG_FILE"

  if [ -f /config/shared_config.yaml.template ]; then
    envsubst '${SERVER_NAME}' < /config/shared_config.yaml.template > /config/shared_config.yaml
  fi

  # Process worker config templates
  if [ -d /config/workers.template ]; then
    mkdir -p /config/workers
    for tmpl in /config/workers.template/*.yaml; do
      [ -f "$tmpl" ] || continue
      envsubst '${METRICS_BIND_ADDRESS}' < "$tmpl" > "/config/workers/$(basename "$tmpl")"
    done
  fi
else
  echo "Skipping template variable substitution (--skip-templating)"
  # When skipping, ensure config files exist (copy templates as-is)
  [ ! -f "$CONFIG_FILE" ] && cp "${CONFIG_FILE}.template" "$CONFIG_FILE"
  [ ! -f /config/shared_config.yaml ] && [ -f /config/shared_config.yaml.template ] && \
    cp /config/shared_config.yaml.template /config/shared_config.yaml
  [ ! -d /config/workers ] && [ -d /config/workers.template ] && \
    cp -r /config/workers.template /config/workers
fi

(umask 077; echo "${SIGNING_KEY}" > /config/signing.key)
/usr/local/bin/wait-for.sh -t 30 "$DB_HOST:5432"

# Start Synapse based on SYNAPSE_WORKERS mode (default: single-process)
SYNAPSE_WORKERS="${SYNAPSE_WORKERS:-false}"

if [ "$SYNAPSE_WORKERS" = "true" ]; then
  echo "Starting Synapse in multi-worker mode with config: $CONFIG_FILE"
  echo "Note: Multi-worker mode requires a load balancer for createRoom routing"
  synctl start "$CONFIG_FILE" -w /config/workers/main_process.yaml
  synctl start "$CONFIG_FILE" -w /config/workers/worker1.yaml
  synctl start "$CONFIG_FILE" -w /config/workers/worker2.yaml
  synctl start "$CONFIG_FILE" -w /config/workers/worker3.yaml
  synctl start "$CONFIG_FILE" -w /config/workers/worker4.yaml --no-daemonize
else
  echo "Starting Synapse in single-process mode with config: $CONFIG_FILE"
  synctl start "$CONFIG_FILE" --no-daemonize
fi
