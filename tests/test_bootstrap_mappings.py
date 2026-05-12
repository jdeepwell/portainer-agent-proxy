import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import bootstrap_mappings


class BootstrapMappingsTests(unittest.TestCase):
    @patch("app.bootstrap_mappings.nginx_manager.write_mapping_config")
    def test_restore_persisted_mappings_rebuilds_configs(self, write_mock):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            data_path = temp_path / "mappings.json"
            conf_dir = temp_path / "conf.d"
            conf_dir.mkdir()
            (conf_dir / "9101.conf").write_text("stale", encoding="utf-8")
            (conf_dir / "custom.conf").write_text("keep", encoding="utf-8")
            data_path.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {
                                "port": 9101,
                                "name": "one",
                                "remote_url": "https://one.example.com",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            count = bootstrap_mappings.restore_persisted_mappings(
                data_path=data_path,
                conf_dir=conf_dir,
                nginx_bin="nginx",
            )

            self.assertEqual(1, count)
            self.assertFalse((conf_dir / "9101.conf").exists())
            self.assertTrue((conf_dir / "custom.conf").exists())
            write_mock.assert_called_once()
            mapping = write_mock.call_args.args[0]
            self.assertEqual(9101, mapping.port)
            self.assertEqual("https://one.example.com", mapping.remote_url)
            self.assertEqual("nginx", write_mock.call_args.kwargs["nginx_bin"])

    @patch("app.bootstrap_mappings.nginx_manager.write_mapping_config")
    def test_restore_without_mapping_file_cleans_generated_configs(self, write_mock):
        with tempfile.TemporaryDirectory() as temp_dir:
            conf_dir = Path(temp_dir) / "conf.d"
            conf_dir.mkdir()
            (conf_dir / "9102.conf").write_text("stale", encoding="utf-8")

            count = bootstrap_mappings.restore_persisted_mappings(
                data_path=Path(temp_dir) / "missing.json",
                conf_dir=conf_dir,
            )

            self.assertEqual(0, count)
            self.assertFalse((conf_dir / "9102.conf").exists())
            write_mock.assert_not_called()

    def test_restore_rejects_malformed_mapping_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_path = Path(temp_dir) / "mappings.json"
            data_path.write_text(json.dumps({"mappings": "bad"}), encoding="utf-8")

            with self.assertRaises(bootstrap_mappings.BootstrapError):
                bootstrap_mappings.restore_persisted_mappings(data_path=data_path)


if __name__ == "__main__":
    unittest.main()
