"""Management web UI and REST API entrypoint."""

from __future__ import annotations

import base64
import socket
import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, render_template, request

try:
    import nginx_manager
except ModuleNotFoundError:
    from app import nginx_manager


SOCKET_PATH = Path("/run/nginx-agent.sock")
AGENT_TIMEOUT_SECONDS = 10
PING_TIMEOUT_SECONDS = 5
MAX_CERT_UPLOAD_BYTES = 256 * 1024

app = Flask(__name__)


class ApiError(Exception):
    """Base error for API operations."""

    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class AgentError(ApiError):
    """Raised when the privileged agent rejects or cannot handle a request."""

    status_code = 502


class ConfigReadError(ApiError):
    """Raised when managed nginx mapping configs cannot be read."""

    status_code = 500


@app.errorhandler(ApiError)
def handle_api_error(error: ApiError):
    return jsonify({"error": str(error)}), error.status_code


@app.errorhandler(nginx_manager.MappingValidationError)
def handle_mapping_validation_error(error: nginx_manager.MappingValidationError):
    return jsonify({"error": str(error)}), 400


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/mappings")
def list_mappings():
    return jsonify({"mappings": load_mappings()})


@app.post("/api/mappings")
def add_mapping():
    payload = parse_json_body()
    mappings = load_mappings()
    mapping = build_mapping(payload, mappings)
    config_content = nginx_manager.generate_server_block(mapping)

    send_agent_request(f"WRITE {mapping['port']}\n{config_content}END\n")

    return jsonify({"mapping": mapping}), 201


@app.delete("/api/mappings/<port>")
def delete_mapping(port):
    safe_port = nginx_manager.validate_port(port)
    mappings = load_mappings()
    if not any(mapping["port"] == safe_port for mapping in mappings):
        raise ApiError("mapping not found", 404)

    send_agent_request(f"DELETE {safe_port}\nEND\n")

    return jsonify({"status": "deleted", "port": safe_port})


@app.get("/api/mappings/<port>/ping")
def ping_mapping(port):
    safe_port = nginx_manager.validate_port(port)
    mapping = find_mapping(load_mappings(), safe_port)
    if mapping is None:
        raise ApiError("mapping not found", 404)

    result = ping_remote(mapping["remote_url"])
    return jsonify({"port": safe_port, "remote_url": mapping["remote_url"], **result})


@app.get("/api/certificates/status")
def get_certificate_status():
    return jsonify(certificate_status())


@app.post("/api/certificates")
def upload_certificates():
    certificate = read_uploaded_text("client_cert", "client certificate")
    private_key = read_uploaded_text("client_key", "client key")
    validate_uploaded_pem_shape(certificate, private_key)

    send_agent_request(build_install_certificates_request(certificate, private_key))
    rewrite_mapping_configs(load_mappings())

    return jsonify({"status": "uploaded", "certificate": certificate_status()})


def parse_json_body() -> dict:
    if not request.is_json:
        raise ApiError("request body must be JSON")

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("request body must be a JSON object")
    return payload


def read_uploaded_text(field_name: str, label: str) -> str:
    uploaded = request.files.get(field_name)
    if uploaded is None or uploaded.filename == "":
        raise ApiError(f"{label} file is required")

    data = uploaded.stream.read(MAX_CERT_UPLOAD_BYTES + 1)
    if len(data) > MAX_CERT_UPLOAD_BYTES:
        raise ApiError(f"{label} file is too large")

    try:
        value = data.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ApiError(f"{label} file must be UTF-8 text") from exc

    if not value:
        raise ApiError(f"{label} file is required")
    return value


def validate_uploaded_pem_shape(certificate: str, private_key: str) -> None:
    if "-----BEGIN CERTIFICATE-----" not in certificate:
        raise ApiError("client certificate must be PEM encoded")
    if "-----BEGIN " not in private_key or "PRIVATE KEY-----" not in private_key:
        raise ApiError("client key must be PEM encoded")
    if "-----BEGIN ENCRYPTED PRIVATE KEY-----" in private_key:
        raise ApiError("encrypted private keys are not supported")


def load_mappings(conf_dir: Path | str | None = None) -> list[dict]:
    try:
        mappings = nginx_manager.list_mapping_configs(conf_dir or nginx_manager.CONF_DIR)
    except (OSError, nginx_manager.NginxManagerError) as exc:
        raise ConfigReadError(f"could not read managed nginx mappings: {exc}") from exc
    return [mapping_to_dict(mapping) for mapping in mappings]


def build_mapping(payload: dict, existing_mappings: list[dict]) -> dict:
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ApiError("name is required")

    existing_ports = {mapping["port"] for mapping in existing_mappings}
    port = payload.get("port")
    if port in (None, ""):
        port = next_available_port(existing_ports)
    else:
        port = nginx_manager.validate_port(port)
        if port in existing_ports:
            raise ApiError("port already exists", 409)

    mapping = nginx_manager.normalize_mapping(
        {
            "port": port,
            "name": name,
            "remote_url": payload.get("remote_url"),
        }
    )
    return mapping_to_dict(mapping)


def next_available_port(used_ports: set[int]) -> int:
    for port in range(nginx_manager.PORT_MIN, nginx_manager.PORT_MAX + 1):
        if port not in used_ports:
            return port
    raise ApiError("no available proxy ports", 409)


def find_mapping(mappings: list[dict], port: int) -> dict | None:
    return next((mapping for mapping in mappings if mapping["port"] == port), None)


def mapping_to_dict(mapping: nginx_manager.Mapping) -> dict:
    return {
        "port": mapping.port,
        "name": mapping.name,
        "remote_url": mapping.remote_url,
    }


def build_install_certificates_request(certificate: str, private_key: str) -> str:
    cert_payload = base64.b64encode(certificate.encode("utf-8")).decode("ascii")
    key_payload = base64.b64encode(private_key.encode("utf-8")).decode("ascii")
    return f"INSTALL_CERTS\nCERT {cert_payload}\nKEY {key_payload}\nEND\n"


def rewrite_mapping_configs(mappings: list[dict]) -> None:
    for mapping in mappings:
        content = nginx_manager.generate_server_block(mapping)
        send_agent_request(f"WRITE {mapping['port']}\n{content}END\n")


def certificate_status() -> dict:
    uploaded = nginx_manager.UPLOADED_CERT_PATH.exists() and nginx_manager.UPLOADED_KEY_PATH.exists()
    fallback = nginx_manager.CERT_PATH.exists() and nginx_manager.KEY_PATH.exists()
    cert_path, key_path = nginx_manager.active_client_cert_paths()
    source = "uploaded" if uploaded else "mounted" if fallback else "missing"
    return {
        "source": source,
        "uploaded": uploaded,
        "mounted": fallback,
        "active_cert_path": str(cert_path),
        "active_key_path": str(key_path),
    }


def send_agent_request(message: str, socket_path: Path | str | None = None) -> None:
    socket_path = Path(socket_path or SOCKET_PATH)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(AGENT_TIMEOUT_SECONDS)
            client.connect(str(socket_path))
            client.sendall(message.encode("utf-8"))
            client.shutdown(socket.SHUT_WR)
            response = client.makefile("r", encoding="utf-8", newline="").readline().strip()
    except OSError as exc:
        raise AgentError(f"agent request failed: {exc}") from exc

    if response == "OK":
        return
    if response.startswith("ERROR: "):
        raise AgentError(response.removeprefix("ERROR: "))
    raise AgentError(f"unexpected agent response: {response or 'empty response'}")


def ping_remote(remote_url: str) -> dict:
    context = ssl.create_default_context()
    cert_path, key_path = nginx_manager.active_client_cert_paths()
    try:
        context.load_cert_chain(str(cert_path), str(key_path))
        request_context = Request(remote_url, method="GET")
        with urlopen(request_context, timeout=PING_TIMEOUT_SECONDS, context=context) as response:
            return {"status": "ok", "reachable": True, "code": response.status}
    except HTTPError as exc:
        if exc.code == 403:
            return {
                "status": "ok",
                "reachable": True,
                "code": exc.code,
                "message": "agent rejected unsigned ping request",
            }
        return {"status": "http_error", "reachable": True, "code": exc.code, "error": str(exc)}
    except (OSError, URLError, ssl.SSLError) as exc:
        return {"status": "error", "reachable": False, "error": str(exc)}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9200)
