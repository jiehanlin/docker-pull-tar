import gzip
import hashlib
import json
import os
import re
import shutil
import threading
import time
import math  # 新增 math 库用于计算页数
import urllib.request
import warnings

warnings.filterwarnings("ignore", message="urllib3.*doesn\\'t match a supported version")
warnings.filterwarnings("ignore", category=UserWarning, module="requests")

import requests
from urllib.request import getproxies
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import tarfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List
from pathlib import Path
import gradio as gr

# 配置参数
VERSION = "v2.0.2-Gradio7-Web"
DEFAULT_1MS_API = "https://1ms.run/api/v1/registry"
CONNECT_TIMEOUT = 8
READ_TIMEOUT = 120
DOWNLOAD_MAX_RETRIES = 4
BACKOFF_BASE = 0.3
MAX_PARALLEL_LAYERS = 8

# 可用镜像源
# 华为云 DDN 是 docker.io 的国内中转（官方源慢/不可达时的首选）；
# repo_prefix 为该源特有的仓库路径前缀：华为云 DDN 用 ddn-k8s/docker.io/<name>（不带 library/）
@dataclass(frozen=True)
class RegistrySource:
    label: str
    host: str
    repo_prefix: str = ""


# 内置默认源（可在界面中编辑，也可写入 sources.json 持久化）
# 配置行格式：名称|registry 主机名|仓库路径前缀(可选)
BUILTIN_SOURCES = [
    RegistrySource("华为云 DDN 加速 (swr.cn-north-4)", "swr.cn-north-4.myhuaweicloud.com", "ddn-k8s/docker.io"),
    RegistrySource("1ms 专属加速 (docker.1ms.run)", "docker.1ms.run"),
    RegistrySource("DaoCloud 镜像 (docker.m.daocloud.io)", "docker.m.daocloud.io"),
    RegistrySource("轩辕镜像 (docker.xuanyuan.me)", "docker.xuanyuan.me"),
    RegistrySource("Docker 官方 (registry-1.docker.io)", "registry-1.docker.io"),
]

# 当前生效的源列表（可由界面/配置文件动态替换）
REGISTRY_SOURCES = list(BUILTIN_SOURCES)
REGISTRY_MAP = {s.label: s for s in REGISTRY_SOURCES}

# 源配置文件：可用环境变量 SOURCES_CONFIG 指定路径，默认 conf/sources.conf（相对工作目录）
SOURCE_CONFIG_FILE = os.environ.get("SOURCES_CONFIG", os.path.join("conf", "sources.conf"))
SOURCE_CONFIG_HINT = (
    "# 每行一个镜像源，格式：名称|registry 主机名|仓库路径前缀(可选)\n"
    "# 例：华为云 DDN 加速|swr.cn-north-4.myhuaweicloud.com|ddn-k8s/docker.io\n"
    "# 例：自建 Harbor|harbor.example.com\n"
    "# 顺序即优先级：首选源失败后按顺序自动回退；以 # 开头的行会被忽略"
)


def format_sources(sources: List[RegistrySource]) -> str:
    """把源列表序列化为可编辑的文本。"""
    lines = [SOURCE_CONFIG_HINT]
    for s in sources:
        lines.append(f"{s.label}|{s.host}|{s.repo_prefix}".rstrip("|"))
    return "\n".join(lines)


def parse_source_lines(text: str):
    """解析源配置文本，返回 (sources, errors)。

    每行格式：名称|registry 主机名|仓库路径前缀(可选)
    也允许只写主机名（名称默认取主机名）。
    """
    sources: List[RegistrySource] = []
    errors: List[str] = []
    seen_hosts = set()
    for lineno, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 1:
            label, host, prefix = parts[0], parts[0], ""
        elif len(parts) == 2:
            label, host, prefix = parts[0], parts[1], ""
        else:
            label, host, prefix = parts[0], parts[1], parts[2]
        if not host:
            errors.append(f"第 {lineno} 行缺少 registry 主机名，已忽略")
            continue
        if host in seen_hosts:
            errors.append(f"第 {lineno} 行主机名 {host} 重复，已忽略")
            continue
        seen_hosts.add(host)
        sources.append(RegistrySource(label or host, host, prefix))
    if not sources:
        errors.append("至少需要配置一个镜像源")
    return sources, errors


def apply_source_config(sources: List[RegistrySource]):
    """让解析出的源列表立即生效。"""
    global REGISTRY_SOURCES, REGISTRY_MAP
    REGISTRY_SOURCES = list(sources)
    REGISTRY_MAP = {s.label: s for s in REGISTRY_SOURCES}


def load_source_config() -> List[RegistrySource]:
    """从配置文件加载源配置；文件不存在则使用内置默认并生成配置文件。"""
    path = Path(SOURCE_CONFIG_FILE)
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
            sources, errors = parse_source_lines(text)
            if sources:
                apply_source_config(sources)
                print(f"已从 {path} 加载 {len(sources)} 个镜像源")
                return REGISTRY_SOURCES
            print(f"{path} 配置无效，改用内置默认：{errors}")
        except Exception as e:
            print(f"读取 {path} 失败，改用内置默认：{e}")
    apply_source_config(BUILTIN_SOURCES)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(format_sources(BUILTIN_SOURCES), encoding="utf-8")
    except Exception as e:
        print(f"写入默认配置文件失败（可忽略）：{e}")
    return REGISTRY_SOURCES


def save_source_config(text: str):
    """校验并保存源配置，返回 (sources, message)。"""
    sources, errors = parse_source_lines(text)
    if errors:
        return None, "；".join(errors)
    path = Path(SOURCE_CONFIG_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(format_sources(sources), encoding="utf-8")
    except Exception as e:
        return None, f"保存失败：{e}"
    apply_source_config(sources)
    return sources, f"✅ 已保存 {len(sources)} 个镜像源到 {SOURCE_CONFIG_FILE}"


# 防止回环代理问题
local_proxies = getproxies()
os.environ["HTTP_PROXY"] = os.environ["http_proxy"] = local_proxies.get("http", "")
os.environ["HTTPS_PROXY"] = os.environ["https_proxy"] = local_proxies.get("https", "")
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "localhost, 127.0.0.1/8, ::1"


# --------------------------
# 状态与进度管理 (适配 Web)
# --------------------------
@dataclass
class DownloadStats:
    total_size: int = 0
    downloaded_size: int = 0
    start_time: float = 0.0
    speeds: List[float] = field(default_factory=list)

    def get_avg_speed(self) -> float:
        if not self.speeds: return 0.0
        return sum(self.speeds[-10:]) / len(self.speeds[-10:])

    def format_size(self, size: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024: return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"


class LayerProgress:
    def __init__(self, name: str, total_size: int, index: int, total_layers: int):
        self.name = name
        self.total_size = total_size
        self.downloaded_size = 0
        self.index = index
        self.total_layers = total_layers
        self.status = "waiting"  # waiting, downloading, completed


class WebProgressDisplay:
    def __init__(self):
        self.layers: Dict[str, LayerProgress] = {}
        self.stats = DownloadStats()
        self.lock = threading.Lock()
        self.is_done = False
        self.error_msg = ""
        self.final_path = ""

    def add_layer(self, name: str, total_size: int, index: int, total_layers: int):
        with self.lock:
            self.layers[name] = LayerProgress(name, total_size, index, total_layers)

    def update_layer(self, name: str, downloaded: int):
        with self.lock:
            if name in self.layers:
                self.layers[name].downloaded_size = downloaded
                self.layers[name].status = "downloading"

    def update_layer_size(self, name: str, total_size: int):
        with self.lock:
            if name in self.layers and total_size and total_size > 0:
                self.layers[name].total_size = max(self.layers[name].total_size, total_size)

    def complete_layer(self, name: str):
        with self.lock:
            if name in self.layers:
                layer = self.layers[name]
                if layer.total_size == 0:
                    layer.total_size = layer.downloaded_size
                else:
                    layer.downloaded_size = layer.total_size
                layer.status = "completed"

    def get_html_content(self) -> str:
        with self.lock:
            if self.error_msg:
                return f"""
                <div style="padding: 15px; border-radius: 8px; background-color: #ffebee; color: #c62828; font-weight: bold; border: 1px solid #ef5350;">
                    ❌ 发生错误: {self.error_msg}
                </div>
                """

            html = ["""
            <div style="font-family: 'Consolas', 'Courier New', monospace; background-color: #1e1e1e; color: #e0e0e0; padding: 16px; border-radius: 8px; min-height: 250px; font-size: 13px;">
                <div style="margin-bottom: 15px; font-weight: 600; color: #4fc3f7;">🐳 Docker Image Pull Status</div>
            """]

            for _, layer in sorted(self.layers.items(), key=lambda x: x[1].index):
                if layer.total_size > 0:
                    progress_pct = (layer.downloaded_size / layer.total_size) * 100
                else:
                    progress_pct = 100.0 if layer.status == "completed" else 0.0

                if layer.status == "completed":
                    icon_color, bar_color, icon = "#4caf50", "#4caf50", "✔"
                elif layer.status == "downloading":
                    icon_color, bar_color, icon = "#29b6f6", "#29b6f6", "⬇"
                else:
                    icon_color, bar_color, icon = "#757575", "#616161", "⏳"

                t_str = self.stats.format_size(layer.total_size) if layer.total_size > 0 else "?"
                c_str = self.stats.format_size(layer.downloaded_size)

                html.append(f"""
                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
                    <div style="width: 140px; color: {icon_color};">
                        <span style="display: inline-block; width: 20px;">{icon}</span>
                        ({layer.index}/{layer.total_layers}) {layer.name:<12}
                    </div>
                    <div style="flex-grow: 1; margin: 0 15px; background-color: #333333; height: 10px; border-radius: 5px; overflow: hidden;">
                        <div style="width: {progress_pct}%; background-color: {bar_color}; height: 100%; transition: width 0.3s ease;"></div>
                    </div>
                    <div style="width: 160px; text-align: right; color: #b0bec5;">
                        <span style="display:inline-block; width: 50px;">{progress_pct:5.1f}%</span> | {c_str}/{t_str}
                    </div>
                </div>
                """)

            speed = self.stats.get_avg_speed()
            speed_str = self.stats.format_size(int(speed)) if speed > 0 else "0B"

            html.append(f"""
                <hr style="border: none; border-top: 1px dashed #424242; margin: 15px 0;">
                <div style="color: #ffb74d;">📊 预估下载速度: {speed_str}/s</div>
            """)

            if self.is_done:
                html.append(f"""
                    <div style="margin-top: 15px; padding: 15px; background-color: #e8f5e9; color: #1b5e20; border: 1px solid #a5d6a7; border-radius: 6px; font-family: sans-serif;">
                        <div style="font-size: 16px; font-weight: bold; margin-bottom: 10px;">🎉 所有组件下载并打包完成!</div>
                        <div style="margin-bottom: 8px;">
                            📦 <strong>保存路径:</strong> 
                            <code style="background: #ffffff; color: #d81b60; padding: 3px 6px; border-radius: 4px; border: 1px solid #f48fb1; font-family: monospace;">{self.final_path}</code>
                        </div>
                        <div>
                            💡 <strong>导入命令:</strong> 
                            <code style="background: #ffffff; color: #1976d2; padding: 3px 6px; border-radius: 4px; border: 1px solid #90caf9; font-family: monospace;">docker load -i {self.final_path}</code>
                        </div>
                    </div>
                """)

            html.append("</div>")
            return "".join(html)


# --------------------------
# 核心网络与下载逻辑
# --------------------------
# 架构别名 -> OCI 平台 (architecture, variant)
ARCH_ALIASES = {
    "amd64": ("amd64", None),
    "x86_64": ("amd64", None),
    "arm64": ("arm64", None),
    "aarch64": ("arm64", None),
    "arm/v7": ("arm", "v7"),
    "armv7": ("arm", "v7"),
    "arm/v6": ("arm", "v6"),
    "armv6": ("arm", "v6"),
    "s390x": ("s390x", None),
    "ppc64le": ("ppc64le", None),
    "mips64le": ("mips64le", None),
}


def resolve_platform(arch: str):
    """把用户选择的架构名转换为 OCI 平台的 (architecture, variant)"""
    key = (arch or "").strip().lower()
    return ARCH_ALIASES.get(key, (key, None))


# Docker Hub 的等价域名，剥离后按官方镜像处理
DOCKER_HUB_PREFIXES = ("docker.io/", "index.docker.io/", "registry-1.docker.io/", "registry.hub.docker.com/")


def normalize_repository(repo: str) -> str:
    """把用户输入的镜像名转换为 registry 使用的 repository 路径。

    docker.io/nginx -> library/nginx
    nginx           -> library/nginx
    harbor.x.com/ns/img -> 原样保留（私有仓库）
    """
    name = (repo or "").strip().strip("/")
    if not name:
        return ""
    lowered = name.lower()
    for prefix in DOCKER_HUB_PREFIXES:
        if lowered.startswith(prefix):
            name = name[len(prefix):]
            break
    name = name.strip("/")
    if not name:
        return ""
    if "/" not in name:
        return f"library/{name}"
    return name


def parse_image_input(text: str):
    """解析搜索框输入，返回 (registry|None, repository, tag|None)。

    支持 nginx / nginx:1.31.8 / docker.io/nginx:1.31.8 / ns/repo:tag /
          harbor:5000/ns/repo:tag /
          swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nginx:1.31.8
    registry 为 None 表示使用界面选择的镜像源。
    """
    value = (text or "").strip()
    if not value:
        return None, "", None
    head, sep, tail = value.rpartition(":")
    if sep and head and "/" not in tail:
        repository, tag = head, tail
    else:
        repository, tag = value, None

    registry = None
    repo_part = repository.strip("/")
    if "/" in repo_part:
        first, _, rest = repo_part.partition("/")
        # 第一段像主机名（带 . 或 :，或是 localhost）时视为自定义 registry
        if first == "localhost" or "." in first or ":" in first:
            registry = first
            repo_part = rest.strip("/")
    if not repo_part:
        return registry, "", tag

    # Docker Hub 官方域名等价于默认源，不算自定义 registry
    if registry and registry.lower() in (
            "docker.io", "index.docker.io", "registry-1.docker.io", "registry.hub.docker.com"):
        registry = None

    if registry:
        # 自定义 registry：仓库路径按用户输入原样使用
        return registry, repo_part, tag
    return None, normalize_repository(repo_part), tag


def source_repository(repository: str, source: RegistrySource) -> str:
    """按镜像源规则改写仓库路径（华为云 DDN 需要 ddn-k8s/docker.io/<name>）。"""
    if not source.repo_prefix:
        return repository
    # 已经带过该前缀（如用户直接输入完整地址）就不再重复拼接
    if repository.startswith(f"{source.repo_prefix}/"):
        return repository
    name = repository
    if name.startswith("library/"):
        name = name[len("library/"):]
    return f"{source.repo_prefix}/{name}"


def source_tag_candidates(tag: str, arch: str, source: RegistrySource) -> List[str]:
    """某些代理源（如华为云 DDN）把 arm64 拆成独立 tag：<tag>-linuxarm64。"""
    if source.repo_prefix and resolve_platform(arch)[0] == "arm64":
        return [f"{tag}-linuxarm64", tag]
    return [tag]


def sort_tags(tags: List[str]) -> List[str]:
    """按语义版本倒序排列 tag，最新版本置顶（latest 固定第一）。"""
    uniq = sorted({t for t in tags if t})
    latest = [t for t in uniq if t == "latest"]
    rest = [t for t in uniq if t != "latest"]

    def key(tag: str):
        m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(.*)$", tag)
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4))
        return (-1, -1, -1, tag)

    rest.sort(key=key, reverse=True)
    return latest + rest


def registry_candidates(preferred: str = "") -> List[RegistrySource]:
    """按用户选择排序的镜像源候选列表（用于失败自动切换）。"""
    ordered: List[RegistrySource] = []
    if preferred:
        # 命中内置源时保留其仓库路径前缀；真正的自定义源才没有前缀
        matched = next((s for s in REGISTRY_SOURCES if s.host == preferred), None)
        ordered.append(matched or RegistrySource("自定义", preferred))
    for s in REGISTRY_SOURCES:
        if s.host not in [o.host for o in ordered]:
            ordered.append(s)
    return ordered


def apply_proxy_config(proxy_mode, p_host, p_user, p_pass):
    proxies = {}
    if proxy_mode == "系统代理":
        sys_proxies = urllib.request.getproxies()
        if "http" in sys_proxies:
            os.environ["HTTP_PROXY"] = os.environ["http_proxy"] = sys_proxies["http"]
            proxies["http"] = sys_proxies["http"]
        if "https" in sys_proxies:
            os.environ["HTTPS_PROXY"] = os.environ["https_proxy"] = sys_proxies["https"]
            proxies["https"] = sys_proxies["https"]
    elif proxy_mode == "自定义代理":
        auth = f"{p_user}:{p_pass}@" if p_user else ""
        url = f"http://{auth}{p_host}" if p_host else ""
        if url:
            os.environ["HTTP_PROXY"] = os.environ["http_proxy"] = url
            os.environ["HTTPS_PROXY"] = os.environ["https_proxy"] = url
            proxies = {"http": url, "https": url}
    return proxies


def get_session(verify_ssl, proxies) -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=64, pool_maxsize=128)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.verify = verify_ssl
    session.proxies = proxies
    return session


def get_auth_head(session, registry, repository):
    try:
        ping_url = f"https://{registry}/v2/"
        resp = session.get(ping_url, timeout=10)
        if resp.status_code == 401 and "WWW-Authenticate" in resp.headers:
            www = resp.headers["WWW-Authenticate"]
            auth_url = www.split('"')[1]
            reg_service = www.split('"')[3]
            token_url = f"{auth_url}?service={reg_service}&scope=repository:{repository}:pull"
            t_resp = session.get(token_url, timeout=10)
            t_resp.raise_for_status()
            access_token = t_resp.json()["token"]
            return {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json"
            }
    except Exception as e:
        print(f"Auth Ping Fallback: {e}")
    return {
        "Accept": "application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json"}


def download_file_chunked(session, url, headers, save_path, desc, expected_digest, progress: WebProgressDisplay):
    resume_pos = os.path.getsize(save_path) if os.path.exists(save_path) else 0
    fetch_headers = headers.copy()
    if resume_pos > 0: fetch_headers["Range"] = f"bytes={resume_pos}-"

    for attempt in range(DOWNLOAD_MAX_RETRIES):
        if progress.error_msg: return False
        try:
            with session.get(url, headers=fetch_headers, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as resp:
                if resp.status_code == 416:
                    progress.complete_layer(desc)
                    return True
                resp.raise_for_status()

                content_range = resp.headers.get("content-range")
                total_size = int(content_range.split("/")[1]) if content_range else int(
                    resp.headers.get("content-length", 0)) + resume_pos
                progress.update_layer_size(desc, total_size)

                mode = "ab" if resume_pos > 0 else "wb"
                downloaded_size = resume_pos
                last_update = time.time()
                last_size = resume_pos

                with open(save_path, mode) as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if progress.error_msg: return False
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            progress.update_layer(desc, downloaded_size)
                            progress.stats.total_size += len(chunk)

                            now = time.time()
                            if now - last_update >= 0.5:
                                speed = (downloaded_size - last_size) / (now - last_update)
                                progress.stats.speeds.append(speed)
                                last_size = downloaded_size
                                last_update = now
            progress.complete_layer(desc)
            return True
        except Exception as e:
            if attempt < DOWNLOAD_MAX_RETRIES - 1:
                time.sleep(1)
                continue
            raise e
    return False


def pull_image_logic(progress: WebProgressDisplay, sources, repository, tag, arch, output_dir, proxy_args):
    """依次尝试多个镜像源，任一源成功即完成；全部源失败才报错。"""
    errors: List[str] = []
    for source in sources:
        try:
            pull_from_registry(progress, source, repository, tag, arch, output_dir, proxy_args)
            return
        except Exception as e:
            print(f"[镜像源 {source.host}] 拉取失败，尝试下一个源: {e}")
            errors.append(f"{source.host}: {e}")
            # 复位状态，允许换源继续
            progress.error_msg = ""
            progress.layers.clear()
            progress.stats = DownloadStats()
    progress.error_msg = "；".join(errors) if errors else "所有镜像源均拉取失败"


def fetch_manifest_json(session, registry, repository, tag, auth_head):
    """获取 manifest 解析结果；tag 不存在时返回 None。"""
    mani_url = f"https://{registry}/v2/{repository}/manifests/{tag}"
    resp = session.get(mani_url, headers=auth_head)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def fetch_config_architecture(session, registry, repository, config_digest, auth_head) -> str:
    """读取镜像 config，返回其中的架构（用于校验单架构镜像是否与所选架构一致）。"""
    try:
        url = f"https://{registry}/v2/{repository}/blobs/{config_digest}"
        resp = session.get(url, headers=auth_head, timeout=(CONNECT_TIMEOUT, 60))
        resp.raise_for_status()
        return (resp.json() or {}).get("architecture", "")
    except Exception:
        return ""


def pull_from_registry(progress: WebProgressDisplay, source, repository, tag, arch, output_dir, proxy_args):
    """从单个镜像源拉取镜像；失败时抛出异常，交由上层切换镜像源。"""
    try:
        proxies = apply_proxy_config(*proxy_args[:4])
        session = get_session(proxy_args[4], proxies)

        registry = source.host
        # 仓库路径在输入层已归一化，这里只做源特有的前缀改写
        display_repo = repository
        repository = source_repository(display_repo, source)
        auth_head = get_auth_head(session, registry, repository)

        resp_json = None
        used_tag = None
        for cand_tag in source_tag_candidates(tag, arch, source):
            resp_json = fetch_manifest_json(session, registry, repository, cand_tag, auth_head)
            if resp_json:
                used_tag = cand_tag
                break
        if not resp_json:
            raise ValueError(f"镜像或标签不存在：{repository}:{tag}（源 {registry}）")

        want_arch, want_variant = resolve_platform(arch)
        if resp_json.get("manifests"):
            digest = None
            for m in resp_json["manifests"]:
                platform = m.get("platform", {}) or {}
                m_arch = platform.get("architecture")
                m_os = platform.get("os")
                if m_arch == want_arch and (not want_variant or platform.get("variant") == want_variant) \
                        and (not m_os or m_os == "linux"):
                    digest = m.get("digest")
                    break
            if not digest:
                raise ValueError(f"该镜像在 {tag} 下未找到 {arch} 架构。")

            mani_url = f"https://{registry}/v2/{repository}/manifests/{digest}"
            resp = session.get(mani_url, headers=auth_head)
            resp.raise_for_status()
            resp_json = resp.json()
        else:
            # 单架构 manifest（如华为云 DDN 代理）：校验实际架构，避免张冠李戴
            actual_arch = fetch_config_architecture(
                session, registry, repository, resp_json["config"]["digest"], auth_head)
            if actual_arch and actual_arch != want_arch:
                raise ValueError(
                    f"源 {registry} 的 {used_tag or tag} 实际架构为 {actual_arch}，与所选 {arch} 不一致")

        layers = resp_json.get("layers", [])
        config_digest = resp_json["config"]["digest"]
        config_size = resp_json["config"].get("size", 0)

        # 产物命名与 RepoTags 使用原始仓库名（不带代理源前缀），docker load 后显示为 nginx:1.31.8
        safe_repo = display_repo.replace("/", "_")
        # 架构名可能带 "/"（如 arm/v7），不能直接用于路径
        safe_arch = arch.replace("/", "_").replace("\\", "_")
        target_dir = Path(output_dir) / f"{safe_repo}_{tag}_{safe_arch}"
        imgdir = target_dir / "layers"
        imgdir.mkdir(parents=True, exist_ok=True)

        config_path = str(imgdir / f"{config_digest[7:]}.json")
        config_url = f"https://{registry}/v2/{repository}/blobs/{config_digest}"

        progress.add_layer("Config", config_size, 1, len(layers) + 1)

        parentid = ""
        for idx, l in enumerate(layers):
            ublob = l["digest"]
            fake_layerid = hashlib.sha256((parentid + "\n" + ublob + "\n").encode("utf-8")).hexdigest()
            l["fake_layerid"] = fake_layerid
            l["parent_layerid"] = parentid
            parentid = fake_layerid
            progress.add_layer(ublob[:12], l.get("size", 0), idx + 2, len(layers) + 1)

        download_file_chunked(session, config_url, auth_head, config_path, "Config", config_digest, progress)

        def download_worker(layer_obj):
            ublob = layer_obj["digest"]
            url = f"https://{registry}/v2/{repository}/blobs/{ublob}"
            fake_layerid = layer_obj["fake_layerid"]
            ldir = imgdir / fake_layerid
            ldir.mkdir(parents=True, exist_ok=True)

            save_path = str(ldir / "layer_gzip.tar")
            success = download_file_chunked(session, url, auth_head, save_path, ublob[:12], ublob, progress)
            return success, fake_layerid, ldir

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_LAYERS) as pool:
            futures = [pool.submit(download_worker, l) for l in layers]
            for fut in as_completed(futures):
                try:
                    suc, fid, ldir = fut.result()
                    if not suc:
                        raise Exception("分片网络下载失败")
                except Exception as e:
                    raise RuntimeError(f"分片下载失败：{e}")

        content = [{"Config": f"{config_digest[7:]}.json", "RepoTags": [f"{display_repo}:{tag}"], "Layers": []}]

        for l in layers:
            fake_layerid = l["fake_layerid"]
            parent_layerid = l["parent_layerid"]
            ldir = imgdir / fake_layerid
            gz_path = ldir / "layer_gzip.tar"
            tar_path = ldir / "layer.tar"

            if gz_path.exists():
                with gzip.open(gz_path, "rb") as gz, open(tar_path, "wb") as file:
                    shutil.copyfileobj(gz, file)
                os.remove(gz_path)

            with open(ldir / "json", "w") as f:
                json.dump({"id": fake_layerid, "parent": parent_layerid if parent_layerid else None}, f)

            content[0]["Layers"].append(f"{fake_layerid}/layer.tar")

        with open(imgdir / "manifest.json", "w") as f:
            json.dump(content, f)
        with open(imgdir / "repositories", "w") as f:
            json.dump({display_repo: {tag: layers[-1]["fake_layerid"]}}, f)

        tar_final = Path(output_dir) / f"{safe_repo}_{tag}_{safe_arch}.tar"
        with tarfile.open(str(tar_final), "w") as tar:
            tar.add(str(imgdir), arcname="/")

        shutil.rmtree(str(imgdir), ignore_errors=True)
        # 清理下载过程留下的空临时目录（目录非空时忽略）
        try:
            target_dir.rmdir()
        except OSError:
            pass

        progress.final_path = str(tar_final.absolute())
        progress.is_done = True

    except Exception as e:
        import traceback
        traceback.print_exc()
        # 向上抛出，由 pull_image_logic 决定是否切换镜像源
        raise


# --------------------------
# UI 交互函数与文件管理
# --------------------------
def get_downloaded_tars(out_dir):
    if not out_dir: out_dir = "downloads"
    os.makedirs(out_dir, exist_ok=True)
    files = []
    try:
        for f in os.listdir(out_dir):
            if f.endswith(".tar"):
                files.append(os.path.join(out_dir, f))
        files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    except Exception:
        pass

    choices = [os.path.basename(f) for f in files]
    return files, gr.update(choices=choices, value=None)


def delete_local_tar(filename, out_dir):
    if filename:
        filepath = os.path.join(out_dir, filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                gr.Info(f"🗑️ 成功删除: {filename}")
            except Exception as e:
                gr.Warning(f"删除失败: {e}")
    return get_downloaded_tars(out_dir)


# 🌟 新增：带分页的核心搜索函数
def execute_search(keyword, target_page, p_mode, p_host, p_user, p_pass, vSSL):
    if not keyword:
        return [], 1, "<div style='text-align:center;'>请先输入关键词</div>", gr.update(interactive=False), gr.update(
            interactive=False)

    proxies = apply_proxy_config(p_mode, p_host, p_user, p_pass)
    session = get_session(vSSL, proxies)
    page_size = 20
    try:
        resp = session.get(f"{DEFAULT_1MS_API}/search",
                           params={"query": keyword, "page": target_page, "page_size": page_size})
        resp.raise_for_status()
        data = resp.json().get("data", {})
        items = data.get("list", [])
        total = data.get("total", 0)

        # 计算总页数和当前限制
        total_pages = max(1, math.ceil(total / page_size))
        actual_page = min(max(1, target_page), total_pages)

        res = []
        for it in items:
            ns = it.get("namespace", "library")
            name = it.get("name", "")
            repo = f"{ns}/{name}" if ns else name
            res.append([
                repo,
                it.get("star_count", 0),
                it.get("pull_count", 0),
                it.get("last_modified", "")[:10],
                (it.get("description") or "").replace("\n", "")[:50]
            ])

        html_info = f"<div style='text-align:center; padding-top:8px; color:#555; font-size:14px;'>第 <b>{actual_page}</b> / {total_pages} 页 (共 {total} 个结果)</div>"

        can_prev = actual_page > 1
        can_next = actual_page < total_pages

        return res, actual_page, html_info, gr.update(interactive=can_prev), gr.update(interactive=can_next)

    except Exception as e:
        raise gr.Error(f"搜索失败: {str(e)}")


def fetch_tags_from_registry(session, registry: str, repository: str) -> List[str]:
    """通过标准 registry 接口获取实时 tag 列表。

    1ms 的搜索索引更新较慢（镜像已发布的新 tag 可能还没进索引），
    直接问 registry 的 tags/list 可以立刻拿到最新 tag。
    """
    headers = get_auth_head(session, registry, repository)
    resp = session.get(f"https://{registry}/v2/{repository}/tags/list", headers=headers,
                        timeout=(CONNECT_TIMEOUT, 30))
    resp.raise_for_status()
    return [t for t in (resp.json().get("tags") or []) if t]


def fn_get_tags(selected_repo, registry, p_mode, p_host, p_user, p_pass, vSSL, custom_registry=""):
    proxies = apply_proxy_config(p_mode, p_host, p_user, p_pass)
    session = get_session(vSSL, proxies)
    # 自定义 registry 时仓库路径按用户输入原样使用；否则按 Docker Hub 规则归一化
    repository = selected_repo.strip("/") if custom_registry else normalize_repository(selected_repo)

    def _to_update(tag_list: List[str]):
        if not tag_list:
            tag_list = ["latest"]
        # 按版本倒序，最新 tag 置顶，避免在上千个 tag 里翻找
        tag_list = sort_tags(tag_list)
        # 下拉框的值必须存在于 choices 中，否则 Gradio 在事件触发时会抛
        # ValueError: xxx is not in the list of choices
        default_tag = "latest" if "latest" in tag_list else tag_list[0]
        return gr.update(choices=tag_list, value=default_tag, interactive=True)

    # 1) 优先使用 registry 的实时 tag 列表（所选源不通时自动换源）
    preferred = custom_registry or (REGISTRY_MAP.get(registry).host if REGISTRY_MAP.get(registry) else "")
    for source in registry_candidates(preferred):
        try:
            live_tags = fetch_tags_from_registry(
                session, source.host, source_repository(repository, source))
            if live_tags:
                return _to_update(live_tags)
        except Exception:
            continue

    # 2) 回退到 1ms 索引接口
    try:
        # 分页拉取全部 tag（单页 100 条会被截断），最多 5 页
        tags: List[str] = []
        seen = set()
        page = 1
        page_size = 100
        total = None
        while page <= 5:
            resp = session.get(f"{DEFAULT_1MS_API}/get_tags",
                               params={"repositories": repository, "page": page, "page_size": page_size})
            resp.raise_for_status()
            # 越界页时接口会返回 data = null，需要容错
            data = resp.json().get("data") or {}
            items = data.get("list") or []
            if total is None:
                total = data.get("total", 0)
            for it in items:
                tag_name = it.get("tag_name")
                if tag_name and tag_name not in seen:
                    seen.add(tag_name)
                    tags.append(tag_name)
            if not items or (total and len(tags) >= total):
                break
            page += 1

        return _to_update(tags)
    except Exception:
        return gr.update(choices=["latest"], value="latest", interactive=True)


def fn_download_manager(repo, tag, arch, registry, out_dir, p_mode, p_host, p_user, p_pass, vSSL,
                        custom_registry=""):
    if not repo:
        yield '<div style="color:red;font-weight:bold;">❌ 请先在上方搜索并在表格中点击选择一个镜像！</div>', gr.skip(), gr.skip()
        return

    # 自定义 registry 时仓库路径按用户输入原样使用；否则按 Docker Hub 规则归一化
    repository = repo.strip("/") if custom_registry else normalize_repository(repo)
    if not repository:
        yield '<div style="color:red;font-weight:bold;">❌ 镜像名无效，请使用 nginx 或 nginx:1.31.8 这样的格式。</div>', gr.skip(), gr.skip()
        return

    tag = tag or "latest"

    # 架构支持多选：一次下载导出多个架构的 tar 包
    if isinstance(arch, (list, tuple, set)):
        arch_list = [a for a in arch if a]
    else:
        arch_list = [arch] if arch else []
    if not arch_list:
        yield '<div style="color:red;font-weight:bold;">❌ 请至少选择一个架构！</div>', gr.skip(), gr.skip()
        return

    yield '<div style="color:#666;font-family:monospace;padding:10px;">🕒 初始化下载任务中，请稍候...</div>', gr.skip(), gr.skip()

    # 所选源优先，失败后按 REGISTRY_SOURCES 顺序自动回退
    if custom_registry:
        preferred = custom_registry
    else:
        source = REGISTRY_MAP.get(registry)
        preferred = source.host if source else REGISTRY_SOURCES[0].host
    source_list = registry_candidates(preferred)
    proxy_args = (p_mode, p_host, p_user, p_pass, vSSL)

    for i, one_arch in enumerate(arch_list):
        progress = WebProgressDisplay()
        prefix = (f"[{i + 1}/{len(arch_list)}] 架构 {one_arch} " if len(arch_list) > 1 else "")

        t = threading.Thread(target=pull_image_logic,
                             args=(progress, source_list, repository, tag, one_arch, out_dir, proxy_args))
        t.start()

        while t.is_alive():
            time.sleep(0.5)
            yield f'<div>{prefix}</div>' + progress.get_html_content(), gr.skip(), gr.skip()

        if progress.error_msg:
            yield f'<div>{prefix}</div>' + progress.get_html_content(), gr.skip(), gr.skip()
            files, drop_update = get_downloaded_tars(out_dir)
            yield '<div style="color:red;font-weight:bold;">❌ 下载失败，已中止后续架构。</div>', files, drop_update
            return

    files, drop_update = get_downloaded_tars(out_dir)
    yield progress.get_html_content(), files, drop_update


# --------------------------
# Gradio 界面设计
# --------------------------
# 启动时加载镜像源配置（sources.json），失败则回退到内置默认
load_source_config()

with gr.Blocks(
    title="Docker 镜像离线下载工具"
) as demo:
    # 🌟 新增：隐藏的 State，用于存储当前分页对应的关键词和页码，避免因用户乱改文本框导致翻页报错
    current_kw_state = gr.State("")
    current_page_state = gr.State(1)
    # 用户直接输入完整镜像地址时锁定的自定义 registry（为空表示使用界面选择的源）
    custom_registry_state = gr.State("")

    gr.Markdown(
        f"# 🐳 Docker 镜像快捷离线拉取器 ({VERSION})\n搜索、选择架构、一键导出 `.tar` 镜像包供离线环境 `docker load`。")

    with gr.Accordion("⚙️ 全局设置项 (代理、源与路径)", open=False):
        with gr.Row():
            out_dir_ui = gr.Textbox(label="📦 镜像保存目录", value="downloads",
                                    info="默认下载到当前文件夹的 downloads 目录下", scale=2)
            registry_ui = gr.Dropdown(
                label="🌐 拉取数据源",
                choices=[s.label for s in REGISTRY_SOURCES],
                value=REGISTRY_SOURCES[0].label,
                info="首选源失败时会自动切换到下一个源",
                scale=2
            )
            ssl_ui = gr.Checkbox(label="🔐 开启 SSL 证书验证", value=True, info="如果遇到代理证书问题可取消勾选",
                                 scale=1)

        with gr.Row():
            proxy_mode_ui = gr.Radio(label="📡 代理模式", choices=["无代理", "系统代理", "自定义代理"], value="无代理")
        with gr.Row(visible=False) as custom_proxy_row:
            proxy_host_ui = gr.Textbox(label="代理地址 (例如 127.0.0.1:7890)")
            proxy_user_ui = gr.Textbox(label="代理用户名 (可选)")
            proxy_pass_ui = gr.Textbox(label="代理密码 (可选)", type="password")

        with gr.Accordion("🧩 镜像源配置（可编辑，顺序即优先级）", open=False):
            sources_text_ui = gr.Textbox(
                label="源列表", value=format_sources(REGISTRY_SOURCES), lines=10,
                info="每行一个源：名称|registry 主机名|仓库路径前缀(可选)；# 开头为注释")
            with gr.Row():
                sources_apply_btn = gr.Button("✅ 应用到本次运行", variant="primary")
                sources_save_btn = gr.Button("💾 保存到配置文件")
            source_status_ui = gr.HTML(
                value=f"<div style='color:#666;font-size:13px'>当前生效 {len(REGISTRY_SOURCES)} 个源，"
                      f"配置文件：{SOURCE_CONFIG_FILE}</div>")


    def toggle_proxy_visibility(mode):
        return gr.update(visible=(mode == "自定义代理"))


    proxy_mode_ui.change(fn=toggle_proxy_visibility, inputs=[proxy_mode_ui], outputs=[custom_proxy_row])


    def on_sources_apply(text):
        """校验并应用源配置（仅本次运行生效）。"""
        sources, errors = parse_source_lines(text)
        if errors:
            html = (f"<div style='color:#c62828;font-size:13px'>❌ 配置未生效：{'；'.join(errors)}</div>")
            return gr.update(choices=[s.label for s in REGISTRY_SOURCES], value=REGISTRY_SOURCES[0].label), html
        apply_source_config(sources)
        html = (f"<div style='color:#2e7d32;font-size:13px'>✅ 已应用 {len(sources)} 个源（仅本次运行；"
                f"点“保存到配置文件”可持久化）</div>")
        return gr.update(choices=[s.label for s in sources], value=sources[0].label), html


    def on_sources_save(text):
        """校验并保存源配置到 sources.json。"""
        sources, message = save_source_config(text)
        if not sources:
            html = f"<div style='color:#c62828;font-size:13px'>❌ {message}</div>"
            return gr.update(choices=[s.label for s in REGISTRY_SOURCES], value=REGISTRY_SOURCES[0].label), html
        html = f"<div style='color:#2e7d32;font-size:13px'>{message}</div>"
        return gr.update(choices=[s.label for s in sources], value=sources[0].label), html


    sources_apply_btn.click(fn=on_sources_apply, inputs=[sources_text_ui],
                            outputs=[registry_ui, source_status_ui])
    sources_save_btn.click(fn=on_sources_save, inputs=[sources_text_ui],
                            outputs=[registry_ui, source_status_ui])

    with gr.Row():
        with gr.Column(scale=4):
            gr.Markdown("### 🔍 步骤 1：全网查镜像")
            with gr.Row():
                kw_ui = gr.Textbox(label="输入镜像关键词", placeholder="eg: nginx, redis, pytorch", show_label=False,
                                   info="也可直接输入 username/repo:tag 或完整地址 "
                                        "swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nginx:1.31.8")
                search_btn = gr.Button("🔍 搜 索", variant="primary")

            df_results = gr.Dataframe(
                headers=["镜像名称 (Repo)", "⭐ Stars", "⬇️ Pulls", "更新日期", "描述"],
                interactive=False, wrap=True, type="array", max_height=300
            )

            # 🌟 新增：分页控件区域
            with gr.Row():
                prev_btn = gr.Button("◀ 上一页", interactive=False, size="sm")
                page_info_ui = gr.HTML(
                    "<div style='text-align:center; padding-top:8px; color:#999; font-size:14px;'>等待搜索...</div>")
                next_btn = gr.Button("下一页 ▶", interactive=False, size="sm")

        with gr.Column(scale=3):
            gr.Markdown("### 🏷️ 步骤 2：选择并下载")
            selected_repo_ui = gr.Textbox(label="当前选中目标", placeholder="请从左侧表格中点击选择一项...",
                                          interactive=False)

            with gr.Row():
                tag_ui = gr.Dropdown(label="选择 Tag", choices=["latest"], value="latest", interactive=False,
                                     allow_custom_value=True,
                                     info="列表来自 registry 实时接口；若缺少新版本，可直接手动输入 tag")
                arch_ui = gr.Dropdown(label="选择架构 Arch (可多选)", choices=["amd64", "arm64", "arm/v7", "s390x", "ppc64le"],
                                      value=["amd64", "arm64"], multiselect=True,
                                      info="可同时勾选多个架构，逐个架构导出独立的 .tar 包")

            dl_btn = gr.Button("⬇️ 开始下载 & 导出", variant="primary", size="lg")

            gr.Markdown("#### 运行日志 & 进度")
            log_box = gr.HTML(
                value="<div style='color:#999; font-size:14px; text-align:center; padding: 20px; border: 1px dashed #ccc; border-radius: 8px;'>等待开始...</div>"
            )

    gr.Markdown("---\n### 📁 步骤 3：本地镜像包管理 (.tar)")
    with gr.Row():
        with gr.Column(scale=3):
            file_list_ui = gr.File(
                label="📦 已下载的镜像 (在浏览器中点击文件名即可直接下载至当前设备)",
                file_count="multiple",
                interactive=False
            )
        with gr.Column(scale=1):
            refresh_btn = gr.Button("🔄 刷新本地列表")
            delete_dropdown = gr.Dropdown(label="🗑️ 选择要删除的文件")
            delete_btn = gr.Button("❌ 删除选中文件", variant="stop")


    # --------------------------
    # 事件绑定逻辑
    # --------------------------

    # 🌟 修改：绑定首次搜索事件（重置为第1页），同步更新并保存 State 记录
    def on_initial_search(kw, p_mode, p_host, p_user, p_pass, vSSL, registry):
        value = (kw or "").strip()
        custom_registry, repository, direct_tag = parse_image_input(value)
        # 输入形如 nginx:1.31.8 / docker.io/nginx:1.31.8 / ns/repo:tag / 完整镜像地址 时直接定位镜像
        if repository and (direct_tag or "/" in value):
            tag_update = fn_get_tags(repository, registry, p_mode, p_host, p_user, p_pass, vSSL, custom_registry)
            if direct_tag:
                tag_update = gr.update(choices=[direct_tag], value=direct_tag, interactive=True)
            return value, [], 1, "", False, False, repository, tag_update, custom_registry
        res, actual_page, html_info, can_prev, can_next = execute_search(
            value, 1, p_mode, p_host, p_user, p_pass, vSSL)
        return value, res, actual_page, html_info, can_prev, can_next, "", gr.update(), ""


    search_btn.click(
        fn=on_initial_search,
        inputs=[kw_ui, proxy_mode_ui, proxy_host_ui, proxy_user_ui, proxy_pass_ui, ssl_ui, registry_ui],
        outputs=[current_kw_state, df_results, current_page_state, page_info_ui, prev_btn, next_btn,
                 selected_repo_ui, tag_ui, custom_registry_state]
    )


    # 🌟 新增：绑定上一页与下一页事件（基于保存的 State 进行操作）
    def on_prev_page(saved_kw, current_page, p_mode, p_host, p_user, p_pass, vSSL):
        res, actual_page, html_info, can_prev, can_next = execute_search(saved_kw, current_page - 1, p_mode, p_host,
                                                                         p_user, p_pass, vSSL)
        return res, actual_page, html_info, can_prev, can_next


    def on_next_page(saved_kw, current_page, p_mode, p_host, p_user, p_pass, vSSL):
        res, actual_page, html_info, can_prev, can_next = execute_search(saved_kw, current_page + 1, p_mode, p_host,
                                                                         p_user, p_pass, vSSL)
        return res, actual_page, html_info, can_prev, can_next


    prev_btn.click(
        fn=on_prev_page,
        inputs=[current_kw_state, current_page_state, proxy_mode_ui, proxy_host_ui, proxy_user_ui, proxy_pass_ui,
                ssl_ui],
        outputs=[df_results, current_page_state, page_info_ui, prev_btn, next_btn]
    )

    next_btn.click(
        fn=on_next_page,
        inputs=[current_kw_state, current_page_state, proxy_mode_ui, proxy_host_ui, proxy_user_ui, proxy_pass_ui,
                ssl_ui],
        outputs=[df_results, current_page_state, page_info_ui, prev_btn, next_btn]
    )


    def on_df_select(evt: gr.SelectData, current_df, p_mode, p_host, p_user, p_pass, vSSL, registry):
        try:
            row_idx = evt.index[0]
            if isinstance(current_df, dict) and "data" in current_df:
                repo_name = current_df["data"][row_idx][0]
            elif hasattr(current_df, "iloc"):
                repo_name = current_df.iloc[row_idx, 0]
            else:
                repo_name = current_df[row_idx][0]
            repo_name = normalize_repository(repo_name)
            tag_update = fn_get_tags(repo_name, registry, p_mode, p_host, p_user, p_pass, vSSL)
            return repo_name, tag_update, ""
        except Exception as e:
            import traceback
            traceback.print_exc()
            return "", gr.update(choices=["latest"], value="latest", interactive=True), ""


    df_results.select(
        fn=on_df_select,
        inputs=[df_results, proxy_mode_ui, proxy_host_ui, proxy_user_ui, proxy_pass_ui, ssl_ui, registry_ui],
        outputs=[selected_repo_ui, tag_ui, custom_registry_state]
    )

    dl_btn.click(
        fn=fn_download_manager,
        inputs=[
            selected_repo_ui, tag_ui, arch_ui, registry_ui, out_dir_ui,
            proxy_mode_ui, proxy_host_ui, proxy_user_ui, proxy_pass_ui, ssl_ui, custom_registry_state
        ],
        outputs=[log_box, file_list_ui, delete_dropdown]  # 同步刷新文件面板
    )

    # 文件管理器事件绑定
    demo.load(
        fn=get_downloaded_tars, inputs=[out_dir_ui], outputs=[file_list_ui, delete_dropdown]
    )
    refresh_btn.click(
        fn=get_downloaded_tars, inputs=[out_dir_ui], outputs=[file_list_ui, delete_dropdown]
    )
    delete_btn.click(
        fn=delete_local_tar, inputs=[delete_dropdown, out_dir_ui], outputs=[file_list_ui, delete_dropdown]
    )

    demo.queue(default_concurrency_limit=4)

if __name__ == "__main__":
    print("服务启动中... 请在浏览器访问下方 URL 打开 UI 界面。")
    demo.launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
        theme=gr.themes.Soft(primary_hue="blue"),
        share=os.environ.get("GRADIO_SHARE", "0") == "1",
        inbrowser=os.environ.get("GRADIO_INBROWSER", "1") == "1"
    )
