"""音频设备枚举：分组、名称清洗、"显示全部"、最近使用。

两条后端：
* **pyaudiowpatch**（首选）：能直接给出 WASAPI loopback 设备（模式 2 依赖它）；
* **sounddevice**（回退）：装不上 pyaudiowpatch 时仍能列设备（模式 2 改用它
  的 ``WasapiSettings(loopback=True)``）。

名称清洗/分组规则**不硬编码**：代码只给默认值，用户的
``config.json → device_name_rules`` 可以追加关键词或后缀（见 ``merge_rules``）。

去重策略（这是列表干净与否的关键）：
1. 同一设备被 MME / DirectSound / WASAPI 重复列出时，**默认只留 WASAPI**
   （如果该设备压根没有 WASAPI 条目，才退回其他 hostapi）；
2. "声音映射器 / 主声音捕获驱动程序"这类系统旧版别名默认隐藏；
3. 勾选"显示全部"后以上两条都不生效，并把隐藏原因标在条目上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

try:
    import pyaudiowpatch as _pyaudio
except Exception:  # pragma: no cover - 没有就退 sounddevice
    _pyaudio = None  # type: ignore[assignment]

try:
    import sounddevice as _sd
except Exception:  # pragma: no cover
    _sd = None  # type: ignore[assignment]

#: 分组 id（也决定显示顺序）
GROUP_VIRTUAL = "virtual"
GROUP_MIC = "mic"
GROUP_SPEAKER = "speaker"
GROUP_OTHER = "other"

GROUP_TITLES = {
    "input": {
        GROUP_VIRTUAL: "🔊 虚拟声卡",
        GROUP_MIC: "🎤 麦克风",
        GROUP_OTHER: "🖥️ 其他输入设备",
    },
    "output": {
        GROUP_VIRTUAL: "🔊 虚拟声卡",
        GROUP_SPEAKER: "🔊 物理扬声器",
        GROUP_OTHER: "🔊 其他输出设备",
    },
}

#: 默认规则。用户在 config.json 里给同名字段时是**追加**，不是替换。
DEFAULT_RULES: dict[str, Any] = {
    # 驱动描述类关键词：出现在括号里就认为这段是"驱动后缀"，可以剪掉
    "driver_keywords": [
        "realtek", "high definition audio", "usb audio", "audio device",
        "c-media", "cmedia", "intel", "nvidia", "amd audio", "audio driver",
        "define audio", "audio)", "sound card",
    ],
    # 结尾出现这些片段时直接剪掉（不区分大小写）
    "strip_suffixes": ["[loopback]", "(loopback)"],
    # 系统旧版别名：默认隐藏（勾"显示全部"才出现）
    "legacy_keywords": [
        "声音映射器", "sound mapper", "主声音捕获", "primary sound capture",
        "primary sound driver", "主声音驱动程序",
    ],
    # 名称里出现这些词视为"不可用"（未插入/禁用）
    "unavailable_keywords": [
        "unplugged", "not present", "not plugged", "disabled", "禁用",
        "未插入", "未连接", "已断开",
    ],
    # 关键词命中即归组；顺序即优先级（虚拟声卡先判断，
    # 否则 "Virtual Cable Microphone" 会被麦克风组抢走）
    "groups": [
        {
            "id": GROUP_VIRTUAL,
            "keywords": [
                "cable", "voicemeeter", "vb-audio", "vbaudio", "virtual",
                "stereo mix", "立体声混音", "vac", "intelligo", "wave out",
                "waveout", "loopback", "steam streaming",
            ],
        },
        {
            "id": GROUP_MIC,
            "keywords": [
                "麦克风", "microphone", "headset", "mic", "array", "阵列",
                "chat", "耳麦", "话筒",
            ],
        },
    ],
    # 同名去重时优先选这些 hostapi（越靠前越优先）
    "hostapi_rank": ["wasapi", "wdm-ks", "directsound", "mme"],
}

#: 匹配"结尾的一整个括号段"，允许括号里还套一层 (R) 之类
_TRAILING_PAREN = re.compile(r"^(.*?)\s*\(((?:[^()]|\([^()]*\))*)\)\s*$")
_UNTERMINATED_PAREN = re.compile(r"\s*\([^()]*$")


@dataclass(frozen=True)
class DeviceChoice:
    """界面/配置里用的一台设备。"""

    key: str                 # 持久化标识（后端给的完整原名）
    name: str                # 完整原名（tooltip 用）
    short_name: str          # 清洗后的短名（界面显示）
    group: str               # virtual / mic / speaker / other
    hostapi: str
    channels: int
    samplerate: int
    index: int               # 后端设备索引
    backend: str             # pyaudiowpatch / sounddevice
    is_default: bool = False
    is_loopback: bool = False
    available: bool = True
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def label(self) -> str:
        text = self.short_name
        if self.is_default:
            text += "  ·默认"
        if not self.available:
            text += "  (不可用)"
        return text

    def tooltip(self) -> str:
        lines = [
            f"完整名称：{self.name}",
            f"驱动接口：{self.hostapi or '未知'}",
            f"声道：{self.channels} · 设备采样率：{self.samplerate} Hz",
            f"枚举后端：{self.backend}",
        ]
        if self.is_loopback:
            lines.append("Loopback：录制该设备上所有应用的声音")
        if self.note:
            lines.append(self.note)
        return "\n".join(lines)


# ---------------------------------------------------------------- 规则处理
def merge_rules(user_rules: dict[str, Any] | None) -> dict[str, Any]:
    """默认规则 + 用户追加（列表类字段取并集，groups 按 id 合并关键词）。"""
    rules: dict[str, Any] = {
        key: list(value) for key, value in DEFAULT_RULES.items() if isinstance(value, list)
    }
    rules["groups"] = [dict(g) for g in DEFAULT_RULES["groups"]]
    if not user_rules:
        return rules

    for key in ("driver_keywords", "strip_suffixes", "legacy_keywords",
                "unavailable_keywords", "hostapi_rank"):
        extra = user_rules.get(key)
        if not isinstance(extra, list):
            continue
        lowered = {str(item).lower() for item in rules.get(key, [])}
        for item in extra:
            text = str(item).strip()
            if text and text.lower() not in lowered:
                rules[key].append(text)
                lowered.add(text.lower())

    user_groups = user_rules.get("groups")
    if isinstance(user_groups, list):
        by_id = {g["id"]: g for g in rules["groups"]}
        for item in user_groups:
            if not isinstance(item, dict):
                continue
            gid = str(item.get("id") or "").strip()
            if not gid:
                continue
            keywords = [str(k).strip() for k in item.get("keywords", []) if str(k).strip()]
            if gid in by_id:
                existing = {k.lower() for k in by_id[gid]["keywords"]}
                by_id[gid]["keywords"].extend(k for k in keywords if k.lower() not in existing)
            else:
                rules["groups"].append({"id": gid, "keywords": keywords})
    return rules


# ---------------------------------------------------------------- 名称清洗
def _is_driver_segment(content: str, rules: dict[str, Any]) -> bool:
    lowered = content.lower().strip()
    if not lowered:
        return False
    if lowered in {str(s).lower() for s in rules.get("strip_suffixes", [])}:
        return True
    return any(keyword and keyword in lowered for keyword in rules.get("driver_keywords", []))


def strip_markers(name: str, rules: dict[str, Any]) -> str:
    """去掉 ``[Loopback]`` 这类标记后缀（分组判断必须在去掉之后做）。"""
    text = (name or "").strip()
    for suffix in rules.get("strip_suffixes", []):
        if suffix and text.lower().endswith(str(suffix).lower()):
            text = text[: len(text) - len(str(suffix))].strip()
    return text


def clean_device_name(raw: str, rules: dict[str, Any]) -> str:
    """把 ``Microphone (C-Media(R) Audio) [Loopback]`` 洗成 ``Microphone``。

    只剪"驱动描述类"的结尾括号；``扬声器 (Steam Streaming Speakers)``
    这种括号里是设备身份，会保留，方便用户区分设备。
    """
    name = strip_markers(raw, rules)
    if not name:
        return "(未知设备)"

    # 反复剪结尾括号，处理 "A (B) (C)" 这种叠了两层的
    for _ in range(4):
        match = _TRAILING_PAREN.match(name)
        if not match:
            break
        stem, content = match.group(1).strip(), match.group(2)
        if not _is_driver_segment(content, rules):
            break
        name = stem or name
    # MME 会把名字截断在 31 字符，"麦克风 (Steam Streaming Microphone" 这种
    # 会留下没闭合的括号，把尾部残片剪掉
    name = _UNTERMINATED_PAREN.sub("", name).strip()
    name = re.sub(r"\s{2,}", " ", name).strip(" -_·")
    return name or strip_markers(raw, rules) or "(未知设备)"


def classify(name: str, rules: dict[str, Any], kind: str = "input") -> tuple[str, str]:
    """返回 (group_id, group_title)。kind = "input" / "output"。"""
    lowered = (name or "").lower()
    titles = GROUP_TITLES.get(kind, GROUP_TITLES["input"])
    for group in rules.get("groups", []):
        gid = group.get("id")
        if gid not in titles:
            continue
        for keyword in group.get("keywords", []):
            if keyword and keyword.lower() in lowered:
                return gid, titles[gid]
    fallback = GROUP_MIC if kind == "input" else GROUP_SPEAKER
    return fallback, titles.get(fallback, "")


def _hostapi_rank(hostapi: str, rules: dict[str, Any]) -> int:
    lowered = (hostapi or "").lower()
    for position, keyword in enumerate(rules.get("hostapi_rank", [])):
        if keyword and keyword in lowered:
            return position
    return 99


def _is_legacy(name: str, rules: dict[str, Any]) -> bool:
    lowered = (name or "").lower()
    return any(k.lower() in lowered for k in rules.get("legacy_keywords", []) if k)


def _looks_unavailable(name: str, channels: int, rules: dict[str, Any]) -> bool:
    if channels <= 0:
        return True
    lowered = (name or "").lower()
    return any(
        keyword.lower() in lowered
        for keyword in rules.get("unavailable_keywords", [])
        if keyword
    )


# ---------------------------------------------------------------- 枚举
def _sounddevice_defaults() -> tuple[str | None, str | None]:
    """用 sounddevice 拿系统默认设备名（WASAPI 全名，比 MME 截断名可靠）。"""
    if _sd is None:
        return None, None
    try:
        in_name = out_name = None
        in_index = _sd.default.device[0]
        out_index = _sd.default.device[1]
        if isinstance(in_index, int) and in_index >= 0:
            in_name = str(_sd.query_devices(in_index)["name"]).strip()
        if isinstance(out_index, int) and out_index >= 0:
            out_name = str(_sd.query_devices(out_index)["name"]).strip()
        return in_name, out_name
    except Exception:
        return None, None


def _is_default_device(name: str, short: str, default_full: str | None,
                       default_short: str | None) -> bool:
    if not default_full and not default_short:
        return False
    if default_full and name == default_full:
        return True
    if default_short and short == default_short:
        return True
    return False


def _build_choice(
    *, name: str, hostapi: str, channels: int, rate: int, index: int,
    backend: str, rules: dict[str, Any], kind: str, is_loopback: bool,
    is_default: bool,
) -> DeviceChoice:
    short = clean_device_name(name, rules)
    # 分组用清洗后的名字判断，否则名字里的 "[Loopback]" 会把设备错分到虚拟声卡组
    group, _title = classify(short, rules, kind)
    legacy = _is_legacy(name, rules)
    unavailable = legacy or _looks_unavailable(name, channels, rules)
    note = "系统旧版别名（建议用 WASAPI 版本）" if legacy else ""
    return DeviceChoice(
        key=name,
        name=name,
        short_name=short,
        group=group,
        hostapi=hostapi,
        channels=channels,
        samplerate=rate,
        index=index,
        backend=backend,
        is_default=is_default,
        is_loopback=is_loopback,
        available=not unavailable,
        note=note,
    )


def _enumerate_pyaudio(rules: dict[str, Any], want_loopback: bool) -> list[DeviceChoice]:
    assert _pyaudio is not None
    choices: list[DeviceChoice] = []
    default_in, default_out = _sounddevice_defaults()
    kind = "output" if want_loopback else "input"
    audio = _pyaudio.PyAudio()
    try:
        for index in range(audio.get_device_count()):
            try:
                info = audio.get_device_info_by_index(index)
            except Exception:
                continue
            name = str(info.get("name", "")).strip()
            channels_in = int(info.get("maxInputChannels", 0) or 0)
            channels_out = int(info.get("maxOutputChannels", 0) or 0)
            is_loopback = bool(info.get("isLoopbackDevice", False))
            try:
                hostapi = str(
                    audio.get_host_api_info_by_index(int(info.get("hostApi", 0)))["name"]
                )
            except Exception:
                hostapi = ""

            if want_loopback:
                if not is_loopback:
                    continue
                channels = channels_in or channels_out
            else:
                if channels_in <= 0 or is_loopback:
                    continue
                channels = channels_in

            short = clean_device_name(name, rules)
            default_full = default_out if want_loopback else default_in
            default_short = clean_device_name(default_full, rules) if default_full else None
            choices.append(_build_choice(
                name=name,
                hostapi=hostapi,
                channels=channels,
                rate=int(float(info.get("defaultSampleRate", 48000) or 48000)),
                index=index,
                backend="pyaudiowpatch",
                rules=rules,
                kind=kind,
                is_loopback=is_loopback,
                is_default=_is_default_device(name, short, default_full, default_short),
            ))
    finally:
        try:
            audio.terminate()
        except Exception:
            pass
    return choices


def _enumerate_sounddevice(rules: dict[str, Any], kind: str) -> list[DeviceChoice]:
    """用 sounddevice 枚举。kind = "input" | "output" | "loopback"。

    「output」这一档专门给**播放**用：播放走 sounddevice，所以索引必须来自
    sounddevice，不能拿 pyaudiowpatch 的索引去开播放流（两套 PortAudio 索引不通用）。
    """
    if _sd is None:
        return []
    try:
        devices = _sd.query_devices()
        hostapis = _sd.query_hostapis()
    except Exception:
        return []

    choices: list[DeviceChoice] = []
    default_in, default_out = _sounddevice_defaults()
    is_output = kind in ("output", "loopback")
    kind_for_groups = "output" if is_output else "input"

    for index, dev in enumerate(devices):
        try:
            name = str(dev.get("name", "")).strip()
            channels_in = int(dev.get("max_input_channels", 0) or 0)
            channels_out = int(dev.get("max_output_channels", 0) or 0)
            api_index = int(dev.get("hostapi", 0))
            hostapi = str(hostapis[api_index]["name"]) if api_index < len(hostapis) else ""
        except Exception:
            continue

        if is_output:
            if channels_out <= 0:
                continue
            channels = channels_out
        else:
            if channels_in <= 0:
                continue
            channels = channels_in

        short = clean_device_name(name, rules)
        default_full = default_out if is_output else default_in
        default_short = clean_device_name(default_full, rules) if default_full else None
        choices.append(_build_choice(
            name=name,
            hostapi=hostapi,
            channels=channels,
            rate=int(float(dev.get("default_samplerate", 48000) or 48000)),
            index=index,
            backend="sounddevice",
            rules=rules,
            kind=kind_for_groups,
            is_loopback=(kind == "loopback"),
            is_default=_is_default_device(name, short, default_full, default_short),
        ))
    return choices


def _dedupe(choices: list[DeviceChoice], rules: dict[str, Any]) -> list[DeviceChoice]:
    """同名设备（被多个 hostapi 重复列出）只保留 hostapi 排名最高的。"""
    best: dict[str, DeviceChoice] = {}
    for choice in choices:
        current = best.get(choice.key)
        if current is None:
            best[choice.key] = choice
            continue
        if _hostapi_rank(choice.hostapi, rules) < _hostapi_rank(current.hostapi, rules):
            best[choice.key] = choice
        elif choice.is_default and not current.is_default:
            best[choice.key] = choice
    return list(best.values())


def _prefer_wasapi(choices: list[DeviceChoice], rules: dict[str, Any]) -> list[DeviceChoice]:
    """默认列表只留 WASAPI 条目（前提是系统里确实有 WASAPI 设备）。

    理由：同一块设备会被 MME / DirectSound / WASAPI 各列一次，MME 还把名字
    截断到 31 个字符，导致列表里出现一堆"看着像两台"的重复项。
    统一只留 WASAPI，一台设备一行；想看旧接口的在界面上勾"显示全部"。
    """
    wasapi = [c for c in choices if _hostapi_rank(c.hostapi, rules) == 0]
    return wasapi if wasapi else choices


def list_devices(
    kind: str,
    rules: dict[str, Any],
    show_all: bool = False,
) -> list[DeviceChoice]:
    """列出设备。

    kind:
      * ``"input"``    —— 模式 1 的录音输入（pyaudiowpatch 优先）
      * ``"loopback"`` —— 模式 2 的系统声音（pyaudiowpatch 的 loopback 设备）
      * ``"output"``   —— 播放输出（**固定走 sounddevice**，索引要和播放流一致）

    两条 PortAudio 实例（sounddevice 与 pyaudiowpatch）的索引不通用，
    所以这里把"哪种用途用哪套索引"定死在枚举层，上层只拿 DeviceChoice。
    """
    if kind == "output":
        choices = _enumerate_sounddevice(rules, "output")
    elif kind == "loopback":
        if _pyaudio is not None:
            choices = _enumerate_pyaudio(rules, True)
        else:
            choices = _enumerate_sounddevice(rules, "loopback")
    else:
        if _pyaudio is not None:
            choices = _enumerate_pyaudio(rules, False)
        else:
            choices = _enumerate_sounddevice(rules, "input")

    choices = _dedupe(choices, rules)
    if not show_all:
        choices = _prefer_wasapi(choices, rules)
        choices = [c for c in choices if c.available]
    choices.sort(key=lambda c: (c.group, c.short_name.lower(), c.hostapi))
    return choices


def group_choices(
    choices: list[DeviceChoice],
    kind: str,
    recent_keys: Iterable[str] = (),
) -> list[tuple[str, list[DeviceChoice]]]:
    """按分组整理成 [(标题, [设备...])]，最近使用的单独置顶。

    kind 接受 "input" / "output"，也接受调用方常用的 "loopback"（等价 output）。
    """
    title_kind = "output" if kind in ("output", "loopback") else "input"
    titles = GROUP_TITLES.get(title_kind, GROUP_TITLES["input"])
    recent = [k for k in recent_keys if k]

    pinned: list[DeviceChoice] = []
    for key in recent:
        for choice in choices:
            if choice.key == key and choice not in pinned:
                pinned.append(choice)
                break

    buckets: dict[str, list[DeviceChoice]] = {}
    for choice in choices:
        if choice in pinned:
            continue
        buckets.setdefault(choice.group, []).append(choice)

    ordered: list[tuple[str, list[DeviceChoice]]] = []
    if pinned:
        ordered.append(("★ 最近使用", pinned))
    for gid in (GROUP_VIRTUAL, GROUP_MIC, GROUP_SPEAKER, GROUP_OTHER):
        items = buckets.get(gid)
        if items:
            ordered.append((titles.get(gid, gid), items))
    return ordered


def find_choice(choices: list[DeviceChoice], key: str | None) -> DeviceChoice | None:
    """按配置里的完整名找设备；支持模糊匹配以应对设备改名。"""
    if not key:
        return None
    for choice in choices:
        if choice.key == key:
            return choice
    lowered = key.lower()
    for choice in choices:
        if lowered in choice.name.lower() or choice.name.lower() in lowered:
            return choice
    return None


# ---------------------------------------------------------------- 虚拟声卡（VB-CABLE）
#: 虚拟声卡**播放端**（程序往这里写，语音软件从 CABLE Output 读）
CABLE_PLAYBACK_HINTS = ("cable input", "vb-audio virtual cable", "vb-cable", "cable in")
#: 虚拟声卡**录音端**（语音软件把它当麦克风）
CABLE_CAPTURE_HINTS = ("cable output", "vb-audio virtual cable", "vb-cable", "cable out")

CABLE_NAME = "CABLE Input"
CABLE_CAPTURE_NAME = "CABLE Output"


@dataclass
class VBCableStatus:
    """虚拟声卡检测结果（播放端/录音端分开看，缺一端也能给出准确提示）。"""

    playback: DeviceChoice | None = None
    capture: DeviceChoice | None = None

    @property
    def installed(self) -> bool:
        return self.playback is not None

    @property
    def complete(self) -> bool:
        return self.playback is not None and self.capture is not None

    def playback_label(self) -> str:
        return self.playback.name if self.playback else CABLE_NAME

    def capture_label(self) -> str:
        return self.capture.name if self.capture else CABLE_CAPTURE_NAME

    @property
    def rate_mismatch(self) -> bool:
        """两端默认采样率不一致 —— VB-CABLE 跨率不重采样，会导致音效变调/断续。"""
        if self.playback is None or self.capture is None:
            return False
        left = int(self.playback.samplerate or 0)
        right = int(self.capture.samplerate or 0)
        return bool(left and right and left != right)

    def summary(self) -> str:
        if self.complete:
            if self.rate_mismatch:
                return (
                    f"已检测到 VB-CABLE，但两端采样率不一致"
                    f"（输入 {self.playback.samplerate}Hz / 输出 {self.capture.samplerate}Hz），"
                    "音效会变调或断续：请把两端设成同一采样率（推荐 48000Hz）"
                )
            return "已检测到 VB-CABLE（播放端 + 录音端）"
        if self.playback is not None:
            return "检测到 CABLE Input，但没有 CABLE Output（语音软件可能选不到输入）"
        if self.capture is not None:
            return "检测到 CABLE Output，但没有 CABLE Input（无法输出到虚拟声卡）"
        return "未检测到 VB-CABLE，请先安装（vb-audio.com/Cable）"


def _match_hints(choices: list[DeviceChoice], hints: tuple[str, ...],
                 rules: dict[str, Any] | None = None,
                 prefer: str = "") -> DeviceChoice | None:
    """按关键词匹配，并且**优先 WASAPI**（MME/DirectSound 的名字会被截断且延迟更高）。"""
    active_rules = rules or merge_rules(None)

    def best(candidates: list[DeviceChoice]) -> DeviceChoice | None:
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda c: (_hostapi_rank(c.hostapi, active_rules), c.index),
        )[0]

    if prefer:
        found = best([c for c in choices if prefer.lower() in c.name.lower()])
        if found is not None:
            return found
    for hint in hints:
        found = best([c for c in choices if hint in c.name.lower()])
        if found is not None:
            return found
    for hint in hints:
        found = best([c for c in choices if hint in c.short_name.lower()])
        if found is not None:
            return found
    return None


def find_cable_input(rules: dict[str, Any], show_all: bool = False) -> DeviceChoice | None:
    """找 VB-CABLE 的播放端（程序输出到这里）。"""
    choices = list_devices("output", rules, show_all=show_all)
    return _match_hints(choices, CABLE_PLAYBACK_HINTS, rules, prefer="cable input")


def find_cable_output(rules: dict[str, Any], show_all: bool = False) -> DeviceChoice | None:
    """找 VB-CABLE 的录音端（语音软件把它当麦克风）。"""
    choices = list_devices("input", rules, show_all=show_all)
    found = _match_hints(choices, CABLE_CAPTURE_HINTS, rules, prefer="cable output")
    if found is not None:
        return found
    # 有些驱动只在 loopback 列表里暴露 CABLE Output
    loops = list_devices("loopback", rules, show_all=show_all)
    return _match_hints(loops, CABLE_CAPTURE_HINTS, rules, prefer="cable output")


def detect_vb_cable(rules: dict[str, Any] | None = None,
                    show_all: bool = True) -> VBCableStatus:
    """一次性检测虚拟声卡两端（供顶栏徽章 / 设置分组用）。"""
    active_rules = rules or merge_rules(None)
    return VBCableStatus(
        playback=find_cable_input(active_rules, show_all=show_all),
        capture=find_cable_output(active_rules, show_all=show_all),
    )


def resolve_by_name(
    name: str | None,
    kind: str,
    rules: dict[str, Any],
    show_all: bool = True,
) -> DeviceChoice | None:
    """按**名字**解析设备（配置里只存名字，索引每次现算）。

    匹配顺序：完整名精确 → 清洗名精确 → 完整名包含 → 清洗名包含。
    """
    if not name:
        return None
    choices = list_devices(kind, rules, show_all=show_all)
    lowered = str(name).strip().lower()
    if not lowered:
        return None
    for choice in choices:
        if choice.name.lower() == lowered or choice.key.lower() == lowered:
            return choice
    for choice in choices:
        if choice.short_name.lower() == lowered:
            return choice
    for choice in choices:
        if lowered in choice.name.lower() or choice.name.lower() in lowered:
            return choice
    for choice in choices:
        if lowered in choice.short_name.lower() or choice.short_name.lower() in lowered:
            return choice
    return None


def default_output(rules: dict[str, Any], show_all: bool = True) -> DeviceChoice | None:
    """系统默认播放设备。

    以 **sounddevice 报的默认设备全名**为准（同名条目很多，光看 is_default 会选错），
    找不到再退回 is_default，最后退回第一个可用项。
    """
    choices = list_devices("output", rules, show_all=show_all)
    if not choices:
        return None
    _capture_default, playback_default = _sounddevice_defaults()
    if playback_default:
        lowered = playback_default.lower()
        for choice in choices:
            if choice.name.lower() == lowered or choice.key.lower() == lowered:
                return choice
        for choice in choices:
            if lowered in choice.name.lower() or choice.name.lower() in lowered:
                return choice
    for choice in choices:
        if choice.is_default:
            return choice
    return choices[0]


def physical_outputs(rules: dict[str, Any]) -> list[DeviceChoice]:
    """可当"本地监听"的物理输出（排除虚拟声卡类设备）。"""
    choices = list_devices("output", rules, show_all=False)
    result = []
    for choice in choices:
        lowered = choice.name.lower()
        if any(hint in lowered for hint in CABLE_PLAYBACK_HINTS):
            continue
        result.append(choice)
    return result


def is_virtual_name(name: str | None) -> bool:
    lowered = (name or "").lower()
    return any(hint in lowered for hint in CABLE_PLAYBACK_HINTS + CABLE_CAPTURE_HINTS)


def backend_available() -> tuple[bool, str]:
    """录音后端是否可用（pyaudiowpatch 优先，sounddevice 兜底）。"""
    if _pyaudio is not None:
        try:
            audio = _pyaudio.PyAudio()
            audio.terminate()
            return True, "pyaudiowpatch"
        except Exception as exc:
            return False, f"pyaudiowpatch 初始化失败：{exc}"
    if _sd is not None:
        return True, "sounddevice（pyaudiowpatch 不可用，已降级）"
    return False, "没有可用的录音后端（请安装 pyaudiowpatch 或 sounddevice）"


def loopback_backend() -> str:
    """模式 2 实际会用哪个后端。"""
    if _pyaudio is not None:
        return "pyaudiowpatch"
    if _sd is not None:
        return "sounddevice"
    return "none"


# ---------------------------------------------------------------- 混音的麦克风（sounddevice 域）
def list_mixer_inputs(rules: dict[str, Any] | None = None,
                      show_all: bool = False) -> list[DeviceChoice]:
    """混音引擎可用的麦克风列表。

    **必须走 sounddevice（PortAudio A）**：只有和播放输出在同一个 PortAudio
    实例里，才能开 ``sd.Stream`` 双工流、在**同一个回调**里混音；录音器用的是
    pyaudiowpatch（PortAudio B），两套索引不通用，所以这里单独枚举。
    """
    active_rules = rules or merge_rules(None)
    choices = _enumerate_sounddevice(active_rules, "input")
    choices = _dedupe(choices, active_rules)
    if not show_all:
        choices = _prefer_wasapi(choices, active_rules)
        choices = [c for c in choices if c.available]
    choices.sort(key=lambda c: (c.group, c.short_name.lower(), c.hostapi))
    return choices


def resolve_mixer_mic(name: str | None,
                      rules: dict[str, Any] | None = None) -> DeviceChoice | None:
    """按名字在 **sounddevice 域** 里解析混音麦克风。"""
    if not name:
        return None
    active_rules = rules or merge_rules(None)
    choices = list_mixer_inputs(active_rules, show_all=True)
    lowered = str(name).strip().lower()
    for choice in choices:
        if choice.name.lower() == lowered or choice.key.lower() == lowered:
            return choice
    for choice in choices:
        if choice.short_name.lower() == lowered:
            return choice
    for choice in choices:
        if lowered in choice.name.lower() or choice.name.lower() in lowered:
            return choice
    return None


def default_mixer_input(rules: dict[str, Any] | None = None) -> DeviceChoice | None:
    """默认混音麦克风：WASAPI 优先、以 sounddevice 默认录音设备为准，排除虚拟声卡端点。"""
    active_rules = rules or merge_rules(None)
    candidates = list_mixer_inputs(active_rules, show_all=False)   # 已按 WASAPI 优先
    if not candidates:
        candidates = list_mixer_inputs(active_rules, show_all=True)
    choices = [c for c in candidates if not is_virtual_name(c.name)]
    if not choices:
        return None
    capture_default, _playback = _sounddevice_defaults()
    if capture_default:
        lowered = capture_default.lower()
        for choice in choices:
            if choice.name.lower() == lowered:
                return choice
        for choice in choices:
            if lowered in choice.name.lower() or choice.name.lower() in lowered:
                return choice
    for choice in choices:
        if choice.is_default:
            return choice
    return choices[0]


