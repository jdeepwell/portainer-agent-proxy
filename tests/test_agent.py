import base64
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import agent


class AgentTests(unittest.TestCase):
    def test_parse_write_request_preserves_config_content(self):
        request = agent.parse_request(
            [
                "WRITE 9101",
                "server {",
                "    listen 9101;",
                "}",
            ]
        )

        self.assertEqual("WRITE", request.action)
        self.assertEqual(9101, request.port)
        self.assertEqual("server {\n    listen 9101;\n}", request.content)

    def test_parse_delete_request(self):
        request = agent.parse_request(["DELETE 9102"])

        self.assertEqual("DELETE", request.action)
        self.assertEqual(9102, request.port)
        self.assertEqual("", request.content)

    def test_parse_install_certs_request(self):
        cert = "-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----"
        key = "-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----"
        request = agent.parse_request(
            [
                "INSTALL_CERTS",
                f"CERT {base64.b64encode(cert.encode()).decode()}",
                f"KEY {base64.b64encode(key.encode()).decode()}",
            ]
        )

        self.assertEqual("INSTALL_CERTS", request.action)
        self.assertIsNone(request.port)
        self.assertEqual(cert, request.content)
        self.assertEqual(key, request.private_key)

    def test_parse_rejects_malformed_request(self):
        invalid_requests = [
            [],
            ["WRITE 9101"],
            ["WRITE 9200", "server { listen 9200; }"],
            ["DELETE 9101", "unexpected"],
            ["UNKNOWN 9101"],
            ["INSTALL_CERTS", "CERT not-base64"],
        ]

        for lines in invalid_requests:
            with self.subTest(lines=lines):
                with self.assertRaises((agent.AgentProtocolError, agent.nginx_manager.MappingValidationError)):
                    agent.parse_request(lines)

    @patch("app.agent.nginx_manager.reload_nginx")
    @patch("app.agent.nginx_manager.write_config_content")
    def test_execute_write_request_writes_then_reloads(self, write_mock, reload_mock):
        request = agent.AgentRequest("WRITE", 9103, "server { listen 9103; }")

        agent.execute_request(request)

        write_mock.assert_called_once_with(9103, "server { listen 9103; }")
        reload_mock.assert_called_once_with()

    @patch("app.agent.nginx_manager.reload_nginx")
    @patch("app.agent.nginx_manager.delete_mapping_config")
    def test_execute_delete_request_deletes_then_reloads(self, delete_mock, reload_mock):
        request = agent.AgentRequest("DELETE", 9104)

        agent.execute_request(request)

        delete_mock.assert_called_once_with(9104)
        reload_mock.assert_called_once_with()

    @patch("app.agent.nginx_manager.install_client_certificates")
    def test_execute_install_certs_request_installs_without_reload(self, install_mock):
        request = agent.AgentRequest("INSTALL_CERTS", content="cert", private_key="key")

        agent.execute_request(request)

        install_mock.assert_called_once_with("cert", "key")

    @patch("app.agent.nginx_manager.reload_nginx")
    @patch("app.agent.nginx_manager.write_config_content")
    def test_failed_write_does_not_reload(self, write_mock, reload_mock):
        write_mock.side_effect = RuntimeError("bad config")

        response = agent.process_lines(["WRITE 9105", "server {"])

        self.assertEqual("ERROR: bad config", response)
        reload_mock.assert_not_called()

    @patch("app.agent.nginx_manager.reload_nginx")
    @patch("app.agent.nginx_manager.delete_mapping_config")
    def test_handle_connection_returns_ok(self, delete_mock, reload_mock):
        server, client = socket.socketpair()
        thread = threading.Thread(target=agent.handle_connection, args=(server,))
        thread.start()

        client.sendall(b"DELETE 9106\nEND\n")
        response = client.recv(1024)
        client.close()
        thread.join(timeout=2)

        self.assertEqual(b"OK\n", response)
        delete_mock.assert_called_once_with(9106)
        reload_mock.assert_called_once_with()

    @patch("app.agent.os.chmod")
    @patch("app.agent.os.chown")
    @patch("app.agent.grp.getgrnam")
    def test_create_server_socket_sets_socket_permissions(self, getgrnam_mock, chown_mock, chmod_mock):
        getgrnam_mock.return_value.gr_gid = 82

        with tempfile.TemporaryDirectory() as temp_dir:
            socket_path = Path(temp_dir) / "nginx-agent.sock"
            server = agent.create_server_socket(socket_path)
            try:
                self.assertTrue(socket_path.exists())
                chown_mock.assert_called_once_with(socket_path, 0, 82)
                chmod_mock.assert_called_once_with(socket_path, agent.SOCKET_MODE)
            finally:
                server.close()


if __name__ == "__main__":
    unittest.main()
