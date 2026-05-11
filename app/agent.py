"""Privileged nginx configuration agent."""

from __future__ import annotations

import grp
import base64
import os
import signal
import socket
import stat
from dataclasses import dataclass
from pathlib import Path

try:
    import nginx_manager
except ModuleNotFoundError:
    from app import nginx_manager


SOCKET_PATH = Path("/run/nginx-agent.sock")
SOCKET_GROUP = "www-data"
SOCKET_MODE = 0o660
MAX_REQUEST_BYTES = 1024 * 1024
LISTEN_BACKLOG = 8


class AgentProtocolError(ValueError):
    """Raised when the web app sends a malformed agent request."""


@dataclass(frozen=True)
class AgentRequest:
    """A parsed privileged agent request."""

    action: str
    port: int | None = None
    content: str = ""
    private_key: str = ""


def parse_request(lines: list[str]) -> AgentRequest:
    """Parse request lines without the terminating END marker."""

    if not lines:
        raise AgentProtocolError("empty request")

    command, *payload = lines
    parts = command.split()
    if not parts:
        raise AgentProtocolError("empty command")

    action = parts[0].upper()

    if action == "INSTALL_CERTS":
        if len(parts) != 1:
            raise AgentProtocolError("INSTALL_CERTS does not accept command arguments")
        certificate, private_key = parse_certificate_payload(payload)
        return AgentRequest(action="INSTALL_CERTS", content=certificate, private_key=private_key)

    if len(parts) != 2:
        raise AgentProtocolError("first line must be WRITE <port>, DELETE <port>, or INSTALL_CERTS")

    port = nginx_manager.validate_port(parts[1])

    if action == "DELETE":
        if any(line.strip() for line in payload):
            raise AgentProtocolError("DELETE does not accept a payload")
        return AgentRequest(action="DELETE", port=port)

    if action == "WRITE":
        content = "\n".join(payload).strip()
        if not content:
            raise AgentProtocolError("WRITE requires config content")
        return AgentRequest(action="WRITE", port=port, content=content)

    raise AgentProtocolError("unknown command")


def execute_request(request: AgentRequest) -> None:
    """Run a parsed request through the nginx manager."""

    if request.action == "WRITE":
        if request.port is None:
            raise AgentProtocolError("WRITE requires a port")
        nginx_manager.write_config_content(request.port, request.content)
        nginx_manager.reload_nginx()
        return

    if request.action == "DELETE":
        if request.port is None:
            raise AgentProtocolError("DELETE requires a port")
        nginx_manager.delete_mapping_config(request.port)
        nginx_manager.reload_nginx()
        return

    if request.action == "INSTALL_CERTS":
        nginx_manager.install_client_certificates(request.content, request.private_key)
        return

    raise AgentProtocolError("unknown command")


def parse_certificate_payload(lines: list[str]) -> tuple[str, str]:
    """Parse base64-encoded certificate upload payload lines."""

    values: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            continue
        key, separator, value = line.partition(" ")
        if not separator:
            raise AgentProtocolError("certificate payload lines must be CERT <base64> or KEY <base64>")
        key = key.upper()
        if key not in {"CERT", "KEY"}:
            raise AgentProtocolError("certificate payload lines must be CERT <base64> or KEY <base64>")
        if key in values:
            raise AgentProtocolError(f"duplicate {key} payload")
        values[key] = decode_base64_text(value, key.lower())

    if "CERT" not in values or "KEY" not in values:
        raise AgentProtocolError("INSTALL_CERTS requires CERT and KEY payloads")
    return values["CERT"], values["KEY"]


def decode_base64_text(value: str, label: str) -> str:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, ValueError) as exc:
        raise AgentProtocolError(f"{label} payload must be base64-encoded utf-8 text") from exc


def read_request_lines(connection: socket.socket) -> list[str]:
    """Read a line-based request from a socket until END."""

    reader = connection.makefile("r", encoding="utf-8", newline="")
    lines: list[str] = []
    byte_count = 0

    while True:
        raw_line = reader.readline()
        if raw_line == "":
            raise AgentProtocolError("request must end with END")

        byte_count += len(raw_line.encode("utf-8"))
        if byte_count > MAX_REQUEST_BYTES:
            raise AgentProtocolError("request is too large")

        line = raw_line.rstrip("\r\n")
        if line == "END":
            return lines
        lines.append(line)


def process_lines(lines: list[str]) -> str:
    """Process request lines and return a protocol response."""

    try:
        execute_request(parse_request(lines))
    except Exception as exc:
        return f"ERROR: {exc}"
    return "OK"


def handle_connection(connection: socket.socket) -> None:
    """Read one request from a client connection and write one response."""

    try:
        try:
            response = process_lines(read_request_lines(connection))
        except UnicodeDecodeError as exc:
            response = f"ERROR: invalid utf-8 request: {exc}"
        connection.sendall(f"{response}\n".encode("utf-8"))
    finally:
        connection.close()


def create_server_socket(socket_path: Path = SOCKET_PATH) -> socket.socket:
    """Create, bind, and permission the agent Unix socket."""

    if socket_path.exists():
        if stat.S_ISSOCK(socket_path.stat().st_mode):
            socket_path.unlink()
        else:
            raise RuntimeError(f"{socket_path} exists and is not a socket")

    socket_path.parent.mkdir(parents=True, exist_ok=True)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_path))
        group_id = grp.getgrnam(SOCKET_GROUP).gr_gid
        os.chown(socket_path, 0, group_id)
        os.chmod(socket_path, SOCKET_MODE)
        server.listen(LISTEN_BACKLOG)
        server.settimeout(1.0)
    except Exception:
        server.close()
        socket_path.unlink(missing_ok=True)
        raise

    return server


def serve_forever(server: socket.socket, socket_path: Path = SOCKET_PATH) -> None:
    """Accept and handle client connections until SIGTERM or SIGINT."""

    running = True

    def stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        while running:
            try:
                connection, _addr = server.accept()
            except TimeoutError:
                continue
            except socket.timeout:
                continue
            handle_connection(connection)
    finally:
        server.close()
        socket_path.unlink(missing_ok=True)


def main() -> None:
    serve_forever(create_server_socket())


if __name__ == "__main__":
    main()
