#!/bin/sh

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
  METRICS_BIND_ADDRESS="0.0.0.0"
  echo "Metrics enabled on 0.0.0.0:19090 (main), 19091+ (workers)"
else
  METRICS_BIND_ADDRESS="127.0.0.1"
  echo "Metrics disabled (set SYNAPSE_ENABLE_METRICS=1 to enable)"
fi

# Perform variable substitution unless --skip-templating is set
if [ "$SKIP_TEMPLATING" = "false" ]; then
  echo "Performing template variable substitution..."
  sed -i "s/{{SERVER_NAME}}/$SERVER_NAME/g" "$CONFIG_FILE"
  sed -i "s/{{DB_HOST}}/$DB_HOST/g" "$CONFIG_FILE"
  sed -i "s/{{DB_USER}}/$DB_USER/g" "$CONFIG_FILE"
  sed -i "s/{{DB_PASS}}/$DB_PASS/g" "$CONFIG_FILE"
  sed -i "s/{{DB_NAME}}/$DB_NAME/g" "$CONFIG_FILE"
  sed -i "s/{{METRICS_BIND_ADDRESS}}/$METRICS_BIND_ADDRESS/g" "$CONFIG_FILE"

  # Also process shared_config.yaml if it exists
  if [ -f /config/shared_config.yaml ]; then
    sed -i "s/{{SERVER_NAME}}/$SERVER_NAME/g" /config/shared_config.yaml
  fi
else
  echo "Skipping template variable substitution (--skip-templating)"
fi

echo "${SIGNING_KEY}" > /config/signing.key
/usr/local/bin/wait-for.sh "$DB_HOST:5432"

# Start Synapse based on SYNAPSE_WORKERS mode (default: single-process)
SYNAPSE_WORKERS="${SYNAPSE_WORKERS:-false}"

if [ "$SYNAPSE_WORKERS" = "true" ]; then
  echo "Starting Synapse in multi-worker mode with config: $CONFIG_FILE"
  echo "Note: Multi-worker mode requires a load balancer for createRoom routing"
  # Multi-worker mode - each worker exposes metrics on 9001, 9002, 9003, 9004
  # Main process exposes metrics on 9000
  synctl start "$CONFIG_FILE" -w /config/workers/main_process.yaml
  synctl start "$CONFIG_FILE" -w /config/workers/worker1.yaml
  synctl start "$CONFIG_FILE" -w /config/workers/worker2.yaml
  synctl start "$CONFIG_FILE" -w /config/workers/worker3.yaml
  synctl start "$CONFIG_FILE" -w /config/workers/worker4.yaml --no-daemonize
else
  echo "Starting Synapse in single-process mode with config: $CONFIG_FILE"
  synctl start "$CONFIG_FILE" --no-daemonize
fi

# Alternative: systemd-based worker management (commented out)
# systemctl daemon-reload
# systemctl start matrix-synapse.service
# systemctl enable matrix-synapse-worker@worker1.service
# systemctl enable matrix-synapse-worker@worker2.service
# systemctl start matrix-synapse.target.service
