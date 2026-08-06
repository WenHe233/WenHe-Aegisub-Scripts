from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "sync_dependency_control.py"
SPEC = importlib.util.spec_from_file_location("sync_dependency_control", MODULE_PATH)
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync
SPEC.loader.exec_module(sync)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def script_text(
    namespace: str,
    version: str,
    *,
    name: str | None = None,
    description: str = "测试脚本",
) -> str:
    name = name or namespace
    return (
        f'script_name = {json.dumps(name, ensure_ascii=False)}\n'
        f'script_description = {json.dumps(description, ensure_ascii=False)}\n'
        'script_author = "WenHe"\n'
        f'script_version = "{version}"\n'
        f'script_namespace = "{namespace}"\n'
        f'script_url = "{sync.REPOSITORY_URL}"\n'
        "aegisub.register_macro(script_name, script_description, function() end)\n"
    )


class RepositoryCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "macros").mkdir()
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Tests")
        git(self.root, "config", "user.email", "tests@example.invalid")
        self.feed = {
            "$schema": sync.SCHEMA_URL,
            "dependencyControlFeedFormatVersion": "0.4.0",
            "name": "Tests",
            "baseUrl": sync.REPOSITORY_URL,
            "url": "@{baseUrl}",
            "fileBaseUrl": (
                f"{sync.RAW_ROOT}/@{{namespace}}-v@{{version}}/"
                "macros/@{namespace}"
            ),
            "macros": {},
        }
        self.write_feed(self.feed)
        self.commit("initial feed")
        self.initial = git(self.root, "rev-parse", "HEAD")

        self.originals = {
            "ROOT": sync.ROOT,
            "MACROS_DIR": sync.MACROS_DIR,
            "PACKAGES_DIR": sync.PACKAGES_DIR,
            "FEED_PATH": sync.FEED_PATH,
        }
        sync.ROOT = self.root
        sync.MACROS_DIR = self.root / "macros"
        sync.PACKAGES_DIR = self.root / "packages"
        sync.FEED_PATH = self.root / "DependencyControl.json"

    def tearDown(self) -> None:
        for key, value in self.originals.items():
            setattr(sync, key, value)
        self.temp.cleanup()

    def write_feed(self, feed: dict) -> None:
        (self.root / "DependencyControl.json").write_text(
            json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def write_script(self, namespace: str, version: str, **kwargs) -> Path:
        path = self.root / "macros" / f"{namespace}.lua"
        path.write_text(script_text(namespace, version, **kwargs), encoding="utf-8")
        return path

    def commit(self, message: str) -> str:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", message)
        return git(self.root, "rev-parse", "HEAD")

    def publish_initial(self, namespace: str = "wenhe.Test", version: str = "1.0.0"):
        self.write_script(namespace, version)
        script_commit = self.commit(f"新增 {namespace}")
        feed, plan = sync.synchronize(self.initial, script_commit, "2026-08-06")
        self.write_feed(feed)
        feed_commit = self.commit("chore(release): update DependencyControl feed [skip ci]")
        git(self.root, "tag", f"{namespace}-v{version}", feed_commit)
        return feed_commit, feed, plan

    def test_new_script_creates_feed_entry_and_release(self) -> None:
        path = self.write_script("wenhe.NewMacro", "1.2.3", name="新宏")
        head = self.commit("新增新宏")

        feed, plan = sync.synchronize(self.initial, head, "2026-08-06")

        package = feed["macros"]["wenhe.NewMacro"]
        self.assertEqual(package["name"], "新宏")
        self.assertEqual(package["channels"]["stable"]["version"], "1.2.3")
        self.assertEqual(
            package["channels"]["stable"]["files"][0]["sha1"],
            sync.sha1_bytes(path.read_bytes()),
        )
        self.assertEqual(plan[0]["tag"], "wenhe.NewMacro-v1.2.3")
        self.assertEqual(plan[0]["changelog"], ["新增新宏"])

    def test_version_upgrade_updates_release(self) -> None:
        base, _, _ = self.publish_initial()
        self.write_script("wenhe.Test", "1.1.0")
        head = self.commit("改进测试宏")

        feed, plan = sync.synchronize(base, head, "2026-08-07")

        release = feed["macros"]["wenhe.Test"]["channels"]["stable"]
        self.assertEqual(release["version"], "1.1.0")
        self.assertEqual(release["released"], "2026-08-07")
        self.assertEqual(plan[0]["release_notes"], ["改进测试宏"])

    def test_multiple_new_scripts_release_together(self) -> None:
        self.write_script("wenhe.First", "1.0.0")
        self.write_script("wenhe.Second", "2.0.0")
        head = self.commit("新增两个宏")

        feed, plan = sync.synchronize(self.initial, head, "2026-08-06")

        self.assertEqual(set(feed["macros"]), {"wenhe.First", "wenhe.Second"})
        self.assertEqual(
            {item["tag"] for item in plan},
            {"wenhe.First-v1.0.0", "wenhe.Second-v2.0.0"},
        )

    def test_missing_feed_entry_recovers_after_earlier_failed_run(self) -> None:
        self.write_script("wenhe.Recovered", "1.0.0")
        script_commit = self.commit("新增待恢复宏")
        unrelated = self.root / "README.md"
        unrelated.write_text("触发新的工作流\n", encoding="utf-8")
        head = self.commit("修复发布工作流")

        feed, plan = sync.synchronize(script_commit, head, "2026-08-06")

        self.assertIn("wenhe.Recovered", feed["macros"])
        self.assertEqual(plan[0]["tag"], "wenhe.Recovered-v1.0.0")

    def test_content_change_without_version_bump_fails(self) -> None:
        base, _, _ = self.publish_initial()
        self.write_script("wenhe.Test", "1.0.0", description="内容已经改变")
        head = self.commit("忘记升级版本")

        with self.assertRaisesRegex(sync.SyncError, "内容已变化"):
            sync.synchronize(base, head, "2026-08-07")

    def test_version_downgrade_fails(self) -> None:
        base, _, _ = self.publish_initial(version="2.0.0")
        self.write_script("wenhe.Test", "1.9.0")
        head = self.commit("错误降级")

        with self.assertRaisesRegex(sync.SyncError, "倒退"):
            sync.synchronize(base, head, "2026-08-07")

    def test_script_deletion_fails(self) -> None:
        base, _, _ = self.publish_initial()
        (self.root / "macros" / "wenhe.Test.lua").unlink()
        head = self.commit("删除脚本")

        with self.assertRaisesRegex(sync.SyncError, "脚本删除"):
            sync.synchronize(base, head, "2026-08-07")

    def test_optional_package_override_is_merged(self) -> None:
        self.write_script("wenhe.WithDependency", "1.0.0")
        packages = self.root / "packages"
        packages.mkdir()
        (packages / "wenhe.WithDependency.json").write_text(
            json.dumps(
                {
                    "release": {
                        "requiredModules": [
                            {"moduleName": "example.Module", "version": "1.0.0"}
                        ]
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        head = self.commit("新增带依赖的宏")

        feed, _ = sync.synchronize(self.initial, head, "2026-08-06")

        release = feed["macros"]["wenhe.WithDependency"]["channels"]["stable"]
        self.assertEqual(release["requiredModules"][0]["moduleName"], "example.Module")

    def test_hash_mismatch_is_rejected(self) -> None:
        _, feed, _ = self.publish_initial()
        broken = copy.deepcopy(feed)
        broken["macros"]["wenhe.Test"]["channels"]["stable"]["files"][0][
            "sha1"
        ] = "0" * 40
        metadata = sync.parse_script(self.root / "macros" / "wenhe.Test.lua")

        with self.assertRaisesRegex(sync.SyncError, "不一致"):
            sync.validate_generated_feed(broken, {metadata.namespace: metadata})

    def test_filename_must_match_namespace(self) -> None:
        wrong = self.root / "macros" / "wrong.lua"
        wrong.write_text(script_text("wenhe.Right", "1.0.0"), encoding="utf-8")
        self.commit("错误文件名")

        with self.assertRaisesRegex(sync.SyncError, "文件名必须"):
            sync.parse_script(wrong)


if __name__ == "__main__":
    unittest.main()
