"""Management web UI and REST API entrypoint."""

from __future__ import annotations

import json
import os
import socket
import ssl
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, render_template, request

try:
    import nginx_manager
except ModuleNotFoundError:
    from app import nginx_manager


DATA_PATH = Path("/data/mappings.json")
SOCKET_PATH = Path("/run/nginx-agent.sock")
AGENT_TIMEOUT_SECONDS = 10
PING_TIMEOUT_SECONDS = 5

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


class StorageError(ApiError):
    """Raised when mapping persistence cannot be read or written."""

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
    mappings.append(mapping)
    save_mappings(mappings)

    return jsonify({"mapping": mapping}), 201


@app.delete("/api/mappings/<port>")
def delete_mapping(port):
    safe_port = nginx_manager.validate_port(port)
    mappings = load_mappings()
    if not any(mapping["port"] == safe_port for mapping in mappings):
        raise ApiError("mapping not found", 404)

    send_agent_request(f"DELETE {safe_port}\nEND\n")
    save_mappings([mapping for mapping in mappings if mapping["port"] != safe_port])

    return jsonify({"status": "deleted", "port": safe_port})


@app.get("/api/mappings/<port>/ping")
def ping_mapping(port):
    safe_port = nginx_manager.validate_port(port)
    mapping = find_mapping(load_mappings(), safe_port)
    if mapping is None:
        raise ApiError("mapping not found", 404)

    result = ping_remote(mapping["remote_url"])
    return jsonify({"port": safe_port, "remote_url": mapping["remote_url"], **result})


def parse_json_body() -> dict:
    if not request.is_json:
        raise ApiError("request body must be JSON")

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("request body must be a JSON object")
    return payload


def load_mappings(data_path: Path | str | None = None) -> list[dict]:
    path = Path(data_path or DATA_PATH)
    if not path.exists():
        save_mappings([], path)
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError(f"could not read mappings: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("mappings"), list):
        raise StorageError("mappings file must contain a mappings list")

    mappings = [mapping_to_dict(nginx_manager.normalize_mapping(item)) for item in data["mappings"]]
    return sorted(mappings, key=lambda item: item["port"])


def save_mappings(mappings: list[dict], data_path: Path | str | None = None) -> None:
    path = Path(data_path or DATA_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [mapping_to_dict(nginx_manager.normalize_mapping(item)) for item in mappings]
    normalized = sorted(normalized, key=lambda item: item["port"])
    data = {"mappings": normalized}

    fd, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except OSError as exc:
        Path(temp_name).unlink(missing_ok=True)
        raise StorageError(f"could not write mappings: {exc}") from exc


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
    try:
        context.load_cert_chain(str(nginx_manager.CERT_PATH), str(nginx_manager.KEY_PATH))
        request_context = Request(remote_url, method="GET")
        with urlopen(request_context, timeout=PING_TIMEOUT_SECONDS, context=context) as response:
            return {"status": "ok", "reachable": True, "code": response.status}
    except HTTPError as exc:
        return {"status": "error", "reachable": False, "code": exc.code, "error": str(exc)}
    except (OSError, URLError, ssl.SSLError) as exc:
        return {"status": "error", "reachable": False, "error": str(exc)}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9200)
