# Project Status

The repository has been initialized locally and connected to the GitHub repository `jdeepwell/portainer-agent-proxy`. The project now has its container foundation in place: `Dockerfile`, `.dockerignore`, `etc/entrypoint.sh`, `etc/supervisord.conf`, static `nginx/nginx.conf`, the `app/` Python modules, the management UI template, and the tracked `nginx/conf.d/` runtime directory placeholder.

The Docker image is published through GitHub Container Registry as `ghcr.io/jdeepwell/portainer-agent-proxy`. Version `v0.1.1` is the current release and includes HTTPS-facing agent proxy ports, persistent generated inbound TLS certificates, and updated ping handling for expected Portainer Agent `403` responses. The local Portainer Docker Compose stack can pull the image directly from GHCR.

The foundation image has been smoke-tested locally: the image builds, supervisor starts nginx, the root privileged agent, and the `www-data` Flask webapp, nginx configuration validates, `/api/health` responds successfully over a loopback-bound host port, and a generated mapping answers over HTTPS using the proxy's inbound server certificate.

The nginx configuration manager is implemented in `app/nginx_manager.py`. It validates mapping inputs, resolves the active upstream client certificate paths, generates deterministic per-port HTTPS nginx server blocks with upstream TLS SNI enabled for Apache name-based TLS virtual hosts, references the inbound server certificate persisted under `/data/server-certs/`, validates candidate config sets with `nginx -t` before writing live files, performs atomic writes for generated or pre-rendered config content, supports validated deletes, installs uploaded client certificate/key pairs under `/data/certs/`, and reloads nginx with shell-free subprocess calls. Focused unit tests cover mapping validation, active certificate resolution, HTTPS config generation, write/delete behavior, failed validation behavior, uploaded certificate installation, and reload command execution.

The privileged agent is implemented in `app/agent.py`. It owns `/run/nginx-agent.sock`, restricts access to `root:www-data` with mode `0660`, implements the line-based `WRITE <port>`, `DELETE <port>`, and `INSTALL_CERTS` protocol terminated by `END`, delegates config and uploaded certificate mutations to the nginx manager, reloads nginx only after successful nginx config mutations, and returns plain `OK` or `ERROR: <message>` responses. Agent tests cover protocol parsing, certificate payload parsing, write/delete/cert execution, failure handling, socket responses, and socket permission setup.

The Flask management API is implemented in `app/main.py`. It serves the management UI shell and health endpoint, exposes mapping list/add/delete/ping REST routes, exposes certificate status/upload routes, persists normalized mappings in `/data/mappings.json`, creates a default empty mapping file when needed, auto-assigns ports from `9101`, delegates nginx writes, deletes, and certificate installs to the privileged agent over `/run/nginx-agent.sock`, and only persists mapping changes after successful agent operations. The ping endpoint uses Python's standard HTTPS client with the active client certificate/key pair and treats HTTP error responses as reachable remote responses rather than transport failures; `403 Forbidden` from a Portainer Agent is reported as an OK reachability result because the UI probe is expected to be unsigned. API tests cover persistence, automatic port selection, duplicate rejection, certificate status/upload behavior, agent failure handling, delete behavior, ping responses including expected `403`, socket communication, and client-certificate setup.

The management UI is implemented in `app/templates/index.html` as a single-page internal admin surface. It loads mappings from the REST API, shows the current mapping table, provides the add form with optional port override, supports global client certificate/private-key upload, shows whether the active certificate source is uploaded or mounted, supports per-row ping and delete actions, displays inline feedback, tracks API health, and refreshes health, certificate status, and mapping status every 30 seconds. A Flask template test covers the rendered UI shell.

Global client certificate management is implemented for the upstream mTLS identity. Uploaded certificate/key pairs are validated by the root agent and persisted under `/data/certs/client.cert` and `/data/certs/client.key`; mounted `/certs/client.cert` and `/certs/client.key` remain supported as a fallback. The uploaded private key is installed as `root:nginx` with restrictive permissions, and the web app is a member of the `nginx` group so its ping endpoint can read the key without making it world-readable. Both mappings and uploaded certificates survive container recreation when `/data` is backed by a persistent Docker volume.

Inbound HTTPS certificate management is implemented for the certificate presented by the proxy to Portainer on `91xx` mapping ports. On first startup, the entrypoint generates `/data/server-certs/proxy.crt` and `/data/server-certs/proxy.key` with SANs for `portainer_mtls_proxy`, `localhost`, and `127.0.0.1`; additional SANs can be supplied through `PROXY_TLS_SAN`. Existing user-provided certificate/key files at the same paths are preserved, and startup fails if only one half of the pair exists.

Container and deployment integration is implemented. The repository includes `compose.example.yml` for Portainer-side deployment, `README.md` documents GHCR image usage, upstream client certificate persistence, inbound HTTPS certificate persistence, and Portainer Agent addresses using `portainer_mtls_proxy:<port>` or `https://` when a full URL is required. `.github/workflows/docker-image.yml` builds, tests, tags, and publishes the image to GHCR on `main`, semantic version tags, pull requests, and manual dispatch.

## Implementation Plan

### 1. Project foundation

Status: complete.

- Runtime directory structure is represented in the repository.
- Container entrypoint files are present: `Dockerfile`, `etc/entrypoint.sh`, `etc/supervisord.conf`, and `nginx/nginx.conf`.
- The image creates required runtime directories (`/data`, `/data/server-certs`, `/run`, `/certs`, `/nginx/conf.d`) and runs `supervisord` as PID 1.
- The runtime entrypoint prepares `/data` ownership for fresh Docker volumes, generates the persisted inbound HTTPS certificate/key pair when missing, validates that user-supplied inbound cert files are complete, and then starts supervisord.

### 2. nginx configuration manager

Status: complete.

- `app/nginx_manager.py` implements focused helpers for mapping validation, HTTPS nginx server-block generation, config file path resolution, config validation, atomic writes, deletion, and nginx reloads.
- The manager resolves the active client certificate paths, preferring uploaded `/data/certs/` files and falling back to mounted `/certs/` files.
- Generated proxy config listens with HTTPS on each mapping port, presents `/data/server-certs/proxy.crt` and `/data/server-certs/proxy.key` to Portainer, and enables upstream TLS SNI with `$proxy_host`.
- Uploaded certificate/key pairs are validated and installed atomically under `/data/certs/`.
- Subprocess calls are shell-free and argument-list based.
- Generated configuration is validated with `nginx -t` before being promoted into the live `/nginx/conf.d/` directory.
- `tests/test_nginx_manager.py` covers the manager behavior with the Python standard-library `unittest` framework.

### 3. Privileged agent

Status: complete.

- `app/agent.py` runs as the root process that owns `/run/nginx-agent.sock`.
- The socket is permissioned as `0660` and owned by `root:www-data`.
- The agent implements the line-based protocol for writing and deleting per-port nginx config fragments.
- The agent implements certificate upload via `INSTALL_CERTS`.
- It returns `OK` on success and `ERROR: <message>` with useful validation/reload output on failure.
- `tests/test_agent.py` covers protocol parsing, execution, error handling, socket responses, and permission setup.

### 4. Flask management API

Status: complete.

- `app/main.py` implements the unprivileged web app running on port `9200`.
- JSON persistence for `/data/mappings.json` is implemented, including creation of a default empty structure when missing.
- REST routes are implemented for listing mappings, adding mappings, deleting mappings, and pinging a mapping.
- REST routes are implemented for reporting certificate status and uploading the global client certificate/key pair.
- Add/delete operations communicate with the privileged agent over the Unix socket and persist state only after the agent operation succeeds.
- Certificate uploads communicate with the privileged agent over the Unix socket and trigger regeneration of existing mapping configs so nginx uses the active certificate paths.
- Automatic port assignment starts at `9101` when the user does not provide a port.
- Remote Agent ping treats `403 Forbidden` as an OK reachability result because it indicates that TLS/mTLS reached the Agent and the unsigned UI probe was rejected as expected.
- `tests/test_main.py` covers the API behavior and agent-socket integration points.

### 5. Management UI

Status: complete.

- `app/templates/index.html` implements the single-page management interface.
- The UI provides the mappings table, add form, delete action, ping action, inline status feedback, and 30-second status refresh; expected Agent `403` ping results display as OK.
- The UI provides a client certificate upload form and active certificate source/status display.
- Keep the UI simple and operational, since this is an internal loopback-only admin surface.

### 6. Container and deployment integration

Status: complete.

- `compose.example.yml` shows the Portainer integration with loopback-only UI port binding, persistent `/data` volume, optional mounted cert fallback, and an external shared Docker network.
- `.github/workflows/docker-image.yml` builds the image, runs the test suite inside the image, logs in to GHCR, applies Docker metadata tags, and publishes the image.
- The workflow publishes `latest` for `main`, semantic version tags such as `v0.1.1` and `0.1.1`, branch tags, and SHA tags.
- `README.md` documents image usage, Compose setup, certificate persistence, and Portainer environment URLs.
- Agent proxy ports are not published in the example Compose file and remain available only inside the Docker network.

### 7. Verification

Status: in progress across implementation steps.

- Focused tests cover mapping validation, nginx config generation, config writes/deletes, reload command execution, and agent socket protocol handling.
- The Docker image builds locally.
- A disposable container run verifies that supervisor starts nginx, the root agent, and the `www-data` Flask app.
- Runtime verification confirms `/api/health`, agent socket ownership/mode, malformed request handling, `DELETE` handling, `WRITE` handling, generated config creation, and `nginx -t` success after a real agent write.
- Access checks confirm that the host does not publish agent port `9101`, `www-data` can connect to the socket, and the `nginx` user is denied socket access.
- API tests run successfully inside the project Docker image where Flask is installed.
- A disposable container smoke test verifies health, default mapping-file creation, mapping add through the real Flask app and agent socket, generated HTTPS nginx config creation, `nginx -t`, mapping deletion, config removal, and persisted JSON cleanup.
- The local Python test suite passes with the Flask-specific test module skipped when Flask is not installed locally.
- The rendered management UI is smoke-tested through Flask and served successfully from a disposable container.
- Named-volume runtime verification confirms `/data` ownership for `www-data`, uploaded certificate persistence under `/data/certs/`, private-key ownership/mode of `root:nginx:0640`, nginx-user key readability, generated configs using `/data/certs/`, and `nginx -t` success.
- Runtime HTTPS verification confirms `/data/server-certs/proxy.crt` and `/data/server-certs/proxy.key` are generated on first startup and that a generated mapping completes an HTTPS request with the expected self-signed certificate verification result.
- Live preview testing with the World4You remote confirms uploaded certificates are selected, the ping endpoint can read the uploaded key, upstream TLS reaches the remote, and the Portainer Agent returns the expected signed-request authorization response when reached without Portainer's signature headers.
- `docker compose -f compose.example.yml config` validates the example Compose file.
- The GHCR workflow configuration is in place and has previously succeeded on `main` and on tag `v0.1.0`; the `v0.1.1` release tag is expected to publish the updated image.

## Current Next Step

Use `ghcr.io/jdeepwell/portainer-agent-proxy:0.1.1`, `ghcr.io/jdeepwell/portainer-agent-proxy:v0.1.1`, or `ghcr.io/jdeepwell/portainer-agent-proxy:latest` in the local Portainer Docker Compose stack and validate a real Portainer environment connection through `portainer_mtls_proxy:9101`.
