"""配置读取。

配置放 server/config.ini，模板是同目录的 config.example.ini（提交进库）。
真实配置在 .gitignore 里排除，不进版本库。

规则：
- 任何值都可以写成 ${VAR}，表示这个值启动时从环境变量 VAR 取。
  密钥就用这个写法，密钥不落盘。
- 文件不存在、键缺失、引用的环境变量没设、provider 不认识，
  都直接报错退出。不给默认值，问题在启动时就暴露，不带进运行里。
"""
import os
import re
from configparser import ConfigParser, SectionProxy
from dataclasses import dataclass
from pathlib import Path

# 匹配 ${VAR} 形式的环境变量引用。
_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass
class LLMConfig:
    """读好之后的配置，字段都已经是最终值，不再有 ${VAR}。"""

    provider: str  # "anthropic" 或 "openai"
    base_url: str
    api_key: str
    model_id: str
    max_tokens: int
    # 思考强度：off / low / medium / high / max，或空字符串。
    # 空 = 请求里不带这个参数。这一个键允许缺省，见 load_config 里的说明。
    effort: str = ""


def load_config(path: Path = Path("config.ini")) -> LLMConfig:
    """读 config.ini。路径相对启动时的工作目录，也就是 server/。"""
    if not path.exists():
        raise SystemExit(f"找不到 {path}。先复制 config.example.ini 为 config.ini 再填。")

    parser = ConfigParser()
    parser.read(path, encoding="utf-8")
    if "llm" not in parser:
        raise SystemExit(f"{path} 缺少 [llm] 这一段。对照 config.example.ini。")
    section = parser["llm"]

    provider = _value(section, "provider", path)
    if provider not in ("anthropic", "openai"):
        raise SystemExit(f"provider 只能是 anthropic 或 openai，现在写的是 {provider}。")

    max_tokens_raw = _value(section, "max_tokens", path)
    if not max_tokens_raw.isdigit():
        raise SystemExit(f"max_tokens 要是数字，现在写的是 {max_tokens_raw}。")

    # effort 允许缺省或留空：意思是「不传」，不是「忘了配」。
    # 中转背后的模型会换，有的根本没有思考强度这个参数，强制填写会让
    # 换模型时启动失败。写了就必须是那五个词之一。
    effort = section["effort"].strip() if "effort" in section else ""
    if effort and effort not in ("off", "low", "medium", "high", "max"):
        raise SystemExit(f"effort 只能是 off / low / medium / high / max 或留空，现在写的是 {effort}。")

    return LLMConfig(
        provider=provider,
        base_url=_value(section, "base_url", path),
        api_key=_value(section, "api_key", path),
        model_id=_value(section, "model_id", path),
        max_tokens=int(max_tokens_raw),
        effort=effort,
    )


def _value(section: SectionProxy, key: str, path: Path) -> str:
    """取出一个键的最终值：缺键报错，${VAR} 展开成环境变量的值。"""
    if key not in section:
        raise SystemExit(f"{path} 的 [llm] 缺少 {key}。对照 config.example.ini 补上。")
    raw = section[key].strip()
    match = _ENV_REF.match(raw)
    if match:
        var = match.group(1)
        if var not in os.environ:
            raise SystemExit(f"{path} 的 {key} 引用了环境变量 {var}，但它没有设置。")
        return os.environ[var]
    return raw
