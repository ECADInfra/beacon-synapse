FROM ghcr.io/element-hq/synapse:v1.147.1
LABEL maintainer="ECAD Infra <ops@ecadinfra.com>"
LABEL org.opencontainers.image.description="Synapse homeserver with Ed25519 Beacon auth for Tezos dApp/wallet relay"
LABEL org.opencontainers.image.source="https://github.com/ECADInfra/beacon-synapse"
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"

# Install dependencies for crypto auth provider
RUN apt-get update && apt-get install -y libsodium-dev gcc netcat-openbsd gettext-base && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip install --no-cache-dir psycopg2 pysodium

# Create keys and data directories
RUN mkdir -p /keys /data

# Copy custom modules (using Python 3.13 path for Element HQ image)
COPY crypto_auth_provider.py /usr/local/lib/python3.13/site-packages/
COPY beacon_info_module.py /usr/local/lib/python3.13/site-packages/
COPY beacon_monitor_module.py /usr/local/lib/python3.13/site-packages/

# Copy configuration templates (envsubst at runtime) and static configs
COPY homeserver.yaml /config/homeserver.yaml.template
COPY synapse.log.config /config/
COPY shared_config.yaml /config/shared_config.yaml.template

# Copy worker configuration templates
COPY workers /config/workers.template

# Increase max event size (1MB instead of default 64KB).
# Beacon messages can exceed the default Matrix PDU size limit.
RUN sed -i 's/65536/1048576/' /usr/local/lib/python3.13/site-packages/synapse/api/constants.py && \
    grep -q '1048576' /usr/local/lib/python3.13/site-packages/synapse/api/constants.py || \
    (echo "FATAL: PDU size patch failed - 65536 not found in constants.py. Upstream may have changed." >&2 && exit 1)

COPY wait-for.sh /usr/local/bin/
COPY synctl_entrypoint.sh /usr/local/bin/

# Expose ports:
#   8008: HTTP (client and federation)
#   19090: Metrics for main process (when SYNAPSE_ENABLE_METRICS=1)
#   19091-19094: Metrics for workers 1-4 (when SYNAPSE_ENABLE_METRICS=1 and SYNAPSE_WORKERS=true)
EXPOSE 8008 19090 19091 19092 19093 19094

ENTRYPOINT ["/usr/local/bin/synctl_entrypoint.sh"]
