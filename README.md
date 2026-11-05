# beacon-synapse

[Matrix](https://matrix.org/) homeserver image for operating [Beacon](https://tzip.tezosagora.org/proposal/tzip-10/) relay nodes, maintained for testing and as a public reference implementation. ECAD no longer operates public relay nodes. Beacon is the open communication standard connecting Tezos wallets and dApps, ratified as [TZIP-10](https://tzip.tezosagora.org/proposal/tzip-10/). Matrix provides the federated transport layer.

## Provenance

Beacon relay infrastructure was originally created by [Papers (AirGap)](https://papers.ch/) as part of the [Beacon SDK](https://github.com/airgap-it/beacon-sdk) ecosystem. Papers designed the protocol, built the SDK, and operated the first relay nodes, establishing the standard that the entire Tezos wallet and dApp ecosystem relies on today.

This image builds on [AirGap's beacon-node](https://github.com/airgap-it/beacon-node), with upgrades to the underlying Synapse version, added observability, and worker support for higher throughput.

### What changed from upstream

- **Synapse v1.98.0 to v1.164.2**: Upgraded to current release
- **`crypto_auth_provider.py` v0.4**: Synapse ModuleApi callback registration, strict Ed25519 credential parsing, structured operational logs, and concurrent first-login recovery
- **`beacon_monitor_module.py`**: Observability module for diagnosing connection and federation issues. Logs operational metadata (room lifecycle, membership changes, payload sizes, login events) in logfmt format. All Beacon message payloads are encrypted end-to-end between wallet and dApp using NaCl cryptobox before reaching the relay server; message content is not and cannot be logged. User and room identifiers are opaque hashes with no link to real-world identity.
- **`beacon_info_module.py`**: HTTP endpoint exposing server region and known relay servers
- **Worker mode**: Official Element HQ worker orchestration (supervisord + nginx + redis) for horizontal scaling
- **`MAX_PDU_SIZE` patch**: 64KB to 1MB (Beacon messages can exceed the default Matrix limit)
- **logfmt logging**: Structured log output for ingestion into Loki/Grafana/etc.
- **SSRF protection**: `federation_ip_range_blacklist` covering RFC 1918, link-local, and loopback
- **Robust entrypoint**: Template variable substitution, database readiness check, single-process or multi-worker modes

## Quick start

```bash
# Single-process mode (default)
docker compose -f docker-compose.example.yml up --build

# Worker mode
SYNAPSE_WORKERS=true docker compose -f docker-compose.example.yml up --build
```

This starts Synapse with PostgreSQL 18.6. The server will be available at `http://localhost:8008`.

The PostgreSQL image stores data beneath `/var/lib/postgresql/18/docker`, with
the volume mounted at `/var/lib/postgresql`. Existing PostgreSQL 16 data needs
a supported dump/restore or `pg_upgrade` migration before use with this example;
changing the image tag does not upgrade an existing database.

## Architecture

### Single-process mode (default)

```
Client/Federation --> :8008 [Synapse] --> PostgreSQL
```

One Synapse process handles everything. Good for development and low-traffic deployments.

### Worker mode

```
Client/Federation --> :8008 [nginx] --> :8080 [Synapse main]
                                   \-> :18009+ [workers]
                                        - synchrotron (sync)
                                        - event_persister (writes)
                                        - federation_inbound (federation)
                     :9469 [nginx] --> Prometheus service discovery
                     [redis] <------> inter-process replication
                     [supervisord] --> manages all processes
```

Supervisord orchestrates the main Synapse process, worker processes, nginx, and redis, all inside a single container. nginx routes requests to the appropriate worker based on URL patterns. Redis handles inter-process replication.

## Configuration

The image uses template variables in `homeserver.yaml` that are substituted at startup. Pass them as environment variables:

| Variable | Required | Description |
|---|---|---|
| `SERVER_NAME` | Yes | Matrix server name (e.g., `beacon-1.example.com`) |
| `DB_HOST` | Yes | PostgreSQL hostname |
| `DB_USER` | Yes | PostgreSQL username |
| `DB_PASS` | Yes | PostgreSQL password |
| `DB_NAME` | Yes | PostgreSQL database name |
| `SIGNING_KEY` | Yes | Synapse signing key (e.g., `ed25519 a_key0 <base64>`) |
| `REGISTRATION_SHARED_SECRET` | Yes | Synapse admin registration secret |
| `SERVER_REGION` | No | Region label for the `/beacon/info` endpoint |
| `SYNAPSE_ENABLE_METRICS` | No | Set to `1` to expose Prometheus metrics on port 19090 |
| `SYNAPSE_WORKERS` | No | Set to `true` to enable multi-worker mode |
| `SYNAPSE_WORKER_TYPES` | No | Comma-separated worker types (default: `synchrotron:2,event_persister:1,federation_inbound:1`) |
| `PUBLIC_BASEURL` | No | Public URL for federation (default: `https://SERVER_NAME`) |
| `SERVE_WELLKNOWN` | No | Set to `true` to serve `.well-known/matrix/server` for Cloudflare |
| `DB_CP_MIN` | No | Minimum database connections (default: `20`) |
| `DB_CP_MAX` | No | Maximum database connections (default: `80`) |

### Worker types

The `SYNAPSE_WORKER_TYPES` variable accepts the official Element HQ worker type syntax:

```bash
# Simple list
SYNAPSE_WORKER_TYPES="synchrotron,event_persister,federation_inbound"

# With multipliers
SYNAPSE_WORKER_TYPES="synchrotron:2,event_persister:2,federation_inbound:1"

# Combined workers (merge types into one process)
SYNAPSE_WORKER_TYPES="stream_writers=account_data+presence+typing"

# Custom names
SYNAPSE_WORKER_TYPES="sync=synchrotron:2,persist=event_persister:1"
```

Available types: `synchrotron`, `event_persister`, `federation_inbound`, `federation_sender`, `federation_reader`, `client_reader`, `event_creator`, `media_repository`, `user_dir`, `pusher`, `appservice`, `background_worker`, `account_data`, `presence`, `receipts`, `to_device`, `typing`, `push_rules`, `device_lists`, `thread_subscriptions`.

### Entrypoint options

```bash
# Default: uses /config/homeserver.yaml with variable substitution
docker run ghcr.io/ecadinfra/beacon-synapse

# Custom config path
docker run ghcr.io/ecadinfra/beacon-synapse -c /custom/homeserver.yaml

# Skip template substitution (if you mount a pre-configured file)
docker run ghcr.io/ecadinfra/beacon-synapse --skip-templating
```

### Ports

| Port | Mode | Service |
|---|---|---|
| 8008 | Both | HTTP (client + federation). Direct in single mode, nginx in worker mode |
| 8080 | Worker | Main Synapse process (internal, behind nginx) |
| 9469 | Worker | Prometheus service discovery + metrics proxy |
| 19090 | Both | Prometheus metrics (main process, when enabled) |

### Prometheus service discovery (worker mode)

When `SYNAPSE_ENABLE_METRICS=1` and worker mode is active, port 9469 serves:

- `GET /metrics/service_discovery` - JSON for Prometheus `http_sd_config`
- `GET /metrics/worker/<name>` - Proxied metrics for each worker
- `GET /metrics/worker/main` - Proxied metrics for the main process

Prometheus config:

```yaml
scrape_configs:
  - job_name: beacon-synapse
    http_sd_configs:
      - url: http://synapse:9469/metrics/service_discovery
    honor_labels: true
```

### Federation behind Cloudflare

If your server is behind Cloudflare (or any proxy that doesn't support port 8448), enable `.well-known` delegation:

```yaml
environment:
  PUBLIC_BASEURL: "https://beacon-1.example.com"
  SERVE_WELLKNOWN: "true"
```

This configures Synapse to:
1. Serve `/.well-known/matrix/server` with delegation to port 443
2. Set `public_baseurl` for proper federation discovery

Other Matrix servers will then connect on port 443 instead of 8448. Ensure your reverse proxy routes:
- `/.well-known/matrix/server` -> Synapse (port 8008)
- `/_matrix/federation/*` -> Synapse (port 8008)
- `/_matrix/client/*` -> Synapse (port 8008)

**Important**: Configure Cloudflare to not challenge `/_matrix/federation/*` paths (these are server-to-server requests, not browsers).

## Authentication protocol

This image replaces Matrix password authentication with Ed25519 signature verification via `crypto_auth_provider.py`.

- **Username**: BLAKE2b hash of the Ed25519 public key (hex encoded)
- **Password**: `ed:<signature>:<public_key>` (both hex encoded)
- **Signature covers**: `BLAKE2b("login:<time_window>")` where `time_window = floor(unix_time / 300)`
- **Clock tolerance**: Accepts signatures for the current, previous, and next 5-minute windows
- **Auto-registration**: New users are automatically registered on first successful authentication

This is the standard Beacon authentication mechanism defined in [TZIP-10](https://tzip.tezosagora.org/proposal/tzip-10/). Any Beacon SDK client or wallet that implements the specification will work with any compliant relay node, regardless of operator.

## Modules

### `beacon_monitor_module.py`

Hooks into Synapse's module API to log operational metadata in logfmt format. Designed for diagnosing connection failures, federation lag, and capacity planning.

- `event=MEMBERSHIP`: Room joins, leaves, invites with local/remote/federated classification
- `event=ROOM_CREATED`: New room creation
- `event=MESSAGE`: Payload size in bytes (content is **not** logged)
- `event=LOGIN`: Login events with auth provider info
- `event=ENCRYPTION_ENABLED`: Room encryption events

**Privacy**: Beacon messages are encrypted end-to-end (NaCl cryptobox) by the SDK before reaching the relay server. This module logs only the size of encrypted payloads, never their content. Relay operators cannot decrypt message payloads. User IDs are BLAKE2b hashes of ephemeral public keys, and room IDs are opaque Matrix identifiers. Neither is linked to real-world identity.

### `beacon_info_module.py`

Exposes `/_synapse/client/beacon/info` returning:

```json
{
  "region": "na-west",
  "known_servers": ["beacon-node-1.octez.io", "beacon-node-2.octez.io", "..."],
  "timestamp": 1708300000.0
}
```

## Running your own relay node

If you want to operate a Beacon relay node for the Tezos ecosystem:

1. Deploy this image with the configuration above
2. Ensure port 8448 (or 443 if using `.well-known` delegation) is reachable from other relay nodes
3. Configure federation with existing operators so wallets and dApps on your node can communicate with the broader network
4. Open an issue or reach out to coordinate federation peering

The more independent operators running relay infrastructure, the more resilient the network is for everyone.

### Operator maintenance

This repository is maintained for testing and as a public reference for relay
operators. ECAD no longer operates public relay nodes. Listing a
remote relay in `known_servers` enables discovery; it does not mean that ECAD
operates that relay, maintains its software, or has validated its deployed build.
Each operator is responsible for its own Synapse upgrades, custom modules,
configuration, monitoring, and wallet interoperability checks.

Track [Synapse releases](https://github.com/element-hq/synapse/releases) and review
upgrade notes before deployment. A client SDK release does not update the relay
server. Record the deployed image digest, source revision, and local patches so
operators can reproduce a build and identify what needs updating.

Before rolling out a new build, validate login, pairing, reconnects, and federation
with the wallets and remote relays used by the deployment. Worker deployments
also need checks against their actual PostgreSQL, Redis, and request-routing
configuration. The unit tests in this repository cover module behavior; they do
not certify another operator's deployment.

## Links

- [TZIP-10 specification](https://tzip.tezosagora.org/proposal/tzip-10/) (the Beacon standard)
- [Beacon SDK](https://github.com/airgap-it/beacon-sdk) (Papers/AirGap)
- [AirGap beacon-node](https://github.com/airgap-it/beacon-node) (upstream relay node)
- [Synapse](https://github.com/element-hq/synapse) (Matrix homeserver)
- [ECAD Infra](https://ecadinfra.com)

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-only).

See [NOTICE](NOTICE) for attribution of upstream works.

### Source for our test environment

This repository contains the code we run for Beacon relay testing, including our
Synapse modifications, authentication modules, configuration templates, and build
scripts. The [Dockerfile](Dockerfile) pins the upstream Synapse version and records
the build-time patch so the image can be reproduced from source.

We publish these changes here under the AGPL-3.0-only license.

## ModuleApi authentication and relay limits

The auth provider is loaded under `modules`, alongside the info and monitor
modules. `password_config.enabled: true` enables the `m.login.password` protocol
flow used for signed credentials; `localdb_enabled: false` disables local database
password authentication. Credentials use the current, previous, or next five-minute
bucket. Authentication succeeds after the account exists or registration completes.
Backend failures return an authentication failure without logging exception text.
`event=AUTH_OK` records successful module authentication, not completion of the
whole login request. Synapse can still reject a later step, such as rate limiting
or device creation. Use the monitor module's `event=LOGIN` for completed logins.

The main process and all workers load `homeserver.yaml`; the shared worker template
adds worker settings without replacing its module list or rate limits. Keep the
base configuration when supplying custom worker configuration files.

Public room directory queries over federation are disabled. This does not disable
room federation or change room join rules. Invite limits are explicitly configured:

| Scope | Invites per second | Burst | Compared with Synapse v1.164.2 defaults |
|---|---:|---:|---|
| Per room | 0.3 | 10 | Unchanged, configured explicitly |
| Per recipient | 0.003 | 5 | Unchanged, configured explicitly |
| Per issuer | 0.1 | 5 | Reduced from 0.3 per second, burst 10 |

The issuer limit bounds an account's invites across rooms. After the initial burst
of five, capacity replenishes at one invite every ten seconds. Before rollout,
measure invite volume per sender using `event=MEMBERSHIP membership=invite` logs,
and check pairing and reconnect bursts. Monitor `M_LIMIT_EXCEEDED` responses after
deployment and tune the limits to the observed workload. Inbound federated
invites are subject to the recipient limit; issuing servers enforce their own issuer
and room limits. These are Synapse process-local rate limiters, not a fleet-wide quota.
See the [Synapse rate-limit documentation](https://element-hq.github.io/synapse/latest/usage/configuration/config_documentation.html#rc_invites).

Relay discovery lists `beacon-node-{1..8}.octez.io`. Retired Papers and ECAD relay
entries have been removed. Discovery is
not a federation allowlist and does not prevent existing peers from federating.

### Room join guard assessment

A `check_event_allowed` guard could allow a membership join when the previous
membership is `invite` or already `join`, the joining user is the room creator,
or fewer than two members are joined. Existing joins need an exception so profile
updates and repeated joins do not fail. Creator detection should use the create
event sender for compatibility across room versions.

Legacy room creation uses `public_chat` with an explicit peer invite, so the usual
creator-then-invited-peer sequence fits this policy. However, it does not enforce
a two-member maximum: additional invited users still qualify, an uninvited user
can occupy the second slot, and concurrent joins can observe the same prior state.
It can also deny an uninvited rejoin once two other members are present.

No enforcing guard is enabled. Synapse warns that rejecting received federation
events through this experimental callback can cause local and remote room histories
to diverge. A local-sender-only rule would avoid rejecting remote events but would
not enforce a network-wide room size. A coordinated relay policy needs multi-server
validation of invites, rejoins, simultaneous joins, and state resolution before
rollout. See [Synapse third-party rules](https://element-hq.github.io/synapse/latest/modules/third_party_rules_callbacks.html#check_event_allowed).

## Development checks

Run the checks with the locked development environment:

```bash
uv run --locked pytest -q
uv run --locked ty check
uv run --locked ruff check
uv run --locked ruff format --check
```

Type and lint checks cover the authentication module and its tests. The development
environment includes the same Synapse version as the Docker image so `ty` checks
the real ModuleApi contract. Keep that version aligned when updating the image.
The upstream worker orchestration and other modules are outside this check scope.

The authentication boundary accepts `Mapping[str, object]`: values must be narrowed
before use, and the module does not mutate the input. No `Any` or casts are needed.

The suite uses real Ed25519 signatures and a narrow
ModuleApi double to exercise validation, bucket boundaries, account registration,
concurrent registration recovery, and credential-free operational logs. It does not
replace container startup or federation interoperability testing.
