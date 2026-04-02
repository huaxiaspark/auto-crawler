import os
import re
import yaml
import logging

_logger = logging.getLogger(__name__)


def load_config(path: str) -> dict:
    """加载 YAML 配置，自动将 ${ENV_VAR} 占位符替换为对应环境变量值。
    若环境变量未设置，保留原始占位符字符串并记录 WARNING 日志。
    """
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    def _replace(m):
        var_name = m.group(1)
        val = os.environ.get(var_name)
        if val is None:
            _logger.warning(f"环境变量 {var_name} 未设置，配置中保留原始占位符")
            return m.group(0)
        return val

    raw = re.sub(r'\$\{(\w+)\}', _replace, raw)
    return yaml.safe_load(raw)
