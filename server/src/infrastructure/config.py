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
    # 模型流量绕过代理的主机列表（NO_PROXY 语义，逗号分隔，* = 全部绕过）。
    # 空 = 完全尊重环境里的代理变量，不替用户做决定。允许缺省。
    no_proxy: str = ""


@dataclass(frozen=True)
class SettingsSnapshot:
    """给界面看的配置，不含真实密钥。"""

    provider: str
    base_url: str
    model_id: str
    max_tokens: int
    effort: str
    api_key_configured: bool
    api_key_source: str


@dataclass(frozen=True)
class SettingsValues:
    """设置界面提交的值；api_key 为空表示保留原值。"""

    provider: str
    base_url: str
    model_id: str
    max_tokens: int
    effort: str = ""
    api_key: str | None = None


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

    # no_proxy 同样允许缺省或留空：默认尊重环境代理，不做任何绕行。
    # 值就是标准 NO_PROXY 的主机列表（逗号分隔，* 表示全部）。
    # 换行直接拒（多行不是一个合法的主机列表）；空格归一掉，
    # 存进 LLMConfig 的永远是干净的逗号列表。
    raw_no_proxy = section["no_proxy"] if "no_proxy" in section else ""
    if "\n" in raw_no_proxy or "\r" in raw_no_proxy:
        raise SystemExit("no_proxy 是逗号分隔的主机列表，不能包含换行。")
    no_proxy = ",".join(item.strip() for item in raw_no_proxy.split(",") if item.strip())

    return LLMConfig(
        provider=provider,
        base_url=_value(section, "base_url", path),
        api_key=_value(section, "api_key", path),
        model_id=_value(section, "model_id", path),
        max_tokens=int(max_tokens_raw),
        effort=effort,
        no_proxy=no_proxy,
    )


def load_settings(path: Path = Path("config.ini")) -> SettingsSnapshot:
    """读取可展示的设置。真实 api_key 只判断来源，不返回给界面。"""
    config = load_config(path)
    parser = ConfigParser()
    parser.read(path, encoding="utf-8")
    raw_key = parser["llm"].get("api_key", "").strip()
    match = _ENV_REF.match(raw_key)
    if match:
        key_source = f"环境变量 {match.group(1)}"
    elif raw_key:
        key_source = "config.ini（明文）"
    else:
        key_source = "未配置"
    return SettingsSnapshot(
        provider=config.provider,
        base_url=config.base_url,
        model_id=config.model_id,
        max_tokens=config.max_tokens,
        effort=config.effort,
        api_key_configured=bool(raw_key),
        api_key_source=key_source,
    )


def save_settings(
    values: SettingsValues,
    path: Path = Path("config.ini"),
) -> SettingsSnapshot:
    """只改 [llm] 的已知键，保留注释和未提交的 api_key。"""
    _validate_values(values)
    if not path.exists():
        raise ValueError(f"找不到 {path}，先复制 config.example.ini。")

    old_text = path.read_text(encoding="utf-8")
    updates = {
        "provider": values.provider,
        "base_url": values.base_url,
        "model_id": values.model_id,
        "max_tokens": str(values.max_tokens),
        "effort": values.effort,
    }
    if values.api_key and values.api_key.strip():
        updates["api_key"] = values.api_key.strip()
    new_text = _rewrite_llm_section(old_text, updates)

    # 先写临时文件并用真实加载器验证；失败不动原配置。
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(new_text, encoding="utf-8")
    try:
        load_config(temp_path)
    except SystemExit as exc:
        temp_path.unlink(missing_ok=True)
        raise ValueError(str(exc).replace(str(temp_path), str(path))) from exc
    temp_path.replace(path)
    return load_settings(path)


def _validate_values(values: SettingsValues) -> None:
    if values.provider not in ("anthropic", "openai"):
        raise ValueError("provider 只能是 anthropic 或 openai。")
    for name, value in (
        ("base_url", values.base_url),
        ("model_id", values.model_id),
        ("effort", values.effort),
        ("api_key", values.api_key or ""),
    ):
        if "\n" in value or "\r" in value:
            raise ValueError(f"{name} 不能包含换行。")
    if not values.base_url.strip():
        raise ValueError("base_url 不能为空。")
    if not values.model_id.strip():
        raise ValueError("model_id 不能为空。")
    if values.max_tokens <= 0:
        raise ValueError("max_tokens 必须大于 0。")
    if values.effort not in ("", "off", "low", "medium", "high", "max"):
        raise ValueError("effort 只能是 off / low / medium / high / max 或留空。")


def _rewrite_llm_section(text: str, updates: dict[str, str]) -> str:
    """替换 [llm] 里的键，保留注释和其他段落。"""
    lines = text.splitlines()
    section_start: int | None = None
    section_end = len(lines)
    in_llm = False
    seen: set[str] = set()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_llm:
                section_end = index
                break
            in_llm = stripped == "[llm]"
            if in_llm:
                section_start = index
            continue
        if not in_llm or not stripped or stripped.startswith((";", "#")):
            continue
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            lines[index] = f"{key} = {updates[key]}"
            seen.add(key)

    if section_start is None:
        raise ValueError("config.ini 缺少 [llm] 这一段。")
    insertion = section_end
    for key, value in updates.items():
        if key not in seen:
            lines.insert(insertion, f"{key} = {value}")
            insertion += 1
    return "\n".join(lines) + "\n"


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
