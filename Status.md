# Project Status

The repository has been initialized locally and connected to the GitHub repository `jdeepwell/portainer-agent-proxy`. The project now has its container foundation in place: `Dockerfile`, `.dockerignore`, `etc/supervisord.conf`, static `nginx/nginx.conf`, the `app/` Python module skeletons, the initial management UI template, and the tracked `nginx/conf.d/` runtime directory placeholder.

The specification now also requires the finished Docker image to be published through GitHub Container Registry as `ghcr.io/jdeepwell/portainer-agent-proxy`, with the local Portainer Docker Compose stack pulling that image directly.

The foundation image has been smoke-tested locally: the image builds, supervisor starts nginx, the root agent skeleton, and the `www-data` Flask webapp, nginx configuration validates, and `/api/health` responds successfully over a loopback-bound host port.

The nginx configuration manager is implemented in `app/nginx_manager.py`. It validates mapping inputs, generates deterministic per-port nginx server blocks, validates candidate config sets with `nginx -t` before writing live files, performs atomic writes, supports validated deletes, and reloads nginx with shell-free subprocess calls. Focused unit tests cover mapping validation, config generation, write/delete behavior, failed validation behavior, and reload command execution.

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

Status: next.

- Implement `app/agent.py` as the root process that owns `/run/nginx-agent.sock`.
- Set socket permissions to `0660` and ownership to `root:www-data`.
- Implement the line-based protocol for writing and deleting per-port nginx config fragments.
- Return `OK` on success and `ERROR: <message>` with useful validation/reload output on failure.

### 4. Flask management API

Status: pending.

- Implement `app/main.py` as the unprivileged web app running on port `9200`.
- Add JSON persistence for `/data/mappings.json`, including creation of a default empty structure when missing.
- Implement the REST routes from the spec: list mappings, add mapping, delete mapping, and ping a mapping.
- On add/delete, communicate with the agent over the Unix socket and only persist state after the agent operation succeeds.
- Support automatic port assignment starting at `9101` when the user does not provide a port.

### 5. Management UI

Status: pending.

- Implement `app/templates/index.html` as a single-page management interface.
- Provide the mappings table, add form, delete action, ping action, inline status feedback, and 30-second status refresh.
- Keep the UI simple and operational, since this is an internal loopback-only admin surface.

### 6. Container and deployment integration

Status: pending.

- Add a sample Docker Compose snippet or example file showing the Portainer integration, loopback-only UI port binding, cert mount, data volume, and shared Docker network.
- Add the GitHub Actions workflow that builds and publishes the Docker image to GHCR.
- Confirm that agent proxy ports are only available inside the Docker network and are not exposed on the host.

### 7. Verification

Status: in progress across implementation steps.

- Add focused tests where practical for mapping validation, nginx config generation, persistence behavior, and socket protocol handling.
- Build the Docker image locally.
- Run the container with mounted test certificates/data and verify that all three supervised processes start.
- Verify nginx config generation, `nginx -t` validation failure behavior, reload behavior, mapping persistence, UI/API flows, and ping behavior.

## Current Next Step

Implement the privileged agent in `app/agent.py`.
