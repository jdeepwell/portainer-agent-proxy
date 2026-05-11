"""nginx configuration helpers for Portainer agent mappings."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse


CONF_DIR = Path("/nginx/conf.d")
CERT_PATH = Path("/certs/client.cert")
KEY_PATH = Path("/certs/client.key")
NGINX_BIN = "/usr/sbin/nginx"
PORT_MIN = 9101
PORT_MAX = 9199


class NginxManagerError(Exception):
    """Base error for nginx manager operations."""


class MappingValidationError(NginxManagerError, ValueError):
    """Raised when mapping input is invalid."""


class NginxValidationError(NginxManagerError, RuntimeError):
    """Raised when nginx rejects a generated configuration."""


class NginxReloadError(NginxManagerError, RuntimeError):
    """Raised when nginx cannot be reloaded."""


@dataclass(frozen=True)
class Mapping:
    """A single local-port to remote-agent mapping."""

    port: int
    remote_url: str
    name: str = ""


def validate_port(port: int | str) -> int:
    """Return a safe local proxy port."""

    if isinstance(port, bool):
        raise MappingValidationError("port must be an integer")

    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise MappingValidationError("port must be an integer") from exc

    if not PORT_MIN <= value <= PORT_MAX:
        raise MappingValidationError(f"port must be between {PORT_MIN} and {PORT_MAX}")

    return value


def normalize_remote_url(remote_url: str) -> str:
    """Return a sanitized HTTPS upstream URL suitable for nginx proxy_pass."""

    if not isinstance(remote_url, str):
        raise MappingValidationError("remote_url must be a string")

    value = remote_url.strip()
    if not value:
        raise MappingValidationError("remote_url is required")

    if any(char.isspace() for char in value):
        raise MappingValidationError("remote_url must not contain whitespace")

    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise MappingValidationError("remote_url must use https")
    if not parsed.hostname:
        raise MappingValidationError("remote_url must include a hostname")
    if parsed.username or parsed.password:
        raise MappingValidationError("remote_url must not include credentials")
    if parsed.query or parsed.fragment:
        raise MappingValidationError("remote_url must not include a query string or fragment")

    hostname = parsed.hostname
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise MappingValidationError("remote_url hostname is invalid") from exc

    try:
        port = parsed.port
    except ValueError as exc:
        raise MappingValidationError("remote_url port is invalid") from exc

    netloc = hostname
    if port is not None:
        netloc = f"{netloc}:{port}"

    path = parsed.path or ""
    return urlunparse(("https", netloc, path, "", "", ""))


def normalize_mapping(mapping: Mapping | dict) -> Mapping:
    """Validate and normalize mapping data."""

    if isinstance(mapping, Mapping):
        port = mapping.port
        remote_url = mapping.remote_url
        name = mapping.name
    elif isinstance(mapping, dict):
        port = mapping.get("port")
        remote_url = mapping.get("remote_url")
        name = mapping.get("name", "")
    else:
        raise MappingValidationError("mapping must be a Mapping or dict")

    return Mapping(
        port=validate_port(port),
        remote_url=normalize_remote_url(remote_url),
        name=str(name or "").strip(),
    )


def config_path(port: int | str, conf_dir: Path | str = CONF_DIR) -> Path:
    """Return the live nginx config path for a mapping port."""

    safe_port = validate_port(port)
    return Path(conf_dir) / f"{safe_port}.conf"


def generate_server_block(mapping: Mapping | dict) -> str:
    """Generate a deterministic nginx server block for a mapping."""

    normalized = normalize_mapping(mapping)
    return f"""server {{
    listen {normalized.port};

    location / {{
        proxy_pass                    {normalized.remote_url};
        proxy_ssl_certificate         {CERT_PATH};
        proxy_ssl_certificate_key     {KEY_PATH};

        proxy_http_version            1.1;
        proxy_set_header              Upgrade    $http_upgrade;
        proxy_set_header              Connection "upgrade";

        proxy_set_header              Host       $proxy_host;
        proxy_read_timeout            3600s;
        proxy_send_timeout            3600s;
    }}
}}
"""


def validate_config_set(
    *,
    conf_dir: Path | str = CONF_DIR,
    candidate_port: int | str | None = None,
    candidate_content: str | None = None,
    exclude_port: int | str | None = None,
    nginx_bin: str = NGINX_BIN,
) -> None:
    """Validate a temporary nginx config set without touching live files."""

    if candidate_port is not None:
        candidate_port = validate_port(candidate_port)
    if exclude_port is not None:
        exclude_port = validate_port(exclude_port)

    if candidate_content is not None and candidate_port is None:
        raise MappingValidationError("candidate_port is required with candidate_content")

    source_dir = Path(conf_dir)
    with tempfile.TemporaryDirectory(prefix="nginx-manager-") as temp_root:
        temp_root_path = Path(temp_root)
        temp_conf_dir = temp_root_path / "conf.d"
        temp_conf_dir.mkdir()

        _copy_existing_configs(
            source_dir=source_dir,
            target_dir=temp_conf_dir,
            exclude_ports={port for port in (candidate_port, exclude_port) if port is not None},
        )

        if candidate_content is not None:
            (temp_conf_dir / f"{candidate_port}.conf").write_text(candidate_content, encoding="utf-8")

        test_config = temp_root_path / "nginx.conf"
        test_config.write_text(_test_nginx_config(temp_conf_dir, temp_root_path), encoding="utf-8")

        result = subprocess.run(
            [nginx_bin, "-t", "-c", str(test_config), "-p", "/"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
            raise NginxValidationError(output or "nginx configuration validation failed")


def write_mapping_config(
    mapping: Mapping | dict,
    *,
    conf_dir: Path | str = CONF_DIR,
    nginx_bin: str = NGINX_BIN,
) -> Path:
    """Validate and atomically write a mapping config file."""

    normalized = normalize_mapping(mapping)
    content = generate_server_block(normalized)
    return write_config_content(
        normalized.port,
        content,
        conf_dir=conf_dir,
        nginx_bin=nginx_bin,
    )


def write_config_content(
    port: int | str,
    content: str,
    *,
    conf_dir: Path | str = CONF_DIR,
    nginx_bin: str = NGINX_BIN,
) -> Path:
    """Validate and atomically write a pre-rendered mapping config file."""

    safe_port = validate_port(port)
    if not isinstance(content, str):
        raise MappingValidationError("config content must be a string")

    content = content.strip()
    if not content:
        raise MappingValidationError("config content is required")
    content = f"{content}\n"

    validate_config_set(
        conf_dir=conf_dir,
        candidate_port=safe_port,
        candidate_content=content,
        nginx_bin=nginx_bin,
    )

    target = config_path(safe_port, conf_dir)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{safe_port}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise

    return target


def delete_mapping_config(
    port: int | str,
    *,
    conf_dir: Path | str = CONF_DIR,
    nginx_bin: str = NGINX_BIN,
) -> Path:
    """Validate and delete a mapping config file."""

    safe_port = validate_port(port)
    validate_config_set(conf_dir=conf_dir, exclude_port=safe_port, nginx_bin=nginx_bin)

    target = config_path(safe_port, conf_dir)
    target.unlink(missing_ok=True)
    return target


def reload_nginx(*, nginx_bin: str = NGINX_BIN) -> None:
    """Reload nginx after a successful configuration change."""

    result = subprocess.run(
        [nginx_bin, "-s", "reload"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        raise NginxReloadError(output or "nginx reload failed")


def _copy_existing_configs(source_dir: Path, target_dir: Path, exclude_ports: set[int]) -> None:
    if not source_dir.exists():
        return

    for source in sorted(source_dir.glob("*.conf")):
        try:
            source_port = int(source.stem)
        except ValueError:
            source_port = None
        if source_port in exclude_ports:
            continue
        shutil.copy2(source, target_dir / source.name)


def _test_nginx_config(conf_dir: Path, temp_root: Path) -> str:
    return f"""worker_processes auto;
pid {temp_root / "nginx.pid"};

events {{
    worker_connections 1024;
}}

http {{
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    sendfile on;
    keepalive_timeout 65;

    include {conf_dir}/*.conf;
}}
"""
