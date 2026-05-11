# Project Status

The repository has been initialized locally and connected to the GitHub repository `jdeepwell/portainer-agent-proxy`. The project now has its container foundation in place: `Dockerfile`, `.dockerignore`, `etc/supervisord.conf`, static `nginx/nginx.conf`, the `app/` Python module skeletons, the initial management UI template, and the tracked `nginx/conf.d/` runtime directory placeholder.

The specification now also requires the finished Docker image to be published through GitHub Container Registry as `ghcr.io/jdeepwell/portainer-agent-proxy`, with the local Portainer Docker Compose stack pulling that image directly.

The foundation image has been smoke-tested locally: the image builds, supervisor starts nginx, the root privileged agent, and the `www-data` Flask webapp, nginx configuration validates, and `/api/health` responds successfully over a loopback-bound host port.

The nginx configuration manager is implemented in `app/nginx_manager.py`. It validates mapping inputs, generates deterministic per-port nginx server blocks, validates candidate config sets with `nginx -t` before writing live files, performs atomic writes for generated or pre-rendered config content, supports validated deletes, and reloads nginx with shell-free subprocess calls. Focused unit tests cover mapping validation, config generation, write/delete behavior, failed validation behavior, and reload command execution.

The privileged agent is implemented in `app/agent.py`. It owns `/run/nginx-agent.sock`, restricts access to `root:www-data` with mode `0660`, implements the line-based `WRITE <port>` and `DELETE <port>` protocol terminated by `END`, delegates all config mutations to the nginx manager, reloads nginx only after successful mutations, and returns plain `OK` or `ERROR: <message>` responses. Agent tests cover protocol parsing, write/delete execution, failure handling, socket responses, and socket permission setup.

The Flask management API is implemented in `app/main.py`. It serves the management UI shell and health endpoint, exposes mapping list/add/delete/ping REST routes, persists normalized mappings in `/data/mappings.json`, creates a default empty mapping file when needed, auto-assigns ports from `9101`, delegates nginx writes and deletes to the privileged agent over `/run/nginx-agent.sock`, and only persists changes after successful agent operations. The ping endpoint uses Python's standard HTTPS client with the mounted client certificate and key. API tests cover persistence, automatic port selection, duplicate rejection, agent failure handling, delete behavior, ping responses, socket communication, and client-certificate setup.

The management UI is implemented in `app/templates/index.html` as a single-page internal admin surface. It loads mappings from the REST API, shows the current mapping table, provides the add form with optional port override, supports per-row ping and delete actions, displays inline feedback, tracks API health, and refreshes health and mapping status every 30 seconds. A Flask template test covers the rendered UI shell.

## Implementation Plan

### 1. Project foundation

Status: complete.

- Runtime directory structure is represented in the repository.
- Container entrypoint files are present: `Dockerfile`, `etc/supervisord.conf`, and `nginx/nginx.conf`.
- The image creates required runtime directories (`/data`, `/run`, `/certs`, `/nginx/conf.d`) and runs `supervisord` as PID 1.

### 2. nginx configuration manager

Status: complete.

- `app/nginx_manager.py` implements focused helpers for mapping validation, nginx server-block generation, config file path resolution, config validation, atomic writes, deletion, and nginx reloads.
- Subprocess calls are shell-free and argument-list based.
- Generated configuration is validated with `nginx -t` before being promoted into the live `/nginx/conf.d/` directory.
- `tests/test_nginx_manager.py` covers the manager behavior with the Python standard-library `unittest` framework.

### 3. Privileged agent

Status: complete.

- `app/agent.py` runs as the root process that owns `/run/nginx-agent.sock`.
- The socket is permissioned as `0660` and owned by `root:www-data`.
- The agent implements the line-based protocol for writing and deleting per-port nginx config fragments.
- It returns `OK` on success and `ERROR: <message>` with useful validation/reload output on failure.
- `tests/test_agent.py` covers protocol parsing, execution, error handling, socket responses, and permission setup.

### 4. Flask management API

Status: complete.

- `app/main.py` implements the unprivileged web app running on port `9200`.
- JSON persistence for `/data/mappings.json` is implemented, including creation of a default empty structure when missing.
- REST routes are implemented for listing mappings, adding mappings, deleting mappings, and pinging a mapping.
- Add/delete operations communicate with the privileged agent over the Unix socket and persist state only after the agent operation succeeds.
- Automatic port assignment starts at `9101` when the user does not provide a port.
- `tests/test_main.py` covers the API behavior and agent-socket integration points.

### 5. Management UI

Status: complete.

- `app/templates/index.html` implements the single-page management interface.
- The UI provides the mappings table, add form, delete action, ping action, inline status feedback, and 30-second status refresh.
- Keep the UI simple and operational, since this is an internal loopback-only admin surface.

### 6. Container and deployment integration

Status: pending.

- Add a sample Docker Compose snippet or example file showing the Portainer integration, loopback-only UI port binding, cert mount, data volume, and shared Docker network.
- Add the GitHub Actions workflow that builds and publishes the Docker image to GHCR.
- Confirm that agent proxy ports are only available inside the Docker network and are not exposed on the host.

### 7. Verification

Status: in progress across implementation steps.

- Focused tests cover mapping validation, nginx config generation, config writes/deletes, reload command execution, and agent socket protocol handling.
- The Docker image builds locally.
- A disposable container run verifies that supervisor starts nginx, the root agent, and the `www-data` Flask app.
- Runtime verification confirms `/api/health`, agent socket ownership/mode, malformed request handling, `DELETE` handling, `WRITE` handling, generated config creation, and `nginx -t` success after a real agent write.
- Access checks confirm that the host does not publish agent port `9101`, `www-data` can connect to the socket, and the `nginx` user is denied socket access.
- API tests run successfully inside the project Docker image where Flask is installed.
- A disposable container HTTP smoke test verifies health, default mapping-file creation, mapping add through the real Flask app and agent socket, generated nginx config creation, `nginx -t`, mapping deletion, config removal, and persisted JSON cleanup.
- The local Python test suite passes with the Flask-specific test module skipped when Flask is not installed locally.
- The rendered management UI is smoke-tested through Flask and served successfully from a disposable container.

## Current Next Step

Implement container and deployment integration.
