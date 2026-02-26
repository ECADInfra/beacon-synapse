#!/usr/local/bin/python
"""
Beacon-specific entrypoint wrapper for Synapse v1.147.1

This wrapper provides backward compatibility with beacon-node's existing
environment variables and configuration patterns while leveraging the
official Synapse worker orchestration system.

Environment Variables:
  SYNAPSE_WORKERS (true/false) - Enable multi-worker mode
  SYNAPSE_ENABLE_METRICS (0/1) - Enable Prometheus metrics
  SERVER_NAME, DB_HOST, DB_USER, DB_PASS, DB_NAME - Database config
  SIGNING_KEY - Synapse signing key

Command-line Arguments:
  -c, --config <path>    Path to homeserver.yaml
  --skip-templating      Skip variable substitution
"""

import os
import sys
import subprocess
import argparse


def log(msg: str) -> None:
    """Print log message to stdout"""
    print(f"[beacon-entrypoint] {msg}", flush=True)


def error(msg: str) -> None:
    """Print error message and exit"""
    print(f"[beacon-entrypoint] ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def perform_template_substitution(config_file: str) -> None:
    """
    Perform variable substitution in config files.
    Uses simple string replacement for backward compatibility.
    """
    log("Performing template variable substitution...")

    # Required variables
    required_vars = ['SERVER_NAME', 'DB_HOST', 'DB_USER', 'DB_PASS', 'DB_NAME']
    for var in required_vars:
        if var not in os.environ:
            error(f"Required environment variable {var} is not set")

    # Determine metrics bind address
    enable_metrics = os.environ.get('SYNAPSE_ENABLE_METRICS', '0')
    metrics_bind = '0.0.0.0' if enable_metrics == '1' else '127.0.0.1'

    log(f"Metrics {'enabled' if enable_metrics == '1' else 'disabled'} (bind: {metrics_bind})")

    # Check for .well-known serving
    serve_wellknown = os.environ.get('SERVE_WELLKNOWN', '').lower() in ('true', '1', 'yes')
    if serve_wellknown:
        log("Well-known serving enabled")

    # Read config file
    try:
        with open(config_file, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        error(f"Config file not found: {config_file}")

    # Perform substitutions
    substitutions = {
        '{{SERVER_NAME}}': os.environ['SERVER_NAME'],
        '{{DB_HOST}}': os.environ['DB_HOST'],
        '{{DB_USER}}': os.environ['DB_USER'],
        '{{DB_PASS}}': os.environ['DB_PASS'],
        '{{DB_NAME}}': os.environ['DB_NAME'],
        '{{METRICS_BIND_ADDRESS}}': metrics_bind,
        # Optional variables with defaults
        '{{PUBLIC_BASEURL}}': os.environ.get('PUBLIC_BASEURL', f"https://{os.environ['SERVER_NAME']}"),
        '{{SERVER_REGION}}': os.environ.get('SERVER_REGION', 'region not set'),
        '{{REGISTRATION_SHARED_SECRET}}': os.environ.get('REGISTRATION_SHARED_SECRET', ''),
        '{{DB_CP_MIN}}': os.environ.get('DB_CP_MIN', '5'),
        '{{DB_CP_MAX}}': os.environ.get('DB_CP_MAX', '10'),
    }

    for placeholder, value in substitutions.items():
        content = content.replace(placeholder, value)

    # Write back
    with open(config_file, 'w') as f:
        f.write(content)

    # Add serve_server_wellknown if enabled
    if serve_wellknown:
        import yaml
        log("Adding serve_server_wellknown: true to config")
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)
        config['serve_server_wellknown'] = True
        with open(config_file, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    # Also process shared_config.yaml if it exists
    shared_config = '/config/shared_config.yaml'
    if os.path.exists(shared_config):
        log(f"Processing {shared_config}...")
        with open(shared_config, 'r') as f:
            shared_content = f.read()
        shared_content = shared_content.replace('{{SERVER_NAME}}', os.environ['SERVER_NAME'])
        with open(shared_config, 'w') as f:
            f.write(shared_content)

    log("Template substitution completed")


def write_signing_key() -> None:
    """Write signing key to file from environment variable"""
    signing_key = os.environ.get('SIGNING_KEY')
    if not signing_key:
        error("SIGNING_KEY environment variable is required")

    signing_key_path = '/config/signing.key'
    log(f"Writing signing key to {signing_key_path}")
    with open(signing_key_path, 'w') as f:
        f.write(signing_key)
    # Secure the signing key file
    os.chmod(signing_key_path, 0o600)
    log(f"Set signing key permissions to 600")


def wait_for_database() -> None:
    """Wait for database to be ready"""
    db_host = os.environ.get('DB_HOST', 'postgres')
    log(f"Waiting for database at {db_host}:5432...")
    subprocess.run(['/usr/local/bin/wait-for.sh', f'{db_host}:5432'], check=True)
    log("Database is ready")


def start_single_process(config_file: str) -> None:
    """Start Synapse in single-process mode"""
    log(f"Starting Synapse in single-process mode with config: {config_file}")

    # Execute synapse directly
    os.execvp('python', [
        'python',
        '-m',
        'synapse.app.homeserver',
        '--config-path',
        config_file
    ])


def prepare_worker_mode_config(config_file: str) -> str:
    """
    Create a worker-mode-specific config by modifying listeners for worker mode.
    In worker mode:
    - Port 8008 becomes the nginx load balancer (external)
    - Port 8080 becomes the main Synapse process (internal, behind nginx)
    - Workers run on ports 18009+
    """
    import yaml

    log("Preparing worker-mode configuration...")

    # Load the config
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)

    # Modify listeners for worker mode
    if 'listeners' in config:
        new_listeners = []
        for listener in config['listeners']:
            # Skip replication listener - configure_workers_and_start.py will add it
            if listener.get('port') == 9093 and 'replication' in str(listener.get('resources', [])):
                log("Removing replication listener (port 9093) - will be generated by worker script")
                continue

            # Change port 8008 client/federation listener to port 8080
            # This allows nginx to take over port 8008 and proxy to 8080
            if listener.get('port') == 8008:
                log("Changing main process listener from port 8008 to 8080 (nginx will handle 8008)")
                listener['port'] = 8080
                listener['bind_address'] = '127.0.0.1'  # Only accessible internally
            new_listeners.append(listener)
        config['listeners'] = new_listeners

    # Write to a worker-specific config file
    worker_config = '/config/homeserver.worker.yaml'
    with open(worker_config, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    log(f"Worker-mode config written to {worker_config}")
    return worker_config


def start_worker_mode(config_file: str) -> None:
    """
    Start Synapse in multi-worker mode using official worker orchestration.
    Maps beacon's simple SYNAPSE_WORKERS=true to optimized worker types.
    """
    log("Starting Synapse in multi-worker mode")

    # Prepare worker-specific config (removes client/federation from port 8008)
    worker_config = prepare_worker_mode_config(config_file)

    # Map beacon's worker mode to optimized Synapse worker types
    # This provides better performance than 4 generic workers:
    # - synchrotron:2 = 2 workers for heavy client sync operations
    # - event_persister:1 = 1 worker for writing events to database
    # - federation_inbound:1 = 1 worker for receiving federation messages
    worker_types = os.environ.get('SYNAPSE_WORKER_TYPES',
                                   'synchrotron:2,event_persister:1,federation_inbound:1')

    log(f"Using worker types: {worker_types}")

    # Set environment variables for configure_workers_and_start.py
    env = os.environ.copy()
    env['SYNAPSE_WORKER_TYPES'] = worker_types

    # The official script needs SYNAPSE_SERVER_NAME and SYNAPSE_REPORT_STATS
    if 'SYNAPSE_SERVER_NAME' not in env and 'SERVER_NAME' in env:
        env['SYNAPSE_SERVER_NAME'] = env['SERVER_NAME']

    if 'SYNAPSE_REPORT_STATS' not in env:
        env['SYNAPSE_REPORT_STATS'] = 'no'

    # Set config path for worker orchestration (use worker-specific config)
    env['SYNAPSE_CONFIG_PATH'] = worker_config

    log("Executing configure_workers_and_start.py...")

    # Execute the official worker orchestration script
    os.execve(
        '/usr/local/bin/configure_workers_and_start.py',
        ['/usr/local/bin/configure_workers_and_start.py'],
        env
    )


def main() -> None:
    """Main entrypoint logic"""

    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='Beacon Synapse Entrypoint')
    parser.add_argument('-c', '--config',
                        default='/config/homeserver.yaml',
                        help='Path to homeserver.yaml')
    parser.add_argument('--skip-templating',
                        action='store_true',
                        help='Skip variable substitution in config')

    args = parser.parse_args()
    config_file = args.config

    log(f"Beacon Synapse v1.147.1 entrypoint starting...")
    log(f"Using config file: {config_file}")

    # Perform template substitution unless skipped
    if not args.skip_templating:
        perform_template_substitution(config_file)
    else:
        log("Skipping template substitution (--skip-templating)")

    # Write signing key
    write_signing_key()

    # Wait for database
    wait_for_database()

    # Determine mode: single-process or multi-worker
    workers_enabled = os.environ.get('SYNAPSE_WORKERS', 'false').lower() == 'true'

    if workers_enabled:
        start_worker_mode(config_file)
    else:
        start_single_process(config_file)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        error(f"Unexpected error: {e}")
        sys.exit(1)
