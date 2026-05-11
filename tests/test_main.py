import json
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if importlib.util.find_spec("flask") is None:
    raise unittest.SkipTest("Flask is not installed in this Python environment")

from app import main


class MainApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_path = Path(self.temp_dir.name) / "mappings.json"
        self.data_patch = patch.object(main, "DATA_PATH", self.data_path)
        self.data_patch.start()
        main.app.config.update(TESTING=True)
        self.client = main.app.test_client()

    def tearDown(self):
        self.data_patch.stop()
        self.temp_dir.cleanup()

    def write_mappings(self, mappings):
        self.data_path.write_text(json.dumps({"mappings": mappings}), encoding="utf-8")

    def read_mappings(self):
        return json.loads(self.data_path.read_text(encoding="utf-8"))["mappings"]

    def test_get_mappings_returns_empty_list_when_file_is_missing(self):
        response = self.client.get("/api/mappings")

        self.assertEqual(200, response.status_code)
        self.assertEqual({"mappings": []}, response.get_json())
        self.assertEqual([], self.read_mappings())

    @patch("app.main.send_agent_request")
    def test_post_mapping_auto_assigns_port_calls_agent_and_persists(self, send_agent_mock):
        response = self.client.post(
            "/api/mappings",
            json={"name": "hetzner-1", "remote_url": "https://agent.example.com"},
        )

        self.assertEqual(201, response.status_code)
        self.assertEqual(
            {
                "port": 9101,
                "name": "hetzner-1",
                "remote_url": "https://agent.example.com",
            },
            response.get_json()["mapping"],
        )
        message = send_agent_mock.call_args.args[0]
        self.assertTrue(message.startswith("WRITE 9101\nserver {"))
        self.assertTrue(message.endswith("END\n"))
        self.assertEqual([response.get_json()["mapping"]], self.read_mappings())

    @patch("app.main.send_agent_request")
    def test_post_mapping_uses_next_available_port(self, _send_agent_mock):
        self.write_mappings(
            [
                {"port": 9101, "name": "one", "remote_url": "https://one.example.com"},
                {"port": 9103, "name": "three", "remote_url": "https://three.example.com"},
            ]
        )

        response = self.client.post(
            "/api/mappings",
            json={"name": "two", "remote_url": "https://two.example.com"},
        )

        self.assertEqual(201, response.status_code)
        self.assertEqual(9102, response.get_json()["mapping"]["port"])

    @patch("app.main.send_agent_request")
    def test_post_mapping_rejects_duplicate_port(self, send_agent_mock):
        self.write_mappings([{"port": 9104, "name": "one", "remote_url": "https://one.example.com"}])

        response = self.client.post(
            "/api/mappings",
            json={"port": 9104, "name": "duplicate", "remote_url": "https://two.example.com"},
        )

        self.assertEqual(409, response.status_code)
        self.assertEqual({"error": "port already exists"}, response.get_json())
        send_agent_mock.assert_not_called()

    @patch("app.main.send_agent_request")
    def test_post_mapping_does_not_persist_when_agent_fails(self, send_agent_mock):
        send_agent_mock.side_effect = main.AgentError("bad nginx config")

        response = self.client.post(
            "/api/mappings",
            json={"name": "broken", "remote_url": "https://broken.example.com"},
        )

        self.assertEqual(502, response.status_code)
        self.assertEqual({"error": "bad nginx config"}, response.get_json())
        self.assertEqual([], self.read_mappings())

    @patch("app.main.send_agent_request")
    def test_delete_mapping_calls_agent_and_persists_removed_mapping(self, send_agent_mock):
        self.write_mappings(
            [
                {"port": 9101, "name": "one", "remote_url": "https://one.example.com"},
                {"port": 9102, "name": "two", "remote_url": "https://two.example.com"},
            ]
        )

        response = self.client.delete("/api/mappings/9101")

        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "deleted", "port": 9101}, response.get_json())
        send_agent_mock.assert_called_once_with("DELETE 9101\nEND\n")
        self.assertEqual(
            [{"port": 9102, "name": "two", "remote_url": "https://two.example.com"}],
            self.read_mappings(),
        )

    @patch("app.main.send_agent_request")
    def test_delete_mapping_does_not_persist_when_agent_fails(self, send_agent_mock):
        self.write_mappings([{"port": 9101, "name": "one", "remote_url": "https://one.example.com"}])
        send_agent_mock.side_effect = main.AgentError("reload failed")

        response = self.client.delete("/api/mappings/9101")

        self.assertEqual(502, response.status_code)
        self.assertEqual({"error": "reload failed"}, response.get_json())
        self.assertEqual(
            [{"port": 9101, "name": "one", "remote_url": "https://one.example.com"}],
            self.read_mappings(),
        )

    @patch("app.main.ping_remote")
    def test_ping_mapping_returns_connectivity_result(self, ping_remote_mock):
        self.write_mappings([{"port": 9101, "name": "one", "remote_url": "https://one.example.com"}])
        ping_remote_mock.return_value = {"status": "ok", "reachable": True, "code": 200}

        response = self.client.get("/api/mappings/9101/ping")

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "port": 9101,
                "remote_url": "https://one.example.com",
                "status": "ok",
                "reachable": True,
                "code": 200,
            },
            response.get_json(),
        )
        ping_remote_mock.assert_called_once_with("https://one.example.com")

    def test_send_agent_request_accepts_ok_response(self):
        server, client = make_agent_socket_pair(b"OK\n")
        with patch("app.main.socket.socket", return_value=client):
            main.send_agent_request("DELETE 9101\nEND\n")

        self.assertEqual(b"DELETE 9101\nEND\n", server.received)

    def test_send_agent_request_raises_for_agent_error_response(self):
        _server, client = make_agent_socket_pair(b"ERROR: no thanks\n")
        with patch("app.main.socket.socket", return_value=client):
            with self.assertRaises(main.AgentError) as context:
                main.send_agent_request("DELETE 9101\nEND\n")

        self.assertEqual("no thanks", str(context.exception))

    @patch("app.main.urlopen")
    @patch("app.main.ssl.create_default_context")
    def test_ping_remote_uses_client_certificate(self, create_context_mock, urlopen_mock):
        context = Mock()
        create_context_mock.return_value = context
        response = Mock()
        response.status = 204
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        urlopen_mock.return_value = response

        result = main.ping_remote("https://agent.example.com")

        self.assertEqual({"status": "ok", "reachable": True, "code": 204}, result)
        context.load_cert_chain.assert_called_once_with("/certs/client.cert", "/certs/client.key")
        self.assertEqual("https://agent.example.com", urlopen_mock.call_args.args[0].full_url)


class FakeAgentClient:
    def __init__(self, response: bytes):
        self.response = response
        self.sent = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return None

    def settimeout(self, _timeout):
        return None

    def connect(self, _socket_path):
        return None

    def sendall(self, data: bytes):
        self.sent.extend(data)

    def shutdown(self, _how):
        return None

    def makefile(self, *_args, **_kwargs):
        return FakeAgentReader(self.response)


class FakeAgentReader:
    def __init__(self, response: bytes):
        self.response = response

    def readline(self):
        return self.response.decode("utf-8")


class FakeAgentServer:
    def __init__(self, client: FakeAgentClient):
        self.client = client

    @property
    def received(self):
        return bytes(self.client.sent)


def make_agent_socket_pair(response: bytes):
    client = FakeAgentClient(response)
    return FakeAgentServer(client), client


if __name__ == "__main__":
    unittest.main()
