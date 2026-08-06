#!/usr/bin/env python3
"""Validate Lua metadata and synchronize DependencyControl.json.

The Lua script metadata is the source of truth for package identity and version.
This tool is intentionally repository-specific: it knows the layout and release
conventions used by WenHe-Aegisub-Scripts and produces a machine-readable release
plan for the GitHub Actions workflow.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
MACROS_DIR = ROOT / "macros"
PACKAGES_DIR = ROOT / "packages"
FEED_PATH = ROOT / "DependencyControl.json"
SCHEMA_URL = (
    "https://raw.githubusercontent.com/TypesettingTools/DependencyControl/"
    "publish/schemas/feed/v0.4.0.json"
)
REPOSITORY_URL = "https://github.com/WenHe233/WenHe-Aegisub-Scripts"
RAW_ROOT = "https://raw.githubusercontent.com/WenHe233/WenHe-Aegisub-Scripts"
ZERO_SHA = "0" * 40

REQUIRED_METADATA = (
    "script_name",
    "script_description",
    "script_author",
    "script_version",
    "script_namespace",
    "script_url",
)
METADATA_RE = re.compile(
    r'^\s*(script_(?:name|description|author|version|namespace|url))\s*=\s*'
    r'("(?:\\.|[^"\\])*")\s*(?:--.*)?$'
)
NAMESPACE_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class SyncError(RuntimeError):
    """A user-facing validation or synchronization error."""


@dataclass(frozen=True)
class ScriptMetadata:
    path: Path
    name: str
    description: str
    author: str
    version: str
    namespace: str
    url: str
    sha1: str

    @property
    def version_tuple(self) -> tuple[int, int, int]:
        return parse_semver(self.version)


def parse_semver(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(version)
    if not match:
        raise SyncError(f"版本 {version!r} 不是三段式语义版本（例如 1.2.3）。")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def parse_script_bytes(path: Path, data: bytes) -> ScriptMetadata:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SyncError(f"{path.as_posix()} 必须使用 UTF-8 编码。") from exc

    values: dict[str, str] = {}
    duplicates: set[str] = set()
    for line in text.splitlines():
        match = METADATA_RE.match(line)
        if not match:
            continue
        key, literal = match.groups()
        if key in values:
            duplicates.add(key)
            continue
        try:
            value = json.loads(literal)
        except json.JSONDecodeError as exc:
            raise SyncError(f"{path.as_posix()} 的 {key} 不是有效的双引号字符串。") from exc
        values[key] = value

    if duplicates:
        raise SyncError(
            f"{path.as_posix()} 重复定义元数据：{', '.join(sorted(duplicates))}。"
        )
    missing = [key for key in REQUIRED_METADATA if key not in values]
    if missing:
        raise SyncError(
            f"{path.as_posix()} 缺少单行双引号元数据：{', '.join(missing)}。"
        )

    namespace = values["script_namespace"]
    if not NAMESPACE_RE.fullmatch(namespace):
        raise SyncError(f"{path.as_posix()} 的命名空间 {namespace!r} 无效。")
    if path.name != f"{namespace}.lua":
        raise SyncError(
            f"{path.as_posix()} 的文件名必须是 {namespace}.lua。"
        )
    parse_semver(values["script_version"])
    if values["script_author"] != "WenHe":
        raise SyncError(f"{path.as_posix()} 的 script_author 必须是 WenHe。")
    if values["script_url"] != REPOSITORY_URL:
        raise SyncError(
            f"{path.as_posix()} 的 script_url 必须是 {REPOSITORY_URL}。"
        )

    return ScriptMetadata(
        path=path,
        name=values["script_name"],
        description=values["script_description"],
        author=values["script_author"],
        version=values["script_version"],
        namespace=namespace,
        url=values["script_url"],
        sha1=sha1_bytes(data),
    )


def parse_script(path: Path) -> ScriptMetadata:
    return parse_script_bytes(path, path.read_bytes())


def run_git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        command = "git " + " ".join(args)
        raise SyncError(f"{command} 失败：{result.stderr.strip()}")
    return result.stdout.strip()


def git_object_exists(ref: str) -> bool:
    if not ref or ref == ZERO_SHA:
        return False
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}^{{commit}}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def git_file(ref: str, relative_path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"{ref}:{relative_path}"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def changed_paths(base: str, head: str) -> tuple[set[str], set[str]]:
    current_scripts = {
        path.relative_to(ROOT).as_posix() for path in MACROS_DIR.glob("*.lua")
    }
    current_packages = {
        path.relative_to(ROOT).as_posix() for path in PACKAGES_DIR.glob("*.json")
    } if PACKAGES_DIR.exists() else set()
    if not git_object_exists(base):
        return current_scripts, current_packages

    output = run_git(
        "diff",
        "--name-status",
        "--no-renames",
        base,
        head,
        "--",
        "macros/*.lua",
        "packages/*.json",
    )
    scripts: set[str] = set()
    packages: set[str] = set()
    for line in output.splitlines():
        if not line.strip():
            continue
        status, path = line.split("\t", 1)
        if status.startswith("D") and path.startswith("macros/"):
            raise SyncError(
                f"检测到脚本删除：{path}。请先制定 DependencyControl 下架/迁移方案。"
            )
        if path.startswith("macros/") and path.endswith(".lua"):
            scripts.add(path)
        elif path.startswith("packages/") and path.endswith(".json"):
            packages.add(path)
    return scripts, packages


def commit_subjects(revision_range: str, path: str) -> list[str]:
    output = run_git("log", "--reverse", "--format=%s", revision_range, "--", path)
    subjects: list[str] = []
    for subject in output.splitlines():
        subject = subject.strip()
        if subject and subject not in subjects and not subject.startswith("chore(release):"):
            subjects.append(subject)
    return subjects


def latest_package_tag(namespace: str) -> str | None:
    output = run_git("tag", "--list", f"{namespace}-v*", "--sort=-version:refname")
    return output.splitlines()[0] if output else None


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SyncError(f"缺少 {path.relative_to(ROOT).as_posix()}。") from exc
    except json.JSONDecodeError as exc:
        raise SyncError(f"{path.relative_to(ROOT).as_posix()} 不是有效 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise SyncError(f"{path.relative_to(ROOT).as_posix()} 的顶层必须是对象。")
    return data


def load_override(namespace: str) -> dict[str, Any]:
    path = PACKAGES_DIR / f"{namespace}.json"
    if not path.exists():
        return {}
    data = load_json(path)
    allowed = {"package", "release"}
    unknown = set(data) - allowed
    if unknown:
        raise SyncError(
            f"{path.relative_to(ROOT).as_posix()} 含未知字段：{', '.join(sorted(unknown))}。"
        )
    for section in allowed:
        if section in data and not isinstance(data[section], dict):
            raise SyncError(f"{path.relative_to(ROOT).as_posix()} 的 {section} 必须是对象。")
    forbidden_package = {"name", "author", "description", "channels", "changelog"}
    forbidden_release = {"version", "released", "default", "files"}
    bad_package = forbidden_package & set(data.get("package", {}))
    bad_release = forbidden_release & set(data.get("release", {}))
    if bad_package or bad_release:
        bad = sorted(bad_package | bad_release)
        raise SyncError(
            f"{path.relative_to(ROOT).as_posix()} 不能覆盖自动字段：{', '.join(bad)}。"
        )
    return data


def deep_merge(target: dict[str, Any], additions: dict[str, Any]) -> dict[str, Any]:
    for key, value in additions.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def package_for_script(
    metadata: ScriptMetadata,
    existing: dict[str, Any] | None,
    release_date: str,
    changelog_entries: list[str],
) -> dict[str, Any]:
    package = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    package.update(
        {
            "url": "@{baseUrl}/blob/main/macros/@{namespace}.lua",
            "author": metadata.author,
            "name": metadata.name,
            "description": metadata.description,
        }
    )

    channels = package.setdefault("channels", {})
    if not isinstance(channels, dict):
        raise SyncError(f"{metadata.namespace} 的 channels 必须是对象。")
    release = channels.setdefault("stable", {})
    if not isinstance(release, dict):
        raise SyncError(f"{metadata.namespace} 的 stable 频道必须是对象。")
    release.update(
        {
            "version": metadata.version,
            "released": release_date,
            "default": True,
            "files": [
                {
                    "name": ".lua",
                    "url": "@{fileBaseUrl}@{fileName}",
                    "sha1": metadata.sha1,
                }
            ],
        }
    )

    override = load_override(metadata.namespace)
    deep_merge(package, override.get("package", {}))
    deep_merge(release, override.get("release", {}))

    changelog = package.setdefault("changelog", {})
    if not isinstance(changelog, dict):
        raise SyncError(f"{metadata.namespace} 的 changelog 必须是对象。")
    if changelog_entries:
        changelog[metadata.version] = changelog_entries
    elif metadata.version not in changelog:
        changelog[metadata.version] = ["首次通过 DependencyControl 发布"]
    return package


def validate_feed_shape(feed: dict[str, Any]) -> None:
    if feed.get("dependencyControlFeedFormatVersion") != "0.4.0":
        raise SyncError("DependencyControl.json 必须使用 feed 格式 0.4.0。")
    if feed.get("baseUrl") != REPOSITORY_URL:
        raise SyncError("DependencyControl.json 的 baseUrl 与仓库地址不一致。")
    if not isinstance(feed.get("macros"), dict):
        raise SyncError("DependencyControl.json 的 macros 必须是对象。")


def validate_with_official_schema(feed: dict[str, Any]) -> None:
    try:
        import jsonschema
    except ImportError as exc:
        raise SyncError("缺少 jsonschema；请安装 requirements-ci.txt。") from exc
    try:
        with urllib.request.urlopen(SCHEMA_URL, timeout=30) as response:
            schema = json.load(response)
    except Exception as exc:  # network and malformed remote JSON
        raise SyncError(f"无法读取官方 DependencyControl Schema：{exc}") from exc

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(feed), key=lambda error: list(error.path))
    if errors:
        details = []
        for error in errors[:20]:
            location = ".".join(str(part) for part in error.absolute_path) or "<root>"
            details.append(f"  - {location}: {error.message}")
        raise SyncError("DependencyControl.json 未通过官方 Schema：\n" + "\n".join(details))


def validate_generated_feed(
    feed: dict[str, Any], scripts: dict[str, ScriptMetadata]
) -> None:
    validate_feed_shape(feed)
    macros = feed["macros"]
    stale = sorted(set(macros) - set(scripts))
    if stale:
        raise SyncError(f"feed 含不存在的脚本：{', '.join(stale)}。")
    missing = sorted(set(scripts) - set(macros))
    if missing:
        raise SyncError(f"feed 缺少脚本：{', '.join(missing)}。")

    for namespace, metadata in scripts.items():
        package = macros[namespace]
        release = package["channels"]["stable"]
        expected = {
            "name": metadata.name,
            "description": metadata.description,
            "author": metadata.author,
            "version": metadata.version,
            "sha1": metadata.sha1,
        }
        actual = {
            "name": package.get("name"),
            "description": package.get("description"),
            "author": package.get("author"),
            "version": release.get("version"),
            "sha1": release.get("files", [{}])[0].get("sha1"),
        }
        if actual != expected:
            raise SyncError(
                f"{namespace} 的 feed 元数据与 Lua 不一致：{actual!r} != {expected!r}。"
            )


def synchronize(
    base: str,
    head: str,
    release_date: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    feed = load_json(FEED_PATH)
    validate_feed_shape(feed)
    changed_scripts, changed_packages = changed_paths(base, head)
    script_paths = sorted(MACROS_DIR.glob("*.lua"))
    if not script_paths:
        raise SyncError("macros/ 中没有 Lua 脚本。")
    scripts = {metadata.namespace: metadata for metadata in map(parse_script, script_paths)}
    if len(scripts) != len(script_paths):
        raise SyncError("检测到重复的 script_namespace。")

    feed_macros = feed.get("macros", {})
    if not isinstance(feed_macros, dict):
        raise SyncError("DependencyControl.json 的 macros 必须是对象。")
    # Recover cleanly when an earlier publishing run failed before creating the
    # feed entry. A script absent from the feed is still an unpublished script,
    # even when its Lua file was introduced by an older commit.
    missing_feed_namespaces = set(scripts) - set(feed_macros)
    for namespace, metadata in scripts.items():
        if namespace in missing_feed_namespaces:
            changed_scripts.add(metadata.path.relative_to(ROOT).as_posix())

    release_namespaces: set[str] = set()
    changelogs: dict[str, list[str]] = {}

    for relative_path in sorted(changed_scripts):
        current_path = ROOT / relative_path
        if not current_path.exists():
            raise SyncError(f"检测到脚本删除：{relative_path}。")
        current = parse_script(current_path)
        previous_bytes = git_file(base, relative_path) if git_object_exists(base) else None
        if current.namespace in missing_feed_namespaces:
            release_namespaces.add(current.namespace)
        elif previous_bytes is None:
            release_namespaces.add(current.namespace)
        else:
            previous = parse_script_bytes(Path(relative_path), previous_bytes)
            if current.version_tuple < previous.version_tuple:
                raise SyncError(
                    f"{relative_path} 的版本从 {previous.version} 倒退到 {current.version}。"
                )
            if current.version_tuple == previous.version_tuple:
                if current_path.read_bytes() != previous_bytes:
                    raise SyncError(
                        f"{relative_path} 内容已变化，但 script_version 仍是 {current.version}。"
                    )
            else:
                release_namespaces.add(current.namespace)

        revision_range = head if not git_object_exists(base) else f"{base}..{head}"
        subjects = commit_subjects(revision_range, relative_path)
        changelogs[current.namespace] = subjects or [f"发布 {current.version}"]

    package_namespaces = {
        Path(path).stem for path in changed_packages if (ROOT / path).exists()
    }
    unknown_overrides = sorted(package_namespaces - set(scripts))
    if unknown_overrides:
        raise SyncError(
            f"包配置没有对应 Lua 脚本：{', '.join(unknown_overrides)}。"
        )

    macros = feed.setdefault("macros", {})
    if not isinstance(macros, dict):
        raise SyncError("DependencyControl.json 的 macros 必须是对象。")

    release_plan: list[dict[str, Any]] = []
    for namespace, metadata in sorted(scripts.items()):
        existing = macros.get(namespace)
        existing_release = (
            existing.get("channels", {}).get("stable", {})
            if isinstance(existing, dict)
            else {}
        )
        existing_date = existing_release.get("released")
        effective_date = (
            release_date
            if namespace in release_namespaces or not existing_date
            else existing_date
        )
        macros[namespace] = package_for_script(
            metadata,
            existing,
            effective_date,
            changelogs.get(namespace, []) if namespace in release_namespaces else [],
        )

        if namespace in release_namespaces:
            previous_tag = latest_package_tag(namespace)
            notes_range = f"{previous_tag}..{head}" if previous_tag else head
            release_notes = commit_subjects(
                notes_range, metadata.path.relative_to(ROOT).as_posix()
            ) or changelogs[namespace]
            release_plan.append(
                {
                    "namespace": namespace,
                    "name": metadata.name,
                    "version": metadata.version,
                    "tag": f"{namespace}-v{metadata.version}",
                    "sha1": metadata.sha1,
                    "file": metadata.path.relative_to(ROOT).as_posix(),
                    "raw_url": (
                        f"{RAW_ROOT}/{namespace}-v{metadata.version}/"
                        f"macros/{namespace}.lua"
                    ),
                    "changelog": changelogs[namespace],
                    "release_notes": release_notes,
                }
            )

    stale = sorted(set(macros) - set(scripts))
    if stale:
        raise SyncError(f"feed 含不存在的脚本：{', '.join(stale)}。")

    validate_generated_feed(feed, scripts)
    return feed, release_plan


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=ZERO_SHA, help="推送/PR 前的 Git commit")
    parser.add_argument("--head", default="HEAD", help="要检查的 Git commit")
    parser.add_argument(
        "--date",
        default=dt.datetime.now(dt.timezone.utc).date().isoformat(),
        help="写入 feed 的 UTC 发布日期",
    )
    parser.add_argument("--write", action="store_true", help="写回 DependencyControl.json")
    parser.add_argument("--plan", type=Path, help="写出机器可读的发布计划 JSON")
    parser.add_argument(
        "--skip-schema", action="store_true", help="仅供离线单元测试使用"
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        parse_semver("0.0.0")  # keep semantic validation initialized early
        feed, plan = synchronize(args.base, args.head, args.date)
        if not args.skip_schema:
            validate_with_official_schema(feed)
        if args.write:
            write_json(FEED_PATH, feed)
        if args.plan:
            output_path = args.plan if args.plan.is_absolute() else ROOT / args.plan
            write_json(output_path, {"releases": plan})
        print(
            f"DependencyControl feed 校验通过；检测到 {len(plan)} 个待发布脚本。",
            file=sys.stdout,
        )
        for release in plan:
            print(f"  - {release['tag']}", file=sys.stdout)
        return 0
    except SyncError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
