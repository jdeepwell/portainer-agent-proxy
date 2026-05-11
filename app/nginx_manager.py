"""nginx configuration helpers for Portainer agent mappings."""

from __future__ import annotations

import os
import grp
import shutil
import ssl
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse


CONF_DIR = Path("/nginx/conf.d")
CERT_PATH = Path("/certs/client.cert")
KEY_PATH = Path("/certs/client.key")
DATA_CERT_DIR = Path("/data/certs")
UPLOADED_CERT_PATH = DATA_CERT_DIR / "client.cert"
UPLOADED_KEY_PATH = DATA_CERT_DIR / "client.key"
NGINX_BIN = "/usr/sbin/nginx"
NGINX_GROUP = "nginx"
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


class CertificateValidationError(NginxManagerError, ValueError):
    """Raised when an uploaded certificate/key pair is invalid."""


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


def active_client_cert_paths(
    *,
    uploaded_cert_path: Path | str | None = None,
    uploaded_key_path: Path | str | None = None,
    fallback_cert_path: Path | str | None = None,
    fallback_key_path: Path | str | None = None,
) -> tuple[Path, Path]:
    """Return uploaded certificate paths when complete, otherwise mounted fallback paths."""

    uploaded_cert = Path(uploaded_cert_path or UPLOADED_CERT_PATH)
    uploaded_key = Path(uploaded_key_path or UPLOADED_KEY_PATH)
    if uploaded_cert.exists() and uploaded_key.exists():
        return uploaded_cert, uploaded_key
    return Path(fallback_cert_path or CERT_PATH), Path(fallback_key_path or KEY_PATH)


def generate_server_block(
    mapping: Mapping | dict,
    *,
    cert_path: Path | str | None = None,
    key_path: Path | str | None = None,
) -> str:
    """Generate a deterministic nginx server block for a mapping."""

    normalized = normalize_mapping(mapping)
    if cert_path is None or key_path is None:
        active_cert_path, active_key_path = active_client_cert_paths()
        cert_path = active_cert_path if cert_path is None else cert_path
        key_path = active_key_path if key_path is None else key_path

    return f"""server {{
    listen {normalized.port};

    location / {{
        proxy_pass                    {normalized.remote_url};
        proxy_ssl_certificate         {Path(cert_path)};
        proxy_ssl_certificate_key     {Path(key_path)};
        proxy_ssl_server_name         on;
        proxy_ssl_name                $proxy_host;

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


def validate_certificate_pair(certificate: str, private_key: str) -> None:
    """Validate that uploaded PEM content can be loaded as a client cert/key pair."""

    certificate = _normalize_pem_content(certificate, "certificate")
    private_key = _normalize_pem_content(private_key, "private key")

    if "-----BEGIN CERTIFICATE-----" not in certificate:
        raise CertificateValidationError("client certificate must be PEM encoded")
    if "-----BEGIN " not in private_key or "PRIVATE KEY-----" not in private_key:
        raise CertificateValidationError("client key must be PEM encoded")
    if "-----BEGIN ENCRYPTED PRIVATE KEY-----" in private_key:
        raise CertificateValidationError("encrypted private keys are not supported")

    with tempfile.TemporaryDirectory(prefix="client-cert-") as temp_dir:
        temp_path = Path(temp_dir)
        cert_file = temp_path / "client.cert"
        key_file = temp_path / "client.key"
        cert_file.write_text(certificate, encoding="utf-8")
        key_file.write_text(private_key, encoding="utf-8")
        try:
            ssl.create_default_context().load_cert_chain(str(cert_file), str(key_file))
        except (OSError, ssl.SSLError) as exc:
            raise CertificateValidationError(f"certificate/key pair is invalid: {exc}") from exc


def install_client_certificates(
    certificate: str,
    private_key: str,
    *,
    cert_path: Path | str = UPLOADED_CERT_PATH,
    key_path: Path | str = UPLOADED_KEY_PATH,
    nginx_group: str = NGINX_GROUP,
) -> tuple[Path, Path]:
    """Validate and atomically install uploaded client certificate files."""

    certificate = _normalize_pem_content(certificate, "certificate")
    private_key = _normalize_pem_content(private_key, "private key")
    validate_certificate_pair(certificate, private_key)

    cert_target = Path(cert_path)
    key_target = Path(key_path)
    if cert_target.parent != key_target.parent:
        raise CertificateValidationError("certificate and key must share a directory")

    cert_target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(cert_target.parent, 0o755)

    group_id = _group_id(nginx_group)
    _atomic_write_sensitive_file(cert_target, certificate, mode=0o644, group_id=group_id)
    _atomic_write_sensitive_file(key_target, private_key, mode=0o640, group_id=group_id)
    return cert_target, key_target


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


def _normalize_pem_content(content: str, label: str) -> str:
    if not isinstance(content, str):
        raise CertificateValidationError(f"{label} must be text")
    value = content.strip()
    if not value:
        raise CertificateValidationError(f"{label} is required")
    return f"{value}\n"


def _group_id(group_name: str) -> int:
    try:
        return grp.getgrnam(group_name).gr_gid
    except KeyError:
        return -1


def _atomic_write_sensitive_file(target: Path, content: str, *, mode: int, group_id: int) -> None:
    fd, temp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temp_name, 0, group_id)
        os.chmod(temp_name, mode)
        os.replace(temp_name, target)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


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
