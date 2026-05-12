"""Restore persisted mapping configs before nginx starts."""

from __future__ import annotations

import json
from pathlib import Path

try:
    import nginx_manager
except ModuleNotFoundError:
    from app import nginx_manager


DATA_PATH = Path("/data/mappings.json")


class BootstrapError(RuntimeError):
    """Raised when persisted mappings cannot be restored."""


def restore_persisted_mappings(
    *,
    data_path: Path | str = DATA_PATH,
    conf_dir: Path | str = nginx_manager.CONF_DIR,
    nginx_bin: str = nginx_manager.NGINX_BIN,
) -> int:
    """Regenerate live nginx config files from persisted mappings."""

    path = Path(data_path)
    if not path.exists():
        remove_generated_configs(conf_dir)
        return 0

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"could not read persisted mappings: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("mappings"), list):
        raise BootstrapError("persisted mappings file must contain a mappings list")

    mappings = [nginx_manager.normalize_mapping(item) for item in data["mappings"]]
    remove_generated_configs(conf_dir)

    for mapping in mappings:
        nginx_manager.write_mapping_config(mapping, conf_dir=conf_dir, nginx_bin=nginx_bin)

    return len(mappings)


def remove_generated_configs(conf_dir: Path | str = nginx_manager.CONF_DIR) -> None:
    """Remove generated mapping configs from a previous container start."""

    path = Path(conf_dir)
    if not path.exists():
        return

    for config in path.glob("*.conf"):
        try:
            port = int(config.stem)
        except ValueError:
            continue
        if nginx_manager.PORT_MIN <= port <= nginx_manager.PORT_MAX:
            config.unlink(missing_ok=True)


def main() -> None:
    count = restore_persisted_mappings()
    if count:
        print(f"Restored {count} persisted nginx mapping config(s).")


if __name__ == "__main__":
    main()
