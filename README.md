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

After adding mappings in the UI, configure Portainer environments using the proxy service name and internal mapping port:

```text
http://portainer_mtls_proxy:9101
http://portainer_mtls_proxy:9102
```

Do not publish `91xx` ports on the host. They are intended for Portainer-to-proxy traffic inside the shared Docker network.
