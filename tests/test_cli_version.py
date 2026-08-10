"""CLI version 命令测试 —— 此前 0 测试覆盖（基建审计 2026-08-08）。

覆盖：liquid-loop version 命令输出当前版本号（依赖 package __version__）。
"""
from click.testing import CliRunner
from pathlib import Path
import tomllib

from liquid_loop.cli import main
import liquid_loop


def _pkg_version() -> str:
    """从 pyproject.toml 动态读取版本（避免硬编码致升版本即红）。"""
    p = Path(__file__).parent.parent / "pyproject.toml"
    return tomllib.loads(p.read_text(encoding="utf-8")).get("project", {}).get("version", "")


def test_version_command():
    r = CliRunner().invoke(main, ["version"])
    assert r.exit_code == 0
    assert f"liquid-loop {liquid_loop.__version__}" in r.output


def test_version_matches_package():
    r = CliRunner().invoke(main, ["version"])
    assert r.exit_code == 0
    assert liquid_loop.__version__ == _pkg_version()
    assert f"liquid-loop {liquid_loop.__version__}" in r.output
