# portainer-agent-proxy
A proxy to connect a local Portainer instance to remote Portainer agents behind a reverse proxy, protecting the agents with mTLS (client cert).

## Image

The release image is published to GitHub Container Registry:

```text
ghcr.io/jdeepwell/portainer-agent-proxy:latest
```

Version tags are also supported when Git tags like `v1.0.0` are pushed.

## Compose Usage

Add the proxy to the same Docker network as Portainer CE. The management UI is bound to localhost only; agent proxy ports in the `91xx` range are only reachable inside the Docker network.

```yaml
services:
  portainer_mtls_proxy:
    image: ghcr.io/jdeepwell/portainer-agent-proxy:latest
    container_name: portainer_mtls_proxy
    restart: unless-stopped
    ports:
      - "127.0.0.1:9200:9200"
    volumes:
      - proxy_data:/data
    networks:
      - portainer_net

volumes:
  proxy_data:

networks:
  portainer_net:
    external: true
```

An editable example is available in `compose.example.yml`.

## Mapping Configuration

The mapping configuration is stored directly as managed nginx config files in the persistent `/data` volume:

```text
/data/nginx/conf.d/9101.conf
/data/nginx/conf.d/9102.conf
```

The management UI reads these files to display current mappings, and the privileged agent validates and writes them when mappings are added or removed. There is no separate mapping database or JSON file.

## Proxy HTTPS Certificate

Portainer talks to Agent endpoints over HTTPS. The proxy therefore listens with HTTPS on every configured `91xx` mapping port.

On first startup the container generates a self-signed server certificate and key at:

```text
/data/server-certs/proxy.crt
/data/server-certs/proxy.key
```

These files are stored in the persistent `/data` volume and are reused across container restarts and image upgrades. To use your own certificate, place a matching PEM certificate and unencrypted private key at those same paths before starting the container.

The generated certificate includes SANs for `portainer_mtls_proxy`, `localhost`, and `127.0.0.1`. If Portainer will connect using another Docker service alias or hostname, add extra SAN entries with `PROXY_TLS_SAN`:

```yaml
environment:
  PROXY_TLS_SAN: "DNS:my_proxy_alias,DNS:agent-proxy.local"
```

## Client Certificate

Open the management UI at:

```text
http://127.0.0.1:9200/
```

Upload the global client certificate and private key from the UI. They are stored in the persistent `/data` volume:

```text
/data/certs/client.cert
/data/certs/client.key
```

If you prefer mounting files instead of uploading them, mount a directory containing `client.cert` and `client.key` at `/certs:ro`; uploaded files in `/data/certs` take precedence when present.

## Portainer Environment URLs

After adding mappings in the UI, configure Portainer Agent environments using the proxy service name and internal mapping port. In Portainer's Agent address field, prefer the same style Portainer documents for normal Agents: host and port, without a protocol.

```text
portainer_mtls_proxy:9101
portainer_mtls_proxy:9102
```

If you are using an API or UI field that requires a full URL, use `https://`, not `http://`.

Do not publish `91xx` ports on the host. They are intended for Portainer-to-proxy HTTPS traffic inside the shared Docker network.
