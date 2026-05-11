# Project Status

The repository has been initialized locally and connected to the GitHub repository `jdeepwell/portainer-agent-proxy`. The current project state is documentation-only: `Spec.md` defines the target architecture, `Claude.md` defines repository working conventions, and `.gitignore` covers macOS filesystem metadata. No application code, Docker image files, nginx configuration, supervisor configuration, tests, or compose examples have been implemented yet.

## Implementation Plan

### 1. Project foundation

- Create the runtime directory structure described in the specification: `app/`, `app/templates/`, `nginx/`, `nginx/conf.d/`, and `etc/`.
- Add the container entrypoint configuration files: `Dockerfile`, `/etc/supervisord.conf`, and static `/nginx/nginx.conf`.
- Ensure the image creates required runtime directories (`/data`, `/run`, `/certs`, `/nginx/conf.d`) and runs `supervisord` as PID 1.

### 2. nginx configuration manager

- Implement `app/nginx_manager.py` with focused helpers for mapping validation, nginx server-block generation, config file path resolution, config validation, atomic writes, deletion, and nginx reloads.
- Keep subprocess calls shell-free and argument-list based.
- Validate generated configuration with `nginx -t` before promoting changes into the live `/nginx/conf.d/` directory.

### 3. Privileged agent

- Implement `app/agent.py` as the root process that owns `/run/nginx-agent.sock`.
- Set socket permissions to `0660` and ownership to `root:www-data`.
- Implement the line-based protocol for writing and deleting per-port nginx config fragments.
- Return `OK` on success and `ERROR: <message>` with useful validation/reload output on failure.

### 4. Flask management API

- Implement `app/main.py` as the unprivileged web app running on port `9200`.
- Add JSON persistence for `/data/mappings.json`, including creation of a default empty structure when missing.
- Implement the REST routes from the spec: list mappings, add mapping, delete mapping, and ping a mapping.
- On add/delete, communicate with the agent over the Unix socket and only persist state after the agent operation succeeds.
- Support automatic port assignment starting at `9101` when the user does not provide a port.

### 5. Management UI

- Implement `app/templates/index.html` as a single-page management interface.
- Provide the mappings table, add form, delete action, ping action, inline status feedback, and 30-second status refresh.
- Keep the UI simple and operational, since this is an internal loopback-only admin surface.

### 6. Container and deployment integration

- Add a sample Docker Compose snippet or example file showing the Portainer integration, loopback-only UI port binding, cert mount, data volume, and shared Docker network.
- Confirm that agent proxy ports are only available inside the Docker network and are not exposed on the host.

### 7. Verification

- Add focused tests where practical for mapping validation, nginx config generation, persistence behavior, and socket protocol handling.
- Build the Docker image locally.
- Run the container with mounted test certificates/data and verify that all three supervised processes start.
- Verify nginx config generation, `nginx -t` validation failure behavior, reload behavior, mapping persistence, UI/API flows, and ping behavior.

## Current Next Step

Begin implementation with the project foundation: add the directory structure, Dockerfile, static nginx config, supervisor config, and the initial Python module skeletons.
