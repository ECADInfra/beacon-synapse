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

# Determine mode: single-process or worker
SYNAPSE_WORKERS="${SYNAPSE_WORKERS:-false}"

# Configure listener port and bind address based on mode
# Single-process: Synapse listens directly on 0.0.0.0:8008
# Worker mode: main process on 127.0.0.1:8080, nginx fronts on 0.0.0.0:8008
if [ "$SYNAPSE_WORKERS" = "true" ]; then
  export SYNAPSE_HTTP_PORT="8080"
  export SYNAPSE_HTTP_BIND="127.0.0.1"
else
  export SYNAPSE_HTTP_PORT="8008"
  export SYNAPSE_HTTP_BIND="0.0.0.0"
fi

# Configure metrics bind address based on SYNAPSE_ENABLE_METRICS (official Synapse convention)
if [ "${SYNAPSE_ENABLE_METRICS:-0}" = "1" ]; then
  export METRICS_BIND_ADDRESS="0.0.0.0"
  echo "Metrics enabled on 0.0.0.0:19090 (main), 19091+ (workers)"
else
  export METRICS_BIND_ADDRESS="127.0.0.1"
  echo "Metrics disabled (set SYNAPSE_ENABLE_METRICS=1 to enable)"
fi

# Set defaults for optional configuration variables
export PUBLIC_BASEURL="${PUBLIC_BASEURL:-https://$SERVER_NAME}"
export DB_CP_MIN="${DB_CP_MIN:-20}"
export DB_CP_MAX="${DB_CP_MAX:-80}"

# Perform variable substitution unless --skip-templating is set
ENVSUBST_VARS='${SERVER_NAME} ${DB_HOST} ${DB_USER} ${DB_PASS} ${DB_NAME} ${REGISTRATION_SHARED_SECRET} ${METRICS_BIND_ADDRESS} ${PUBLIC_BASEURL} ${DB_CP_MIN} ${DB_CP_MAX} ${SYNAPSE_HTTP_PORT} ${SYNAPSE_HTTP_BIND}'

if [ "$SKIP_TEMPLATING" = "false" ]; then
  echo "Performing template variable substitution..."
  envsubst "$ENVSUBST_VARS" < "${CONFIG_FILE}.template" > "$CONFIG_FILE"

  # Implement SERVE_WELLKNOWN functionality for Cloudflare-proxied servers
  if [ "${SERVE_WELLKNOWN:-false}" = "true" ]; then
    echo "Enabling .well-known/matrix/server endpoint (SERVE_WELLKNOWN=true)"
    if ! grep -q "^serve_server_wellknown:" "$CONFIG_FILE"; then
      echo "" >> "$CONFIG_FILE"
      echo "# Auto-configured by entrypoint based on SERVE_WELLKNOWN env var" >> "$CONFIG_FILE"
      echo "serve_server_wellknown: true" >> "$CONFIG_FILE"
    fi
  fi
else
  echo "Skipping template variable substitution (--skip-templating)"
  [ ! -f "$CONFIG_FILE" ] && cp "${CONFIG_FILE}.template" "$CONFIG_FILE"
fi

(umask 077; echo "${SIGNING_KEY}" > /config/signing.key)
/usr/local/bin/wait-for.sh -t 30 "$DB_HOST:5432"

# Start Synapse
if [ "$SYNAPSE_WORKERS" = "true" ]; then
  echo "Starting Synapse in worker mode (supervisord + nginx + redis)"
  export SYNAPSE_SERVER_NAME="$SERVER_NAME"
  export SYNAPSE_REPORT_STATS="no"
  export SYNAPSE_CONFIG_PATH="$CONFIG_FILE"
  # Default worker types if not explicitly set
  export SYNAPSE_WORKER_TYPES="${SYNAPSE_WORKER_TYPES:-synchrotron:2,event_persister:1,federation_inbound:1}"
  echo "Worker types: $SYNAPSE_WORKER_TYPES"
  exec /usr/local/bin/configure_workers_and_start.py
else
  echo "Starting Synapse in single-process mode with config: $CONFIG_FILE"
  exec python -m synapse.app.homeserver --config-path "$CONFIG_FILE"
fi
