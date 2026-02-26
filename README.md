# Beacon Node Docker Image

Custom Synapse v1.147.1 image for Tezos Beacon with Ed25519 crypto authentication and multi-worker support.

## Architecture

This image combines:
- **Official Synapse v1.147.1** (Element HQ) - Latest stable with 49+ CVEs patched since v1.98.0
- **Custom crypto auth** - Ed25519 signature authentication (no passwords)
- **Official worker orchestration** - Supervisord + nginx + redis for production scaling
- **Backward compatibility** - Simple `SYNAPSE_WORKERS=true` toggle maps to optimized worker configuration

### Worker Architecture

**Single-process mode** (default):
```
Client → Synapse (port 8008) → PostgreSQL
```

**Multi-worker mode** (`SYNAPSE_WORKERS=true`):
```
Client → nginx (port 8080) → {
  Main process (createRoom, etc.)
  2x Synchrotron workers (client sync)
  1x Event persister (database writes)
  1x Federation inbound (federation messages)
} → PostgreSQL + Redis
```

The multi-worker configuration uses **specialized worker types** for better performance than generic workers:
- **Synchrotron**: Handles heavy client `/sync` operations (2 workers for load distribution)
- **Event persister**: Optimized for writing events to database (1 worker)
- **Federation inbound**: Handles incoming federation messages (1 worker)

This is more efficient than the legacy 4x generic worker approach and follows [official Synapse best practices](https://element-hq.github.io/synapse/latest/workers.html).

## Quick Start

### Default Configuration (with environment variables)

```bash
docker run \
  -e SERVER_NAME=beacon.example.com \
  -e DB_HOST=postgres \
  -e DB_USER=synapse \
  -e DB_PASS=secret \
  -e DB_NAME=synapse \
  -e SIGNING_KEY="ed25519 a_key ..." \
  -e SYNAPSE_ENABLE_METRICS=1 \
  -p 8008:8008 \
  -p 19090:19090 \
  ghcr.io/ecadinfra/deacon/beacon-node:latest
```

### Custom Configuration File

Mount your own `homeserver.yaml` and specify it with `-c`:

```bash
docker run \
  -v /path/to/your/homeserver.yaml:/custom/homeserver.yaml \
  -e SIGNING_KEY="ed25519 a_key ..." \
  -p 8008:8008 \
  ghcr.io/ecadinfra/deacon/beacon-node:latest \
  -c /custom/homeserver.yaml
```

### Skip Template Variable Substitution

If your config is already complete and doesn't use `{{VARIABLE}}` placeholders:

```bash
docker run \
  -v /path/to/complete-config.yaml:/config/homeserver.yaml \
  -p 8008:8008 \
  ghcr.io/ecadinfra/deacon/beacon-node:latest \
  --skip-templating
```

## Environment Variables

### Required (when using default config)

- `SERVER_NAME` - Matrix server name (e.g., `beacon.example.com`)
- `DB_HOST` - PostgreSQL hostname
- `DB_USER` - Database username
- `DB_PASS` - Database password
- `DB_NAME` - Database name
- `SIGNING_KEY` - Synapse signing key (ed25519 format)

### Optional

- `PUBLIC_BASEURL` - Public URL for the server (default: `https://{SERVER_NAME}`)
  - Used for federation and client discovery
  - Example: `https://beacon.example.com`
- `SERVER_REGION` - Geographic region identifier for Beacon discovery (default: `region not set`)
  - Example: `US-WEST`, `EU-CENTRAL`, `ASIA-PACIFIC`
- `REGISTRATION_SHARED_SECRET` - Secret for admin user registration (default: empty, registration disabled)
  - ⚠️ Keep secret! Only for initial admin setup
- `DB_CP_MIN` - Minimum database connection pool size (default: `5`)
- `DB_CP_MAX` - Maximum database connection pool size (default: `10`)
  - Adjust based on expected load and database capacity

### Worker & Monitoring Options

- `SYNAPSE_ENABLE_METRICS` - Enable Prometheus metrics (default: `0`, disabled)
  - `1` - Metrics accessible on port 19090 (main) + 19091-19094 (workers if enabled)
  - `0` - Metrics disabled
  - **Official Synapse convention** - see [Synapse Docker docs](https://github.com/element-hq/synapse/tree/develop/docker)
- `SYNAPSE_WORKERS` - Enable multi-worker mode (default: `false`)
  - `true` - Run with optimized worker configuration (2x synchrotron, 1x event_persister, 1x federation_inbound)
  - `false` - Run in single-process mode
  - **Advanced**: Set `SYNAPSE_WORKER_TYPES` to customize worker types (see [Synapse docs](https://element-hq.github.io/synapse/latest/workers.html))
- `SERVE_WELLKNOWN` - Serve `.well-known` files for federation delegation (default: `false`)
  - `true` - Adds `serve_server_wellknown: true` to config, enabling `/.well-known/matrix/server` endpoint
  - `false` - .well-known files must be served by reverse proxy
  - **Production**: Usually set to `true` for simpler deployments, or `false` if your reverse proxy handles it

## Entrypoint Options

```
/usr/local/bin/beacon_entrypoint.py [OPTIONS]

Options:
  -c, --config <path>    Path to homeserver.yaml (default: /config/homeserver.yaml)
  --skip-templating      Skip variable substitution in config file
```

## Ports

**Single-process mode:**
- `8008` - HTTP (client and federation)
- `19090` - Prometheus metrics (if `SYNAPSE_ENABLE_METRICS=1`)

**Multi-worker mode:**
- `8080` - HTTP via nginx load balancer (client and federation)
- `8008` - Direct main process access (for createRoom, etc.)
- `19090` - Main process metrics
- `19091+` - Worker metrics (dynamic, one port per worker)
- `9469` - Prometheus HTTP service discovery + metrics proxy

## Template Variables

When using the default config (or any config with templating), these variables are substituted:

- `{{SERVER_NAME}}` → `$SERVER_NAME`
- `{{DB_HOST}}` → `$DB_HOST`
- `{{DB_USER}}` → `$DB_USER`
- `{{DB_PASS}}` → `$DB_PASS`
- `{{DB_NAME}}` → `$DB_NAME`
- `{{METRICS_BIND_ADDRESS}}` → Set based on `$SYNAPSE_ENABLE_METRICS`

## Examples

### Using docker-compose with default config

```yaml
version: '3.8'
services:
  synapse:
    image: ghcr.io/ecadinfra/deacon/beacon-node:latest
    environment:
      SERVER_NAME: beacon.example.com
      DB_HOST: postgres
      DB_USER: synapse
      DB_PASS: secret
      DB_NAME: synapse
      SIGNING_KEY: "ed25519 a_key ..."
      SYNAPSE_ENABLE_METRICS: "1"
    ports:
      - "8008:8008"
      - "19090:19090"
```

### Using custom config with docker-compose

```yaml
version: '3.8'
services:
  synapse:
    image: ghcr.io/ecadinfra/deacon/beacon-node:latest
    volumes:
      - ./my-homeserver.yaml:/custom/homeserver.yaml:ro
    environment:
      SIGNING_KEY: "ed25519 a_key ..."
      # Add any other variables your custom config uses
    command: ["-c", "/custom/homeserver.yaml"]
    ports:
      - "8008:8008"
```

## Metrics

**Following official Synapse Docker conventions** - see [Synapse Docker docs](https://github.com/element-hq/synapse/tree/develop/docker)

Prometheus metrics are available when `SYNAPSE_ENABLE_METRICS=1` (disabled by default).

### Port Allocation (Official Synapse Standard)
- **Main process**: Port **19090**
- **Worker 1**: Port **19091**
- **Worker 2**: Port **19092**
- **Worker 3**: Port **19093**
- **Worker 4**: Port **19094**

**Note**: Each worker exposes its own metrics. You should scrape all endpoints for complete visibility.

### Example Prometheus Scrape Config

**Single-process mode:**
```yaml
scrape_configs:
  - job_name: 'beacon-synapse'
    static_configs:
      - targets: ['beacon-node:19090']
```

**Multi-worker mode:**
```yaml
scrape_configs:
  - job_name: 'beacon-synapse-main'
    static_configs:
      - targets: ['beacon-node:19090']
  - job_name: 'beacon-synapse-workers'
    static_configs:
      - targets:
        - 'beacon-node:19091'
        - 'beacon-node:19092'
        - 'beacon-node:19093'
        - 'beacon-node:19094'
```

## Multi-Worker Mode

Enable horizontal scaling with optimized worker configuration:

```bash
docker run \
  -e SYNAPSE_WORKERS=true \
  -e SYNAPSE_ENABLE_METRICS=1 \
  -p 8080:8080 \
  -p 9469:9469 \
  -p 19090:19090 \
  beacon-node
```

**What happens when you set `SYNAPSE_WORKERS=true`:**
1. Entrypoint maps to: `SYNAPSE_WORKER_TYPES='synchrotron:2,event_persister:1,federation_inbound:1'`
2. Official `configure_workers_and_start.py` generates worker configs from Jinja2 templates
3. nginx load balancer is auto-configured to route requests to appropriate workers
4. supervisord starts and manages all processes (main + workers + redis + nginx)
5. All workers share Redis for coordination

**Architecture:**
- **Main process**: Handles createRoom, user registration, and other non-delegatable operations
- **2x Synchrotron workers**: Handle heavy `/sync` requests from clients (load balanced)
- **1x Event persister**: Optimized for writing events to PostgreSQL
- **1x Federation inbound**: Handles incoming federation traffic
- **nginx**: Routes requests to correct process based on endpoint
- **Redis**: Coordinates state between processes
- **supervisord**: Manages process lifecycle with auto-restart

**Advanced customization:**
```bash
# Custom worker types (see Synapse docs for available types)
docker run \
  -e SYNAPSE_WORKER_TYPES='synchrotron:4,federation_sender:2,media_repository:1' \
  beacon-node
```

Available worker types: `synchrotron`, `event_persister`, `federation_sender`, `federation_inbound`, `federation_reader`, `client_reader`, `pusher`, `appservice`, `user_dir`, `media_repository`, and more. See [official docs](https://element-hq.github.io/synapse/latest/workers.html).

**Metrics in worker mode:**
```yaml
# Prometheus scrape config
scrape_configs:
  - job_name: 'beacon-synapse'
    # HTTP service discovery (recommended)
    http_sd_configs:
      - url: http://beacon-node:9469/metrics/service_discovery
    # Or scrape all metrics through the proxy
    metrics_path: '/metrics'
    relabel_configs:
      - source_labels: [__metrics_path__]
        target_label: __metrics_path__
```

## Key Features

- **Synapse v1.147.1** (Element HQ build) - 49+ CVEs patched since v1.98.0
- **Crypto authentication** via Ed25519 signatures (no passwords)
- **Custom auth provider** replaces password auth
- **1MB max event size** (increased from 64KB default)
- **Flexible configuration** - use default or bring your own
- **Official worker orchestration** - supervisord + nginx + redis
- **Optimized worker types** - specialized workers for better performance
- **Prometheus metrics** with HTTP service discovery
- **Backward compatible** - simple `SYNAPSE_WORKERS=true` toggle
- **Production-ready** for Tezos Beacon relay servers

## Migration from v1.98.0 (AirGap)

This image migrates from AirGap's original v1.98.0 setup to modern Synapse v1.147.1:

**Key changes:**
- ✅ **49+ CVEs patched** (v1.98.0 → v1.147.1)
- ✅ **Official worker orchestration** replaces manual synctl
- ✅ **Optimized worker types** (synchrotron, event_persister, federation_inbound) replace generic workers
- ✅ **Supervisor process management** with auto-restart on crashes
- ✅ **nginx load balancer** included and auto-configured
- ✅ **Prometheus HTTP service discovery** for dynamic worker metrics

**Backward compatibility:**
- Same environment variables (`SYNAPSE_WORKERS=true`, `SYNAPSE_ENABLE_METRICS=1`)
- Same `-c` and `--skip-templating` flags
- Same crypto auth and custom modules
- Port changes: 8080 for nginx (worker mode), 9469 for metrics proxy

## Troubleshooting

**Worker mode not starting:**
- Check logs: `docker logs <container>`
- Ensure Redis is accessible (automatically started in worker mode)
- Verify PostgreSQL is reachable

**Metrics not accessible:**
- Set `SYNAPSE_ENABLE_METRICS=1` (disabled by default)
- In worker mode, use HTTP service discovery: `http://<host>:9469/metrics/service_discovery`
- Or proxy all metrics through: `http://<host>:9469/metrics/worker/<worker_name>`

**Performance issues:**
- Enable worker mode: `SYNAPSE_WORKERS=true`
- Increase worker count for specific types: `SYNAPSE_WORKER_TYPES='synchrotron:4,event_persister:2'`
- Monitor metrics to identify bottlenecks
