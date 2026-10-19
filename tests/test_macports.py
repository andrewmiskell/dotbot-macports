import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parent.parent / "macports.py"

FAKE_PORT = textwrap.dedent(
    """\
    #!/bin/sh
    # Fake `port`: logs each call and keeps state in files next to itself.
    dir=$(dirname "$0")
    echo "$*" >> "$dir/calls.log"
    case "$*" in
      "-q installed active") cat "$dir/installed" 2>/dev/null ;;
      "-q select --show "*)
        group=$(echo "$*" | awk '{print $4}')
        sel=$(cat "$dir/select-$group" 2>/dev/null || echo none)
        echo "The currently selected version for '$group' is '$sel'." ;;
      "select --set "*)
        echo "$4" > "$dir/select-$3" ;;
      "-N install "*)
        case "$3" in broken) exit 1 ;; esac
        # pretend MacPorts added a default variant
        [ "$3" = vim ] && echo "  vim @9.1.0_0+huge+python312 (active)" > "$dir/installed" ;;
    esac
    exit 0
    """
)


@pytest.fixture
def env(tmp_path):
    prefix = tmp_path / "prefix"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    port = bindir / "port"
    port.write_text(FAKE_PORT)
    port.chmod(port.stat().st_mode | stat.S_IEXEC)
    base = tmp_path / "dotfiles"
    base.mkdir()

    class Env:
        def __init__(self):
            self.bindir = bindir
            self.base = base

        def installed(self, text):
            (bindir / "installed").write_text(textwrap.dedent(text))

        def calls(self):
            log = bindir / "calls.log"
            lines = log.read_text().splitlines() if log.exists() else []
            return [c for c in lines if not c.startswith("-q ")]

        def run(self, config, *args):
            defaults = {"prefix": str(prefix), "sudo": False}
            full = [
                {
                    "defaults": {
                        d: dict(defaults)
                        for d in ["install-macports", "port-selfupdate", "port", "portfile", "port-select"]
                    }
                },
                *config,
            ]
            conf = base / "install.conf.yaml"
            conf.write_text(yaml.safe_dump(full))
            return subprocess.run(
                [str(Path(sys.executable).parent / "dotbot"), "-d", str(base), "-c", str(conf), "--plugin", str(PLUGIN), *args],
                capture_output=True,
                text=True,
                check=False,
            )

    return Env()


def test_installs_missing_ports_only(env):
    env.installed("  git @2.47.0_0+credential_osxkeychain (active)\n")
    r = env.run([{"port": ["git", "ripgrep"]}])
    assert r.returncode == 0, r.stdout + r.stderr
    assert env.calls() == ["-N install ripgrep"]


def test_variants_trigger_reinstall(env):
    env.installed(
        """\
        vim @9.1.0_0+python312 (active)
        tmux @3.5_0+x11 (active)
        """
    )
    r = env.run([{"port": ["vim +python312", "vim +huge", "tmux -x11"]}])
    assert r.returncode == 0, r.stdout + r.stderr
    assert env.calls() == ["-N install vim +huge", "-N install tmux -x11"]


def test_failure_reported_and_others_continue(env):
    r = env.run([{"port": ["broken", "fzf"]}])
    assert r.returncode != 0
    assert env.calls() == ["-N install broken", "-N install fzf"]
    assert "Failed to install [broken]" in r.stdout + r.stderr


def test_portfile(env):
    env.installed("  bat @0.24.0_0 (active)\n")
    (env.base / "ports.txt").write_text(
        "# comment\nfzf\n\ntmux +x11  # inline\n  bat @0.24.0_0 (active)\n  jq @1.7_0+docs (active)\n"
    )
    r = env.run([{"portfile": ["ports.txt"]}])
    assert r.returncode == 0, r.stdout + r.stderr
    assert env.calls() == ["-N install fzf", "-N install tmux +x11", "-N install jq +docs"]


def test_port_select_skips_current(env):
    (env.bindir / "select-python").write_text("python312\n")
    r = env.run([{"port-select": {"python": "python312", "pip3": "pip312"}}])
    assert r.returncode == 0, r.stdout + r.stderr
    assert env.calls() == ["select --set pip3 pip312"]


def test_selfupdate(env):
    r = env.run([{"port-selfupdate": True}])
    assert r.returncode == 0, r.stdout + r.stderr
    assert env.calls() == ["selfupdate"]


def test_install_macports_noop_when_present(env):
    r = env.run([{"install-macports": True}], "-v")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "already installed" in r.stdout + r.stderr


def test_dry_run_runs_nothing(env):
    r = env.run([{"port": ["fzf"]}, {"port-select": {"python": "python312"}}, {"port-selfupdate": True}], "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert env.calls() == []
    assert "Would install fzf" in r.stdout + r.stderr


def test_missing_macports_errors(env, tmp_path):
    os.remove(env.bindir / "port")
    r = env.run([{"port": ["fzf"]}])
    assert r.returncode != 0
    assert "MacPorts is not installed" in r.stdout + r.stderr


def test_reinstall_not_repeated_after_default_variants(env):
    r = env.run([{"port": ["vim +huge", "vim +python312"]}])
    assert r.returncode == 0, r.stdout + r.stderr
    assert env.calls() == ["-N install vim +huge"]
