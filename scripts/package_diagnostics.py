#!/usr/bin/env python3
"""Create a private, bounded diagnostic tarball without dependencies or uploads."""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import platform
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SOURCE_DIRS = {"backend", "electron", "ui", "scripts", "tests", "docs", "browser-extension"}
AGENT_SOURCE_DIRS = {"src", "skills", "tests", "docs"}
AGENT_SOURCE_FILES = {"README.md", "run.sh", "setup.sh", "bootstrap.py", "pyproject.toml", "config.example.json", "requirements-browser.txt", "requirements-desktop.txt"}
SOURCE_ROOT_FILES = {
    "run.sh", "package.sh", "README.md", "CONTRACT.md", "BROWSER_LOGIN_CONTRACT.md",
    "requirements.txt", "requirements-browser.txt", "package.json", "package-lock.json",
    "config.example.json", "browser-login-sites.json",
}
SOURCE_SUFFIXES = {".py", ".js", ".cjs", ".css", ".html", ".md", ".sh", ".toml"}
JSON_NAMES = {"manifest.json", "browser-login-sites.json", "config.example.json", "requirements-browser.txt", "requirements-desktop.txt"}
EXCLUDED_DIRS = {"node_modules", ".venv", ".git", "__pycache__", ".pytest_cache", "data", "logs", "dist", "diagnostics", "workspace", "browser-profile", "artifacts"}
LOG_NAME = re.compile(r"^(?:electron|backend|launcher|agent-parent-start)\.log(?:\.\d+)?$")
SENSITIVE_KEY = re.compile(r"(?:api.?key|token|secret|password|authorization|cookie|credential)", re.I)
BODY_KEY = re.compile(r"^(?:payload|prompt|messages?|content|markdown|html|body|response_body|request_body|text|input|output|completion|arguments|result|stdout|stderr|reply|answer|summary|plan|localStorage|storage|value)$", re.I)
URL = re.compile(r"(?:https?|socks(?:4|5|5h)?):\/\/[^\s<>\"']+", re.I)
MASK = "[REDACTED]"
SOURCE_LIMIT = 2 * 1024 * 1024
LOG_LIMIT = 4 * 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024
FILE_LIMIT = 1500
SCAN_LIMIT = 10000
WARNING_LIMIT = 200


def linked_path(path):
    """Reject symlinks in the selected path, including directory ancestors."""
    return any(parent.is_symlink() for parent in (path, *path.parents))


class Collector:
    def __init__(self, root, data_dir, log_dir, include_markdown=False, output=None, agent_runtime_dir=None, include_content=False, agent_run_ids=()):
        self.root = Path(os.path.abspath(root))
        self.data_dir = Path(os.path.abspath(Path(data_dir).expanduser()))
        self.log_dir = Path(os.path.abspath(Path(log_dir).expanduser()))
        self.include_markdown = include_markdown
        self.include_content = include_content
        self.agent_run_ids = tuple(dict.fromkeys(agent_run_ids))
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value) for value in self.agent_run_ids):
            raise ValueError("Agent 任务标识必须为字母、数字、下划线或连字符，最多 128 字符。")
        self.output = Path(os.path.abspath(output)) if output else None
        self.agent_runtime = Path(os.path.abspath(Path(agent_runtime_dir).expanduser())) if agent_runtime_dir else self.root / "desktop-agent/.runtime"
        self.entries = []
        self.warnings = []
        self.total = 0
        self.secrets = set()
        self.config = None
        self.replacements = sorted({str(self.root): "<PROJECT>", str(self.data_dir): "<DATA>",
                                    str(self.log_dir): "<LOGS>", str(Path.home()): "<HOME>"}.items(),
                                   key=lambda item: len(item[0]), reverse=True)

    def warn(self, label, reason):
        # Never expose raw exception text: it may contain paths or credentials.
        if len(self.warnings) < WARNING_LIMIT:
            self.warnings.append({"file": self.scrub(str(label), body=False), "reason": reason})
        elif len(self.warnings) == WARNING_LIMIT:
            self.warnings.append({"file": "manifest", "reason": "further_warnings_omitted"})

    def read(self, path, label, limit=SOURCE_LIMIT, tail=False):
        path = Path(path)
        if self.output is not None and path == self.output:
            return None
        if linked_path(path):
            self.warn(label, "symlink_skipped")
            return None
        descriptor = None
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                self.warn(label, "non_regular_file_skipped")
                return None
            if before.st_size > limit and not tail:
                self.warn(label, "file_size_limit_skipped")
                return None
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = None
                if before.st_size > limit:
                    handle.seek(before.st_size - limit)
                    self.warn(label, "log_tail_only")
                content = handle.read(limit)
                after = os.fstat(handle.fileno())
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                self.warn(label, "file_changed_during_read")
            if tail and before.st_size > limit:
                # Do not keep a partial JSON record or split credential header.
                content = content.partition(b"\n")[2]
            return content
        except FileNotFoundError:
            self.warn(label, "file_not_found")
        except (OSError, ValueError):
            self.warn(label, "file_unreadable")
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return None

    def remember_secrets(self, value, key=""):
        if isinstance(value, dict):
            for name, child in value.items():
                self.remember_secrets(child, name)
        elif isinstance(value, list):
            for child in value:
                self.remember_secrets(child, key)
        elif isinstance(value, str):
            if SENSITIVE_KEY.search(key) and value.strip():
                self.secrets.add(value)
            for match in URL.finditer(value):
                try:
                    parsed = urlsplit(match.group())
                    for part in (parsed.username, parsed.password):
                        if part:
                            self.secrets.add(part)
                    for name, part in parse_qsl(parsed.query):
                        if SENSITIVE_KEY.search(name) and part:
                            self.secrets.add(part)
                except ValueError:
                    pass

    @staticmethod
    def clean_url(value):
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname or ""
            if ":" in hostname:
                hostname = f"[{hostname}]"
            netloc = hostname + (f":{parsed.port}" if parsed.port else "")
            # Every query value and fragment is omitted, including unknown token names.
            query = urlencode([(key, MASK) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)])
            return urlunsplit((parsed.scheme, netloc, parsed.path, query, MASK if parsed.fragment else ""))
        except ValueError:
            return "<REDACTED_URL>"

    def scrub(self, text, body=True):
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, MASK)
        text = URL.sub(lambda match: self.clean_url(match.group()), text)
        text = re.sub(r"(?i)\bBearer\s+[^\s\"',;]+", "Bearer " + MASK, text)
        text = re.sub(r"(?im)\b(?:set-cookie|cookie|authorization)\s*:\s*[^\r\n]+", lambda m: m.group().split(":", 1)[0] + ": " + MASK, text)
        text = re.sub(r'''(?i)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|token|secret|password|credential|authorization|cookie)["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}]+)''', lambda m: m.group(1) + '"' + MASK + '"', text)
        if body:
            # Structured diagnostic records are handled recursively below. This
            # catches conventional plain-text payload fields in legacy logs.
            text = re.sub(r"(?im)\b(?:prompt|messages?|content|markdown|html|body|localStorage)\s*[:=].*$", lambda m: m.group().split(":", 1)[0].split("=", 1)[0] + "=" + MASK, text)
        for original, replacement in self.replacements:
            text = text.replace(original, replacement)
        return text

    def clean_object(self, value, body=True):
        if isinstance(value, dict):
            return {str(key): child if str(key).endswith("_configured") and isinstance(child, bool)
                    else MASK if SENSITIVE_KEY.search(str(key)) or (body and BODY_KEY.match(str(key)))
                    else self.clean_object(child, body=body) for key, child in value.items()}
        if isinstance(value, list):
            return [self.clean_object(child, body=body) for child in value]
        if isinstance(value, str):
            return self.scrub(value, body=body)
        return value

    def log_text(self, content):
        result = []
        for line in content.decode("utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except (ValueError, RecursionError):
                result.append(self.scrub(line))
            else:
                # Explicit chunks are already redacted by the producing process.
                # Preserve their ordering/content when opted in; still scrub known
                # credentials and conventional secret fields during packaging.
                result.append(json.dumps(self.clean_object(record, body=not self.include_content), ensure_ascii=False))
        return ("\n".join(result) + "\n").encode("utf-8")

    def add(self, name, content, category, executable=False):
        if len(self.entries) >= FILE_LIMIT or self.total + len(content) > TOTAL_LIMIT:
            self.warn(name, "archive_size_or_count_limit_skipped")
            return
        self.entries.append((name, content, category, executable))
        self.total += len(content)

    def load_private_config(self):
        for name in ("FUSION_TOKEN", "FUSION_API_KEY", "OPENAI_API_KEY", "FUSION_AGENT_API_KEY"):
            if os.environ.get(name):
                self.secrets.add(os.environ[name])
        token_path = self.data_dir / "api-key.txt"
        if token_path.exists() or token_path.is_symlink():
            token = self.read(token_path, "private/api-key.txt", limit=16384)
            if token:
                self.secrets.add(token.decode("utf-8", errors="replace").strip())
        config_path = self.data_dir / "config.json"
        if config_path.exists() or config_path.is_symlink():
            raw = self.read(config_path, "private/config.json", limit=1024 * 1024)
            try:
                parsed = json.loads(raw) if raw else None
                if isinstance(parsed, dict):
                    self.config = parsed
                    self.remember_secrets(parsed)
                elif raw:
                    self.warn("private/config.json", "invalid_config")
            except (ValueError, RecursionError):
                self.warn("private/config.json", "invalid_config")
        self.secrets.discard("")

    def config_summary(self):
        if not self.config:
            return {"available": False}
        config = self.config
        providers = []
        for provider in config.get("providers", []) if isinstance(config.get("providers"), list) else []:
            if not isinstance(provider, dict):
                continue
            selectors = provider.get("selectors", {})
            providers.append({"id": provider.get("id"), "enabled": provider.get("enabled"),
                              "url": provider.get("url"), "proxy_configured": bool(provider.get("proxy")),
                              "selectors": selectors if isinstance(selectors, dict) else {}})
        fusion = config.get("fusion", {}) if isinstance(config.get("fusion"), dict) else {}
        generation = config.get("generation", {}) if isinstance(config.get("generation"), dict) else {}
        # Never copy arbitrary user configuration fields or secret values.
        summary = {"available": True, "schema_version": config.get("schema_version"), "providers": providers,
                   "fusion": {key: fusion.get(key) for key in ("mode", "provider", "base_url", "model", "timeout_seconds")},
                   "generation": {key: generation.get(key) for key in ("timeout_seconds", "submission_timeout_seconds", "stable_seconds", "min_wait_seconds")},
                   "allow_partial": config.get("allow_partial")}
        summary["fusion"]["api_key_configured"] = bool(fusion.get("api_key"))
        return self.clean_object(summary, body=False)

    def iter_files(self, directory, excluded_paths=()):
        def excluded(path):
            return any(path == item or item in path.parents for item in excluded_paths)
        if excluded(directory):
            return
        if linked_path(directory):
            self.warn("selected_directory", "symlink_directory_skipped")
            return
        if not directory.is_dir():
            return
        scanned = 0
        for current, directories, files in os.walk(directory, followlinks=False, onerror=lambda _: self.warn("selected_directory", "directory_unreadable")):
            directories[:] = sorted(name for name in directories if name not in EXCLUDED_DIRS and not name.startswith(".") and not (Path(current) / name).is_symlink() and not excluded(Path(current) / name))
            for filename in sorted(files):
                scanned += 1
                if scanned > SCAN_LIMIT:
                    self.warn("selected_directory", "scan_count_limit_reached")
                    return
                if not filename.startswith(".") and not excluded(Path(current) / filename):
                    yield Path(current) / filename

    def agent_sources(self):
        root = self.root / "desktop-agent"
        excluded = {self.agent_runtime, root / "workspace", root / ".runtime"}
        config = root / "agent.config.json"
        if config.exists() or config.is_symlink():
            # Read only to identify private paths; never copy this configuration.
            raw = self.read(config, "private/agent.config.json", limit=65536)
            try:
                parsed = json.loads(raw) if raw is not None else None
                if not isinstance(parsed, dict):
                    raise ValueError()
                for key in ("workspace", "runtime_dir", "api_key_file"):
                    value = parsed.get(key, "")
                    if not isinstance(value, str):
                        raise ValueError()
                    if value:
                        path = Path(value).expanduser()
                        excluded.add(Path(os.path.abspath(path if path.is_absolute() else root / path)))
            except (ValueError, TypeError, RecursionError):
                self.warn("code/desktop-agent", "private_paths_unknown_agent_sources_skipped")
                return []
        candidates = [root / name for name in sorted(AGENT_SOURCE_FILES) if (root / name).exists()]
        for directory in sorted(AGENT_SOURCE_DIRS):
            candidates.extend(path for path in self.iter_files(root / directory, excluded)
                              if path.suffix in SOURCE_SUFFIXES or path.name in JSON_NAMES)
        return [path for path in candidates if not any(path == item or item in path.parents for item in excluded)]

    def collect_sources(self):
        candidates = [self.root / name for name in sorted(SOURCE_ROOT_FILES) if (self.root / name).exists()]
        for directory in sorted(SOURCE_DIRS):
            candidates.extend(path for path in self.iter_files(self.root / directory)
                              if path.suffix in SOURCE_SUFFIXES or path.name in JSON_NAMES)
        candidates.extend(self.agent_sources())
        for path in candidates:
            relative = path.relative_to(self.root)
            if any(SENSITIVE_KEY.search(part) for part in relative.parts) and path.name not in {"browser-login-sites.json"}:
                # Unexpected credential dump files are not source artifacts.
                continue
            label = "code/" + relative.as_posix()
            raw = self.read(path, label)
            if raw is not None:
                text = raw.decode("utf-8", errors="replace")
                # Preserve executable code syntax; only literal known secrets
                # are removed from source, unlike runtime logs.
                for secret in sorted(self.secrets, key=len, reverse=True):
                    text = text.replace(secret, MASK)
                text = re.sub(r'''(?i)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password)["']?\s*[:=]\s*)("(?:\\.|[^"\\])+"|'(?:\\.|[^'\\])+')''',
                              lambda match: match.group(1) + '"' + MASK + '"', text)
                self.add(label, text.encode("utf-8"), "source", path.suffix == ".sh")

    def collect_logs(self):
        found = 0
        visited = set()
        for label, directory in (("current", self.log_dir), ("legacy", self.data_dir), ("agent", self.agent_runtime / "logs")):
            if directory in visited:
                continue
            visited.add(directory)
            if linked_path(directory):
                self.warn("logs/" + label, "symlink_directory_skipped")
                continue
            try:
                candidates = sorted(directory.iterdir()) if directory.is_dir() else []
            except OSError:
                self.warn("logs/" + label, "directory_unreadable")
                continue
            for path in candidates:
                matcher = re.compile(r"^agent\.log(?:\.\d+)?$") if label == "agent" else LOG_NAME
                if not matcher.fullmatch(path.name):
                    continue
                archive_name = "logs/" + label + "/" + path.name
                raw = self.read(path, archive_name, limit=LOG_LIMIT, tail=True)
                if raw is not None:
                    self.add(archive_name, self.log_text(raw), "log")
                    found += 1
        for run_id in self.agent_run_ids:
            archive_name = "logs/agent/runs/" + run_id + "/events.jsonl"
            raw = self.read(self.agent_runtime / "runs" / run_id / "events.jsonl", archive_name, limit=LOG_LIMIT, tail=True)
            if raw is not None:
                self.add(archive_name, self.log_text(raw), "log")
                found += 1
        if not found:
            self.warn("logs", "no_logs_found")

    def collect_markdown(self):
        if not self.include_markdown:
            return
        for path in self.iter_files(self.data_dir / "runs"):
            if path.suffix.lower() != ".md":
                continue
            name = "markdown/" + path.relative_to(self.data_dir / "runs").as_posix()
            raw = self.read(path, name)
            if raw is not None:
                self.add(name, self.scrub(raw.decode("utf-8", errors="replace"), body=False).encode("utf-8"), "user_markdown")

    def environment(self):
        result = {"python": platform.python_version(), "os": platform.system(), "kernel": platform.release(),
                  "architecture": platform.machine(), "display_available": bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                  "data_dir_overridden": "FUSION_DATA_DIR" in os.environ, "log_dir_overridden": "FUSION_LOG_DIR" in os.environ}
        version_env = {key: value for key, value in os.environ.items() if key not in {"NODE_OPTIONS", "NODE_PATH"}}
        try:
            result["node"] = subprocess.run(["node", "--version"], capture_output=True, timeout=3, check=True, env=version_env).stdout.decode().strip()[:80]
        except (OSError, subprocess.SubprocessError):
            result["node"] = "unavailable"
        for key, path in (("app", self.root / "package.json"), ("electron", self.root / "node_modules/electron/package.json")):
            if path.exists():
                raw = self.read(path, "environment/" + key, limit=128 * 1024)
                try:
                    result[key] = json.loads(raw or b"{}").get("version", "unknown")
                except (ValueError, AttributeError, RecursionError):
                    result[key] = "unknown"
        return self.clean_object(result, body=False)

    def collect(self):
        self.load_private_config()
        self.collect_sources()
        self.collect_logs()
        self.collect_markdown()
        self.add("config-summary.json", (json.dumps(self.config_summary(), ensure_ascii=False, indent=2) + "\n").encode(), "summary")
        self.add("environment.json", (json.dumps(self.environment(), ensure_ascii=False, indent=2) + "\n").encode(), "environment")
        self.add("READ-ME.txt", (
            "MultiLLM Fusion diagnostic archive. Created locally; nothing was uploaded.\n"
            "Source files, redacted diagnostics, allowlisted configuration summary and runtime versions.\n"
            "Excluded: browser profiles/cookies, login exports, passwords, API key files, databases, .env, dependencies.\n"
            "Markdown included: " + str(self.include_markdown) + "\n"
            "Conversation/tool log content included: " + str(self.include_content) + "\n"
            "Selected Agent run event logs: " + ", ".join(self.agent_run_ids) + "\n"
            "Markdown is user content. Review all archive contents before sharing; heuristic redaction is not a guarantee.\n"
            "See manifest.json for collected files, hashes, limits and skipped-file warnings.\n"
        ).encode(), "notice")
        manifest = {"format": "multillm-fusion-diagnostics", "version": 1,
                    "created_at": datetime.now(timezone.utc).isoformat(), "include_markdown": self.include_markdown, "include_content": self.include_content, "agent_run_ids": list(self.agent_run_ids),
                    "limits": {"source_file_bytes": SOURCE_LIMIT, "log_tail_bytes": LOG_LIMIT, "total_bytes": TOTAL_LIMIT, "files": FILE_LIMIT},
                    "files": [{"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), "category": category}
                              for name, content, category, _ in self.entries], "warnings": self.warnings}
        # The manifest describes payload entries, not itself (which would be self-referential).
        self.entries.append(("manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(), "manifest", False))
        return self

    def write(self, output):
        output = Path(os.path.abspath(output))
        if linked_path(output):
            raise ValueError("输出路径不能包含符号链接。")
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                with tarfile.open(fileobj=handle, mode="w:gz") as archive:
                    for name, content, _, executable in self.entries:
                        info = tarfile.TarInfo("multillm-fusion-diagnostics/" + name)
                        info.size = len(content)
                        info.mode = 0o700 if executable else 0o600
                        info.mtime = int(datetime.now(timezone.utc).timestamp())
                        archive.addfile(info, io.BytesIO(content))
        except BaseException:
            output.unlink(missing_ok=True)
            raise
        return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="将源码和脱敏日志打包用于分析；仅写入本地，不上传，无需 root。")
    parser.add_argument("--output", type=Path, help="输出 tar.gz 路径（不覆盖已有文件）")
    parser.add_argument("--include-markdown", action="store_true", help="主动包含生成的 Markdown；其中可能有问题、回答和其他用户内容")
    parser.add_argument("--include-content", action="store_true", help="保留日志中的发送正文、模型回复和工具参数/结果；默认分析包移除这些正文")
    parser.add_argument("--agent-runtime-dir", type=Path, help="Agent 自定义 runtime_dir；仅收集其中 logs/agent.log*，不收集任务或浏览器状态")
    parser.add_argument("--agent-run-id", action="append", default=[], help="额外收集指定任务的 events.jsonl，可重复；不收集 transcript/state/结果/截图，正文仍受 --include-content 控制")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    output = args.output or root / "diagnostics" / ("multillm-fusion-diagnostics-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".tar.gz")
    data_dir = Path(os.environ.get("FUSION_DATA_DIR") or "~/.local/share/multillm-fusion").expanduser()
    log_dir = Path(os.environ.get("FUSION_LOG_DIR") or str(root / "logs")).expanduser()
    if args.include_markdown:
        print("已启用 --include-markdown：压缩包将含用户对话输出，请检查后再分享。", file=sys.stderr)
    if args.include_content:
        print("已启用 --include-content：日志包含对话和命令结果正文，请检查后再分享。", file=sys.stderr)
    try:
        collector = Collector(root, data_dir, log_dir, args.include_markdown, output, args.agent_runtime_dir, args.include_content, args.agent_run_id).collect()
        collector.write(output)
    except FileExistsError:
        print("打包失败：输出文件已存在，请使用其他 --output 路径。", file=sys.stderr)
        return 1
    except (OSError, ValueError, RecursionError):
        print("打包失败：无法安全读取项目或写入输出，请检查路径、权限和可用空间。", file=sys.stderr)
        return 1
    print(f"已生成：{output.absolute()}")
    print(f"包含 {len(collector.entries)} 个文件，{len(collector.warnings)} 条收集提示（见 manifest.json）。")
    print("未上传。默认不打包对话 Markdown/SQLite/浏览器登录数据；分享前请检查内容。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
