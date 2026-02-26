# Testing the Beacon Node Docker Image

Quick guide for testing the hybrid worker architecture locally.

## Prerequisites

```bash
# Make sure you've built the image
docker build -t beacon-node-test:latest .

# Or pull from GHCR (once published)
# docker pull ghcr.io/ecadinfra/deacon/beacon-node:latest
```

## Test Scenarios

### 1. Single-Process Mode (Simplest)

**Start:**
```bash
docker-compose -f docker-compose.test.yml --profile single up
```

**Verify:**
```bash
# Health check
curl http://localhost:8008/health

# Metrics (should see synapse_* metrics)
curl http://localhost:19090/metrics

# Logs should show:
# [beacon-entrypoint] Starting Synapse in single-process mode
```

**Expected behavior:**
- ✅ Synapse starts on port 8008
- ✅ Metrics available on port 19090
- ✅ No Redis or nginx running
- ✅ No worker processes

**Stop:**
```bash
docker-compose -f docker-compose.test.yml --profile single down
```

---

### 2. Multi-Worker Mode (Production-like)

**Start:**
```bash
docker-compose -f docker-compose.test.yml --profile workers up
```

**Verify:**
```bash
# Health check (through nginx)
curl http://localhost:8080/health

# Main process metrics
curl http://localhost:19090/metrics

# Worker 1 metrics
curl http://localhost:19091/metrics

# Prometheus service discovery
curl http://localhost:9469/metrics/service_discovery

# Logs should show:
# [beacon-entrypoint] Starting Synapse in multi-worker mode
# [beacon-entrypoint] Using worker types: synchrotron:2,event_persister:1,federation_inbound:1
# supervisor... started: redis
# supervisor... started: nginx
# supervisor... started: synapse_main
# supervisor... started: synchrotron_1
# supervisor... started: synchrotron_2
# supervisor... started: event_persister_1
# supervisor... started: federation_inbound_1
```

**Expected behavior:**
- ✅ nginx load balancer on port 8080
- ✅ Main process accessible directly on port 8008
- ✅ Redis running automatically
- ✅ 4 workers running (2x synchrotron, 1x event_persister, 1x federation_inbound)
- ✅ Metrics on ports 19090-19093
- ✅ Service discovery on port 9469

**Stop:**
```bash
docker-compose -f docker-compose.test.yml --profile workers down
```

---

### 3. With Monitoring Stack (Full observability)

**Start:**
```bash
docker-compose -f docker-compose.test.yml --profile workers --profile monitoring up
```

**Access:**
- **Synapse**: http://localhost:8080
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000 (admin/admin)

**In Prometheus UI:**
1. Go to Status → Targets
2. Verify all beacon-workers targets are UP
3. Try queries:
   ```promql
   synapse_http_server_requests_total
   rate(synapse_http_server_requests_total[5m])
   ```

**In Grafana:**
1. Add Prometheus data source: http://prometheus:9090
2. Import Synapse dashboard (ID: 10692 from grafana.com)

---

## Testing Worker Types

### Test Different Worker Configurations

**4 Synchrotron workers (high client sync load):**
```bash
# Edit docker-compose.test.yml, uncomment SYNAPSE_WORKER_TYPES line:
SYNAPSE_WORKER_TYPES: "synchrotron:4"
```

**Mixed worker types:**
```bash
SYNAPSE_WORKER_TYPES: "synchrotron:2,federation_sender:2,event_persister:1,media_repository:1"
```

---

## Debugging

### View logs
```bash
# All services
docker-compose -f docker-compose.test.yml logs -f

# Just beacon node
docker logs -f beacon-single    # or beacon-workers

# Specific process in worker mode
docker exec beacon-workers supervisorctl status
docker exec beacon-workers tail -f /var/log/supervisor/synapse_main-stdout.log
docker exec beacon-workers tail -f /var/log/supervisor/synchrotron_1-stdout.log
```

### Enter container
```bash
docker exec -it beacon-workers /bin/bash

# Check processes
supervisorctl status

# Check nginx config
cat /etc/nginx/conf.d/matrix-synapse.conf

# Check worker configs
ls -la /run/workers/
cat /run/workers/synchrotron_1.yaml
```

### Check database
```bash
docker exec -it beacon-postgres psql -U synapse -d synapse

# List tables
\dt

# Check users (should see crypto auth users)
SELECT name FROM users;
```

---

## Performance Testing

### Simulate load (requires Synapse SDK)
```bash
# Install matrix-nio
pip install matrix-nio

# Python script to test sync performance
python <<EOF
from nio import AsyncClient
import asyncio

async def test_sync():
    client = AsyncClient("http://localhost:8080", "@test:beacon.local.test")
    # ... authenticate and sync ...

asyncio.run(test_sync())
EOF
```

### Monitor metrics during load
```bash
# Watch request rate
watch -n 1 'curl -s http://localhost:19090/metrics | grep synapse_http_server_requests_total'
```

---

## Common Issues

### "Worker mode not starting"
```bash
# Check logs for supervisor errors
docker logs beacon-workers 2>&1 | grep -i error

# Common causes:
# - PostgreSQL not ready → wait for healthcheck
# - SIGNING_KEY not set → check env vars
# - Port conflicts → check if 8080/8008 already in use
```

### "Metrics not accessible"
```bash
# Verify SYNAPSE_ENABLE_METRICS=1
docker exec beacon-workers env | grep SYNAPSE_ENABLE_METRICS

# Check if metrics listener is bound
docker exec beacon-workers netstat -tlnp | grep 19090
```

### "Can't connect to PostgreSQL"
```bash
# Check network connectivity
docker exec beacon-workers nc -zv postgres 5432

# Check postgres is ready
docker exec beacon-postgres pg_isready -U synapse
```

---

## Clean Up

**Remove containers and volumes:**
```bash
docker-compose -f docker-compose.test.yml down -v
```

**Remove built image:**
```bash
docker rmi beacon-node-test:latest
```

**Full cleanup:**
```bash
docker-compose -f docker-compose.test.yml down -v --remove-orphans
docker system prune -af
```

---

## Next Steps

Once testing is successful:

1. **Tag the image:**
   ```bash
   docker tag beacon-node-test:latest ghcr.io/ecadinfra/deacon/beacon-node:test
   ```

2. **Push to registry:**
   ```bash
   docker push ghcr.io/ecadinfra/deacon/beacon-node:test
   ```

3. **Deploy to staging:**
   - Use same docker-compose pattern
   - Update SERVER_NAME to real domain
   - Use proper signing key
   - Add TLS termination (traefik/nginx)

4. **Monitor in production:**
   - Set up Prometheus scraping
   - Configure alerts
   - Monitor worker metrics
