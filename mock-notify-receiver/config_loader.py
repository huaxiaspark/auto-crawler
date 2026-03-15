import os
import yaml


def load_config(path: str = None) -> dict:
    if path is None:
        path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return _expand_env(raw)


def _expand_env(obj):
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(i) for i in obj]
    if isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
        key = obj[2:-1]
        return os.environ.get(key, obj)
    return obj
