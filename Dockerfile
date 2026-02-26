# syntax=docker/dockerfile:1
#
# Beacon Node - Custom Synapse v1.147.1 with Ed25519 auth + multi-worker support
# Based on official Synapse Dockerfile-workers pattern but simplified
#

ARG SYNAPSE_VERSION=v1.147.1

# Start from official Synapse v1.147.1
FROM ghcr.io/element-hq/synapse:${SYNAPSE_VERSION}
LABEL maintainer="ECADInfra Team <ops@ecadlabs.com> (Updated for Element HQ Synapse)"

# Install all dependencies in one layer:
# - libsodium-dev, gcc: for crypto auth provider
# - nginx-light: load balancer for workers
# - redis-server: coordination for workers
# - supervisor: process management for workers
# - netcat-openbsd: for wait-for.sh script
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libsodium-dev \
        gcc \
        nginx-light \
        redis-server \
        supervisor \
        netcat-openbsd && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Configure nginx (remove default site, log to stdout/stderr)
RUN rm -f /etc/nginx/sites-enabled/default && \
    ln -sf /dev/stdout /var/log/nginx/access.log && \
    ln -sf /dev/stderr /var/log/nginx/error.log

# Create supervisor config directory and symlink binaries to expected locations
RUN mkdir -p /etc/supervisor/conf.d && \
    ln -s /usr/bin/supervisord /usr/local/bin/supervisord && \
    ln -s /usr/bin/supervisorctl /usr/local/bin/supervisorctl && \
    ln -s /usr/bin/redis-server /usr/local/bin/redis-server

# Install Python packages
RUN pip install --no-cache-dir psycopg2 pysodium

# Create keys directory
RUN mkdir -p /keys

# Copy custom modules (using Python 3.13 path for Element HQ image)
COPY crypto_auth_provider.py /usr/local/lib/python3.13/site-packages/
COPY beacon_info_module.py /usr/local/lib/python3.13/site-packages/
COPY beacon_monitor_module.py /usr/local/lib/python3.13/site-packages/

# Copy configuration files
COPY homeserver.yaml /config/
COPY synapse.log.config /config/
COPY shared_config.yaml /config/

# Copy systemd service files (if used)
COPY synapse_master.service /etc/systemd/system/
COPY synapse_worker@.service /etc/systemd/system/
COPY matrix_synapse.target /etc/systemd/system/

# Copy workers configuration (legacy - kept for backward compatibility)
COPY workers /config/workers

# Copy worker orchestration files from official Synapse
COPY conf-workers /conf/
COPY configure_workers_and_start.py /usr/local/bin/
COPY prefix-log /usr/local/bin/

# Copy beacon-specific entrypoint wrapper
COPY beacon_entrypoint.py /usr/local/bin/

# Copy utility scripts
COPY wait-for.sh /usr/local/bin/

# Increase max event size (1MB instead of 64KB)
RUN sed -i 's/65536/1048576/' /usr/local/lib/python3.13/site-packages/synapse/api/constants.py

# Expose ports (following official Synapse Docker conventions):
#   8008: HTTP (client and federation - direct)
#   8080: HTTP (nginx load balancer - when workers enabled)
#   19090: Metrics for main process (when SYNAPSE_ENABLE_METRICS=1)
#   19091+: Metrics for workers (when SYNAPSE_ENABLE_METRICS=1 and SYNAPSE_WORKERS=true)
#   9469: Prometheus HTTP service discovery + metrics proxy (when workers enabled)
# See: https://github.com/element-hq/synapse/tree/develop/docker
EXPOSE 8008 8080 19090 9469

# Entrypoint supports backward compatibility:
#   Default: Uses /config/homeserver.yaml with variable substitution
#   Custom config: -c /path/to/config.yaml
#   Skip templating: --skip-templating
#   Worker mode: SYNAPSE_WORKERS=true (auto-configured with optimized worker types)
ENTRYPOINT ["/usr/local/bin/beacon_entrypoint.py"]

# Healthcheck for both single-process and worker modes
# In worker mode, this will be replaced by configure_workers_and_start.py
HEALTHCHECK --start-period=5s --interval=15s --timeout=5s \
    CMD curl -fSs http://localhost:8008/health || exit 1
