FROM ghcr.io/element-hq/synapse:v1.148.0
LABEL maintainer="ECAD Infra <ops@ecadinfra.com>"
LABEL org.opencontainers.image.description="Synapse homeserver with Ed25519 Beacon auth for Tezos dApp/wallet relay"
LABEL org.opencontainers.image.source="https://github.com/ECADInfra/beacon-synapse"
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"

# netcat-openbsd: TCP readiness checks (wait-for.sh)
# gettext-base: envsubst for config templating
# nginx-light, redis-server, supervisor: worker mode orchestration
# No gcc/libsodium-dev needed: PyNaCl (with bundled libsodium) and psycopg2
# are already installed in the base Synapse image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    netcat-openbsd gettext-base \
    nginx-light redis-server supervisor \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Configure nginx for worker mode (remove default site, log to stdout)
RUN rm -f /etc/nginx/sites-enabled/default && \
    ln -sf /dev/stdout /var/log/nginx/access.log && \
    ln -sf /dev/stderr /var/log/nginx/error.log

# Symlink binaries to expected locations (configure_workers_and_start.py expects these)
RUN mkdir -p /etc/supervisor/conf.d && \
    ln -s /usr/bin/supervisord /usr/local/bin/supervisord && \
    ln -s /usr/bin/supervisorctl /usr/local/bin/supervisorctl && \
    ln -s /usr/bin/redis-server /usr/local/bin/redis-server

# Create keys and data directories
RUN mkdir -p /keys /data

# Increase max event size (1MB instead of default 64KB).
# Beacon messages can exceed the default Matrix PDU size limit.
# Runs before COPY so code changes don't invalidate this layer.
RUN sed -i 's/^MAX_PDU_SIZE = 65536$/MAX_PDU_SIZE = 1048576/' /usr/local/lib/python3.13/site-packages/synapse/api/constants.py && \
    grep -q '^MAX_PDU_SIZE = 1048576$' /usr/local/lib/python3.13/site-packages/synapse/api/constants.py || \
    (echo "FATAL: PDU size patch failed - 'MAX_PDU_SIZE = 65536' not found in constants.py. Upstream may have changed." >&2 && exit 1)

# Copy custom modules (using Python 3.13 path for Element HQ image)
COPY crypto_auth_provider.py /usr/local/lib/python3.13/site-packages/
COPY beacon_info_module.py /usr/local/lib/python3.13/site-packages/
COPY beacon_monitor_module.py /usr/local/lib/python3.13/site-packages/

# Copy configuration templates (envsubst at runtime) and static configs
COPY homeserver.yaml /config/homeserver.yaml.template
COPY synapse.log.config /config/

# Copy worker orchestration configs (Jinja2 templates for configure_workers_and_start.py)
COPY conf-workers /conf/
COPY configure_workers_and_start.py /usr/local/bin/
COPY prefix-log /usr/local/bin/

COPY wait-for.sh /usr/local/bin/
COPY synctl_entrypoint.sh /usr/local/bin/

# Expose ports:
#   8008: HTTP (client + federation; direct in single mode, nginx in worker mode)
#   8080: Main process HTTP (worker mode only, internal)
#   9469: Prometheus service discovery (worker mode, nginx)
#   19090: Metrics for main process (when SYNAPSE_ENABLE_METRICS=1)
EXPOSE 8008 8080 9469 19090

ENTRYPOINT ["/usr/local/bin/synctl_entrypoint.sh"]
