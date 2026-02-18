# beacon-synapse

Production-ready [Matrix](https://matrix.org/) homeserver image for operating [Beacon](https://tzip.tezosagora.org/proposal/tzip-10/) relay nodes. Beacon is the open communication standard connecting Tezos wallets and dApps, ratified as [TZIP-10](https://tzip.tezosagora.org/proposal/tzip-10/). Matrix provides the federated transport layer.

This image is what [ECAD Infra](https://ecadinfra.com) runs in production. We publish it so that anyone can operate Beacon relay infrastructure using the same tooling we do.

## Why operator diversity matters

Beacon relay nodes are the infrastructure that every Tezos wallet-to-dApp connection depends on. When a user pairs a wallet with a dApp, that connection flows through Matrix relay servers.

The Beacon network is stronger when relay infrastructure is operated by **multiple independent organizations** across different regions and hosting providers. Federation is a core design property of the Matrix protocol, and Beacon inherits it: wallets and dApps can communicate regardless of which operator's relay node they connect through. No single organization needs to be a bottleneck or a single point of failure.

This image exists to make running a Beacon relay node straightforward. If you operate infrastructure in the Tezos ecosystem, consider running one.

## Provenance

Beacon relay infrastructure was originally created by [Papers (AirGap)](https://papers.ch/) as part of the [Beacon SDK](https://github.com/airgap-it/beacon-sdk) ecosystem. Papers designed the protocol, built the SDK, and operated the first relay nodes, establishing the standard that the entire Tezos wallet and dApp ecosystem relies on today.

This image builds on [AirGap's beacon-node](https://github.com/airgap-it/beacon-node), with upgrades to the underlying Synapse version, added observability, and worker support for higher throughput.

### What changed from upstream

- **Synapse v1.98.0 to v1.147.1**: Upgraded to current release
- **`beacon_monitor_module.py`**: Observability module for diagnosing connection and federation issues. Logs operational metadata (room lifecycle, membership changes, payload sizes, login events) in logfmt format. All Beacon message payloads are encrypted end-to-end between wallet and dApp using NaCl cryptobox before reaching the relay server; message content is not and cannot be logged. User and room identifiers are opaque hashes with no link to real-world identity.
- **`beacon_info_module.py`**: HTTP endpoint exposing server region and known relay servers
- **Worker mode**: Support for 4 generic workers behind the main process
- **`MAX_PDU_SIZE` patch**: 64KB to 1MB (Beacon messages can exceed the default Matrix limit)
- **logfmt logging**: Structured log output for ingestion into Loki/Grafana/etc.
- **Robust entrypoint**: Template variable substitution, database readiness check, single-process or multi-worker modes

## Quick start

```bash
docker compose -f docker-compose.example.yml up --build
```

This starts Synapse with PostgreSQL and Redis. The server will be available at `http://localhost:8008`.

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

| Port | Service |
|---|---|
| 8008 | HTTP (client + federation) |
| 19090 | Prometheus metrics (main process, when enabled) |
| 19091-19094 | Prometheus metrics (workers 1-4, when enabled) |

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
  "known_servers": ["beacon-node-1.diamond.papers.tech", "..."],
  "timestamp": 1708300000.0
}
```

## Running your own relay node

If you want to operate a Beacon relay node for the Tezos ecosystem:

1. Deploy this image with the configuration above
2. Ensure port 8443 (or your federation port) is reachable from other relay nodes
3. Configure federation with existing operators so wallets and dApps on your node can communicate with the broader network
4. Open an issue or reach out to coordinate federation peering

The more independent operators running relay infrastructure, the more resilient the network is for everyone.

## Links

- [TZIP-10 specification](https://tzip.tezosagora.org/proposal/tzip-10/) (the Beacon standard)
- [Beacon SDK](https://github.com/airgap-it/beacon-sdk) (Papers/AirGap)
- [AirGap beacon-node](https://github.com/airgap-it/beacon-node) (upstream relay node)
- [Synapse](https://github.com/element-hq/synapse) (Matrix homeserver)
- [ECAD Infra](https://ecadinfra.com)

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-only).

See [NOTICE](NOTICE) for attribution of upstream works.
