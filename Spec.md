# Portainer mTLS Proxy — Merged Project Specification

## Project Description

This project implements a local proxy container that enables a Portainer CE installation to manage remote Portainer Agents that are protected by mutual TLS (mTLS). Portainer CE does not natively support TLS client certificates for outbound agent connections. The remote agents sit behind Apache reverse proxies that require a valid TLS client certificate from any connecting client. This proxy bridges that gap: it runs alongside Portainer CE in the same Docker Compose stack, accepts plain HTTP connections from Portainer, and forwards them to the remote agents over HTTPS with the required client certificate attached. A lightweight web-based management UI allows the port-to-remote-URL mappings to be configured at runtime, without rebuilding or restarting the container. All privileged operations (writing nginx configuration, reloading nginx) are handled by a dedicated root-level agent process, while the web UI runs as an unprivileged user, communicating with the agent exclusively through a permission-restricted Unix domain socket.

---

## Architecture

```
Portainer CE ──(HTTP, Docker network)──► mTLS Proxy Container ──(HTTPS + client cert)──► Apache (remote) ──► Portainer Agent
                                              port 9101 → portainer-agent.host-1.example.com
                                              port 9102 → portainer-agent.host-2.example.com
                                              port 91xx → ...

Browser ──► Management UI (port 9200, loopback only)
                    ↓
           Unix socket /run/nginx-agent.sock
                    ↓
           Agent (root): writes /nginx/conf.d/ + reloads nginx
```

Both Portainer CE and the proxy run in the same Docker Compose stack on a shared private bridge network. Portainer connects to the proxy using the Docker service name and port (e.g. `http://portainer_mtls_proxy:9101`). Agent proxy ports are not exposed to the host. Only the management UI port is exposed, bound to the host loopback interface.

The web app never writes nginx configuration directly. All privileged operations are delegated to the agent via the Unix domain socket.

---

## Container Processes

The container runs three processes managed by **supervisord** (PID 1):

| Process | Role | User | Access |
|---|---|---|---|
| `nginx` | HTTP/WebSocket reverse proxy with mTLS to remotes | `nginx` | Dynamically configured ports (91xx) |
| `python3 /app/main.py` | Management web UI + REST API (Flask) | `www-data` | Fixed port `9200` |
| `python3 /app/agent.py` | Privileged config agent | `root` | Unix socket `/run/nginx-agent.sock` |

---

## Base Image & Packages

**Base image:** `nginx:alpine`

**Additional packages installed via `apk`:**
- `supervisor`
- `python3`
- `py3-flask` (or Flask installed via pip if not available via apk)

No PHP, no fcgiwrap, no additional runtimes beyond the above.

---

## File Structure

```
/
├── app/
│   ├── main.py                  # Flask web UI + REST API (runs as www-data)
│   ├── agent.py                 # Privileged config agent (runs as root)
│   ├── nginx_manager.py         # nginx config generation + reload logic (imported by agent)
│   └── templates/
│       └── index.html           # Single-page management UI
├── nginx/
│   ├── nginx.conf               # Base nginx config (static, includes conf.d/*)
│   └── conf.d/                  # Dynamically generated per-mapping server blocks
│       ├── 9101.conf
│       └── 9102.conf
├── certs/                       # Mounted read-only from host
│   ├── client.cert
│   └── client.key
├── data/
│   └── mappings.json            # Persisted mapping configuration
├── etc/
│   └── supervisord.conf         # Supervisor process definitions
└── run/
    └── nginx-agent.sock         # Unix domain socket (created at runtime by agent)
```

---

## Dockerfile

- Based on `nginx:alpine`
- Installs `supervisor`, `python3`, and Flask via `apk` or `pip`
- Creates `www-data` user for the web app process
- Copies all application files into the image
- Creates `/nginx/conf.d/`, `/data/`, `/run/`, and `/certs/` directories as needed
- Sets `CMD ["supervisord", "-c", "/etc/supervisord.conf"]`

---

## supervisord Configuration (`/etc/supervisord.conf`)

- Runs `supervisord` in nodaemon mode, logging to stdout
- Defines three programs:
  - **nginx**: runs `/usr/sbin/nginx -g "daemon off;"`, autorestart enabled, stdout/stderr to console
  - **agent**: runs `python3 /app/agent.py` as `root`, autorestart enabled, stdout/stderr to console
  - **webapp**: runs `python3 /app/main.py` as `www-data`, autorestart enabled, stdout/stderr to console

---

## Mapping Configuration (`/data/mappings.json`)

Persists the port-to-remote-URL mappings across container restarts.

**Schema:**
```json
{
  "mappings": [
    {
      "port": 9101,
      "name": "hetzner-1",
      "remote_url": "https://portainer-agent.hetzner-1.example.com"
    },
    {
      "port": 9102,
      "name": "world4you-1",
      "remote_url": "https://portainer-agent.world4you-1.example.com"
    }
  ]
}
```

**Fields:**
- `port` (integer): the local port nginx will listen on for this mapping
- `name` (string): a human-readable label for the remote agent
- `remote_url` (string): the full HTTPS URL of the remote Apache reverse proxy endpoint

---

## nginx Configuration

### `/nginx/nginx.conf`

Static base configuration. Must include:
```nginx
include /nginx/conf.d/*.conf;
```
Never modified at runtime.

### `/nginx/conf.d/<port>.conf`

One file per mapping, generated dynamically by the agent. Each file contains a single `server` block:

```nginx
server {
    listen <port>;

    location / {
        proxy_pass                    <remote_url>;
        proxy_ssl_certificate         /certs/client.cert;
        proxy_ssl_certificate_key     /certs/client.key;

        # WebSocket support (required for Portainer exec, logs, stats)
        proxy_http_version            1.1;
        proxy_set_header              Upgrade    $http_upgrade;
        proxy_set_header              Connection "upgrade";

        proxy_set_header              Host       $proxy_host;
        proxy_read_timeout            3600s;
        proxy_send_timeout            3600s;
    }
}
```

nginx uses the system CA bundle to verify remote server certificates (Let's Encrypt is trusted by default in Alpine). No custom CA configuration is required.

---

## Agent (`/app/agent.py`)

### Responsibilities
- Run as `root`
- Listen on Unix domain socket `/run/nginx-agent.sock`
- Accept connections exclusively from the `www-data` web app process
- Receive instructions to add, update, or remove per-mapping nginx config files
- Validate any new or modified nginx config using `nginx -t` before writing to disk
- Write validated config fragments to `/nginx/conf.d/<port>.conf`
- Remove config files for deleted mappings
- Reload nginx via `nginx -s reload` after any configuration change
- Return a success or error response to the caller

### Socket Permissions
After creating the socket, the agent must:
- `chmod 660` the socket
- `chown root:www-data` the socket

This ensures only the `www-data` process can connect.

### Protocol
Simple line-based text protocol over the Unix socket:

**Requests from web app to agent:**

1. **Write config:**
   - Lines of the nginx config block to write
   - Terminated by a single line containing only `END`
   - Includes metadata identifying the target port (e.g. as the first line, or as a defined header line in the protocol)

2. **Delete config:**
   - A command to remove the config file for a given port (e.g. `DELETE <port>`)
   - Terminated by `END`

**Response from agent to web app:**
- `OK` on success
- `ERROR: <message>` on failure (including nginx -t output on validation failure)

### Security Requirements
- Always run `nginx -t` using a temporary file **before** writing any config to the live conf.d directory
- Never pass received content directly to a shell command (no shell=True interpolation)
- Reject any request whose config does not pass `nginx -t` validation
- Only process requests arriving via the permission-restricted Unix socket

---

## nginx Manager (`/app/nginx_manager.py`)

A helper module imported by the agent. Responsibilities:
- Generate the nginx `server` block content for a given mapping (port + remote_url)
- Determine the correct config file path (`/nginx/conf.d/<port>.conf`)
- Provide utility functions for writing, deleting, validating (via `nginx -t`), and reloading nginx config

---

## Web App / REST API (`/app/main.py`)

### Responsibilities
- Run as `www-data`
- Serve the management UI and REST API using Flask
- Listen on port `9200`
- Read current mappings from `/data/mappings.json`
- On configuration changes, send the appropriate instruction to the agent via the Unix socket
- Persist mapping changes to `/data/mappings.json`
- Display success or error feedback in the UI

### Routes

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serve the single-page management UI |
| `GET` | `/api/mappings` | Return all current mappings as JSON |
| `POST` | `/api/mappings` | Add a new mapping |
| `DELETE` | `/api/mappings/<port>` | Remove a mapping by port |
| `GET` | `/api/mappings/<port>/ping` | Test connectivity to the remote agent for a given port |

### POST `/api/mappings` Request Body
```json
{
  "port": 9103,
  "name": "my-server",
  "remote_url": "https://portainer-agent.my-server.example.com"
}
```
Port is optional — if omitted, the next available port starting from 9101 is automatically assigned.

### Agent Communication
The web app communicates with the agent by:
- Connecting to the Unix socket at `/run/nginx-agent.sock`
- Sending the appropriate request (new config block or delete command) followed by `END`
- Reading and returning the `OK` or `ERROR: <message>` response

The web app never writes nginx config files directly.

### Ping Endpoint
The ping endpoint (`GET /api/mappings/<port>/ping`) tests whether the remote agent at the configured URL is reachable and responding, using a simple HTTPS request with the client certificate. The result is returned inline to the UI.

---

## Management Web UI (`/app/templates/index.html`)

Single-page interface served at port 9200:

- Table of current mappings with columns: port, name, remote URL, status indicator
- "Add" form with fields: name (required), remote URL (required), port (optional override)
- Delete button per row — calls `DELETE /api/mappings/<port>`
- Ping button per row — calls `GET /api/mappings/<port>/ping` and displays result inline
- Status indicators auto-refresh every 30 seconds
- Displays success or error messages for all operations

No authentication. Access control relies entirely on the loopback binding.

---

## Docker Compose Integration

Add to the existing Portainer `docker-compose.yml`:

```yaml
services:
  portainer_mtls_proxy:
    image: docker-registry.example.com/portainer-mtls-proxy:latest
    container_name: portainer_mtls_proxy
    restart: unless-stopped
    ports:
      - "127.0.0.1:9200:9200"    # Management UI — loopback only, not accessible remotely
    volumes:
      - /path/to/certs:/certs:ro  # TLS client certificate and key, read-only
      - proxy_data:/data           # Persistent mapping configuration
    networks:
      - portainer_net
```

Add `proxy_data:` to the top-level `volumes:` section.

Agent proxy ports (91xx) are **not** declared under `ports:` — they are only reachable within the Docker Compose network by service name.

**Portainer environment configuration:**

| Environment name | URL in Portainer |
|---|---|
| hetzner-1 | `http://portainer_mtls_proxy:9101` |
| world4you-1 | `http://portainer_mtls_proxy:9102` |

---

## Port Convention

| Port | Purpose |
|---|---|
| 9200 | Management web UI (exposed on host loopback only) |
| 9101 | First agent proxy (internal Docker network only) |
| 9102 | Second agent proxy (internal Docker network only) |
| 91xx | Additional agent proxies (internal Docker network only) |

---

## Security Considerations

- **Socket access**: The agent Unix socket is `chmod 660`, owned `root:www-data`. Only the `www-data` web app process can connect. No other process can issue privileged commands.
- **Config validation**: All nginx configuration changes are validated with `nginx -t` using a temporary file before being written to the live configuration directory.
- **No shell injection**: Received config content is never interpolated into shell commands. Subprocess calls use argument lists, not shell strings.
- **Least privilege**: The web app runs as `www-data` with no write access to nginx config files or certificate files.
- **Certificates**: The client certificate and key are mounted read-only into `/certs/`. The proxy never modifies them.
- **Network exposure**: Agent proxy ports are not exposed to the host. Only the management UI port is exposed, bound exclusively to `127.0.0.1`.
- **No UI authentication**: The management UI has no login. Security relies on the loopback-only binding — it is not accessible from outside the host.
- **TLS verification**: nginx verifies remote server certificates using the Alpine system CA bundle. Let's Encrypt certificates are trusted by default. No custom CA configuration is needed.
- **Input handling**: All input submitted via the web UI or REST API must be treated as untrusted. Config content must only be passed to `nginx -t` via a temporary file.

---

## Build & Distribution

- A `Dockerfile` builds the image from `nginx:alpine` with all required packages and application files
- The image is published to a private container registry (e.g. `docker-registry.example.com/portainer-mtls-proxy:latest`)
- Versioned tags are recommended for production deployments (e.g. `portainer-mtls-proxy:1.0.0`)

---

## Summary of Components

| Component | Language/Runtime | User | Purpose |
|---|---|---|---|
| nginx | nginx (alpine) | `nginx` | HTTP/WebSocket reverse proxy with mTLS to remotes |
| `agent.py` | Python 3 | `root` | Privileged config writer and nginx reloader |
| `nginx_manager.py` | Python 3 | `root` (via agent) | nginx config generation and reload utilities |
| `main.py` | Python 3 / Flask | `www-data` | Management UI and REST API |
| `index.html` | HTML/JS | — | Browser-based management interface |
| supervisord | supervisor (alpine) | `root` (PID 1) | Process manager for all container processes |

