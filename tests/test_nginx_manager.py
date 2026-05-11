import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import nginx_manager


def successful_run(args, **kwargs):
    return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="ok")


class NginxManagerTests(unittest.TestCase):
    def test_generate_server_block_contains_required_proxy_settings(self):
        content = nginx_manager.generate_server_block(
            {
                "port": 9101,
                "name": "hetzner-1",
                "remote_url": "https://portainer-agent.example.com",
            }
        )

        self.assertIn("listen 9101;", content)
        self.assertIn("proxy_pass                    https://portainer-agent.example.com;", content)
        self.assertIn("proxy_ssl_certificate         /certs/client.cert;", content)
        self.assertIn("proxy_ssl_certificate_key     /certs/client.key;", content)
        self.assertIn("proxy_set_header              Upgrade    $http_upgrade;", content)
        self.assertIn('proxy_set_header              Connection "upgrade";', content)
        self.assertTrue(content.endswith("\n"))

    def test_remote_url_must_be_https_without_injection_surface(self):
        invalid_urls = [
            "http://portainer-agent.example.com",
            "https://user:pass@portainer-agent.example.com",
            "https://portainer-agent.example.com?x=1",
            "https://portainer-agent.example.com#fragment",
            "https://portainer-agent.example.com:not-a-port",
            "https://portainer-agent.example.com;\nreturn 200",
        ]

        for remote_url in invalid_urls:
            with self.subTest(remote_url=remote_url):
                with self.assertRaises(nginx_manager.MappingValidationError):
                    nginx_manager.normalize_remote_url(remote_url)

    def test_port_must_follow_proxy_port_convention(self):
        for port in (9100, 9200, 65535, "not-a-port", True):
            with self.subTest(port=port):
                with self.assertRaises(nginx_manager.MappingValidationError):
                    nginx_manager.validate_port(port)

        self.assertEqual(9101, nginx_manager.validate_port("9101"))
        self.assertEqual(9199, nginx_manager.validate_port(9199))

    def test_config_path_uses_validated_port_filename(self):
        self.assertEqual(
            Path("/tmp/conf.d/9102.conf"),
            nginx_manager.config_path("9102", "/tmp/conf.d"),
        )

    @patch("app.nginx_manager.subprocess.run", side_effect=successful_run)
    def test_write_mapping_config_validates_before_atomic_write(self, run_mock):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = nginx_manager.write_mapping_config(
                {"port": 9103, "remote_url": "https://agent.example.com"},
                conf_dir=temp_dir,
                nginx_bin="nginx",
            )

            self.assertEqual(Path(temp_dir) / "9103.conf", target)
            self.assertIn("proxy_pass                    https://agent.example.com;", target.read_text())
            self.assertEqual(["nginx", "-t"], run_mock.call_args.args[0][:2])
            self.assertFalse(run_mock.call_args.kwargs.get("shell", False))

    @patch("app.nginx_manager.subprocess.run")
    def test_write_mapping_config_does_not_write_when_validation_fails(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            args=["nginx"],
            returncode=1,
            stdout="",
            stderr="bad config",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(nginx_manager.NginxValidationError):
                nginx_manager.write_mapping_config(
                    {"port": 9104, "remote_url": "https://agent.example.com"},
                    conf_dir=temp_dir,
                    nginx_bin="nginx",
                )

            self.assertFalse((Path(temp_dir) / "9104.conf").exists())

    @patch("app.nginx_manager.subprocess.run", side_effect=successful_run)
    def test_delete_mapping_config_validates_without_target_then_deletes(self, _run_mock):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "9105.conf"
            target.write_text("server { listen 9105; }\n", encoding="utf-8")

            deleted = nginx_manager.delete_mapping_config(9105, conf_dir=temp_dir, nginx_bin="nginx")

            self.assertEqual(target, deleted)
            self.assertFalse(target.exists())

    @patch("app.nginx_manager.subprocess.run")
    def test_reload_nginx_uses_argument_list(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            args=["nginx"],
            returncode=0,
            stdout="",
            stderr="",
        )

        nginx_manager.reload_nginx(nginx_bin="nginx")

        self.assertEqual(["nginx", "-s", "reload"], run_mock.call_args.args[0])
        self.assertFalse(run_mock.call_args.kwargs.get("shell", False))


if __name__ == "__main__":
    unittest.main()
