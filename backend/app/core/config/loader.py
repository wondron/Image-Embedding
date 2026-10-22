from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from app.core.config.settings import settings


def parse_config_text(text: str, source: str = "configuration") -> dict[str, Any]:
    """Parse a YAML configuration document and require a mapping at its root."""
    config = yaml.safe_load(text)
    if not isinstance(config, dict):
        raise ValueError(f"{source} must contain a YAML mapping at the document root")
    return config


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    return parse_config_text(path.read_text(encoding="utf-8"), str(path))


def read_text_if_path(d: dict, key: str, new_key: str):
    path = d.get(key)
    if path:
        d[new_key] = Path(path).read_text(encoding="utf-8")
    return d


def prepare_config(config: dict[str, Any]) -> dict[str, Any]:
    """Apply the same local-path expansion to local and remote configurations."""
    config = deepcopy(config)
    for server_key in ("MLLM_SERVER", "DOUBLE_SERVER"):
        server_config = config.get(server_key)
        if isinstance(server_config, dict):
            config[server_key] = read_text_if_path(
                server_config, "prompt_path", "prompt"
            )
    return config


def load_config(yaml_path: str | Path | None = None) -> dict:
    yaml_path = yaml_path or settings.config.yaml_path
    return prepare_config(load_yaml(yaml_path))
