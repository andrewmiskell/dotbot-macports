import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.request
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

import dotbot
from dotbot.util.common import shell_command

RELEASES_API = "https://api.github.com/repos/macports/macports-base/releases"
DEFAULT_PREFIX = "/opt/local"


class MacPorts(dotbot.Plugin):
    """
    Install MacPorts and ports.
    """

    supports_dry_run = True

    _defaults: Mapping[str, Mapping[str, Any]] = {
        "install-macports": {
            "stdin": True,
            "stdout": True,
            "stderr": True,
        },
        "port-selfupdate": {
            "stdin": True,
            "stdout": False,
            "stderr": True,
        },
        "port": {
            "stdin": True,
            "stdout": False,
            "stderr": True,
        },
        "portfile": {
            "stdin": True,
            "stdout": False,
            "stderr": True,
        },
        "port-select": {
            "stdin": True,
            "stdout": False,
            "stderr": True,
        },
    }

    # options shared by every directive
    _common_defaults: Mapping[str, Any] = {
        "prefix": DEFAULT_PREFIX,
        "sudo": True,
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._directives: Dict[str, Callable[[Any, Dict[str, Any]], bool]] = {
            "install-macports": self._install_macports,
            "port-selfupdate": self._selfupdate,
            "port": self._port,
            "portfile": self._portfile,
            "port-select": self._port_select,
        }
        self._installed_cache: Optional[Dict[str, Set[str]]] = None

    def can_handle(self, directive: str) -> bool:
        return directive in self._directives

    def handle(self, directive: str, data: Any) -> bool:
        if directive not in self._directives:
            msg = f"MacPorts cannot handle directive {directive}"
            raise ValueError(msg)
        user_defaults = self._context.defaults().get(directive, {})
        defaults = {
            **self._common_defaults,
            **self._defaults.get(directive, {}),
            **user_defaults,
        }
        return self._directives[directive](data, defaults)

    # ------------------------------------------------------------------ helpers

    def _port_bin(self, defaults: Mapping[str, Any]) -> str:
        return os.path.join(defaults["prefix"], "bin", "port")

    def _have_port(self, defaults: Mapping[str, Any]) -> bool:
        return os.access(self._port_bin(defaults), os.X_OK)

    def _sudo(self, defaults: Mapping[str, Any]) -> str:
        return "sudo " if defaults["sudo"] else ""

    def _run(self, cmd: str, defaults: Mapping[str, Any]) -> int:
        stdout = defaults["stdout"]
        stderr = defaults["stderr"]
        if self._context.options().verbose > 1:
            stdout = stderr = True
        return shell_command(
            cmd,
            cwd=self._context.base_directory(),
            enable_stdin=defaults["stdin"],
            enable_stdout=stdout,
            enable_stderr=stderr,
        )

    def _action(self, msg: str) -> None:
        if self._context.dry_run():
            msg = "Would " + msg[0].lower() + msg[1:]
        self._log.action(msg)

    @staticmethod
    def _parse_spec(spec: str) -> Tuple[str, List[str], List[str]]:
        """
        Split a port spec like "vim +huge -x11 configure.cc=clang" into the
        port name, the requested variants, and any remaining arguments.
        """
        tokens = shlex.split(spec)
        name = tokens[0]
        variants: List[str] = []
        rest: List[str] = []
        for tok in tokens[1:]:
            if re.fullmatch(r"([+-][A-Za-z0-9_]+)+", tok):
                variants.extend(re.findall(r"[+-][A-Za-z0-9_]+", tok))
            else:
                rest.append(tok)
        return name, variants, rest

    def _installed(self, defaults: Mapping[str, Any]) -> Dict[str, Set[str]]:
        """
        Map each active port to its set of enabled variants. One call to
        `port installed active` covers every port, which is much faster than
        asking about each port separately.
        """
        if self._installed_cache is not None:
            return self._installed_cache
        installed: Dict[str, Set[str]] = {}
        if self._have_port(defaults):
            out = subprocess.run(  # noqa: S603
                [self._port_bin(defaults), "-q", "installed", "active"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            # lines look like: "  vim @9.1.0_0+huge+python312 (active)"
            for line in out.splitlines():
                m = re.match(r"\s*(\S+)\s+@\S*?((?:\+[A-Za-z0-9_]+)*)\s", line + " ")
                if m:
                    installed[m[1]] = set(re.findall(r"\+([A-Za-z0-9_]+)", m[2]))
        self._installed_cache = installed
        return installed

    def _is_satisfied(self, name: str, variants: List[str], defaults: Mapping[str, Any]) -> bool:
        installed = self._installed(defaults)
        if name not in installed:
            return False
        have = installed[name]
        for v in variants:
            if v[0] == "+" and v[1:] not in have:
                return False
            if v[0] == "-" and v[1:] in have:
                return False
        return True

    # --------------------------------------------------------------- directives

    def _install_macports(self, data: Any, defaults: Dict[str, Any]) -> bool:
        if isinstance(data, dict):
            defaults = {**defaults, **data}
            version = data.get("version")
        elif data is True:
            version = None
        else:
            self._log.error("install-macports expects `true` or a mapping of options")
            return False

        if self._have_port(defaults):
            self._log.info("MacPorts is already installed")
            return True

        if platform.system() != "Darwin":
            self._log.error("MacPorts can only be installed on macOS")
            return False

        if defaults["prefix"] != DEFAULT_PREFIX:
            self._log.error(
                f"The MacPorts installer only supports {DEFAULT_PREFIX}; "
                f"build from source to use the prefix {defaults['prefix']}"
            )
            return False

        if not shutil.which("xcode-select") or subprocess.call(  # noqa: S603
            ["xcode-select", "-p"],  # noqa: S607
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) != 0:
            self._log.warning(
                "Xcode Command Line Tools were not found; MacPorts needs them. "
                "Run `xcode-select --install` first."
            )

        try:
            asset_name, url, release = self._find_installer(version)
        except Exception as e:  # noqa: BLE001
            self._log.error(f"Could not find a MacPorts installer: {e}")
            return False

        if self._context.dry_run():
            self._log.action(f"Would install MacPorts from {asset_name}")
            return True

        self._log.action(f"Installing MacPorts from {asset_name}")
        with tempfile.TemporaryDirectory(prefix="dotbot-macports-") as tmp:
            pkg = os.path.join(tmp, asset_name)
            try:
                urllib.request.urlretrieve(url, pkg)  # noqa: S310
                self._verify_checksum(pkg, asset_name, release)
            except Exception as e:  # noqa: BLE001
                self._log.error(f"Could not download MacPorts: {e}")
                return False

            cmd = f"{self._sudo(defaults)}installer -pkg {shlex.quote(pkg)} -target /"
            if self._run(cmd, defaults) != 0:
                self._log.error("The MacPorts installer failed")
                return False

        self._log.info("MacPorts has been installed")
        return True

    def _find_installer(self, version: Optional[str]) -> Tuple[str, str, Dict[str, Any]]:
        if version:
            api = f"{RELEASES_API}/tags/v{str(version).lstrip('v')}"
        else:
            api = f"{RELEASES_API}/latest"
        release = self._fetch_json(api)

        macos = platform.mac_ver()[0]
        parts = macos.split(".")
        # releases are named by major version from Big Sur on (11-BigSur,
        # 27-GoldenGate) and by major.minor before that (10.15-Catalina)
        os_key = ".".join(parts[:2]) if parts[0] == "10" else parts[0]
        pattern = re.compile(rf"^MacPorts-[\d.]+-{re.escape(os_key)}-[A-Za-z]+\.pkg$")
        for asset in release.get("assets", []):
            if pattern.match(asset["name"]):
                return asset["name"], asset["browser_download_url"], release
        msg = (
            f"release {release.get('tag_name')} has no installer for macOS {macos}; "
            "see https://www.macports.org/install.php"
        )
        raise RuntimeError(msg)

    def _verify_checksum(self, pkg: str, asset_name: str, release: Dict[str, Any]) -> None:
        chk = next(
            (a for a in release.get("assets", []) if a["name"].endswith(".chk.txt")),
            None,
        )
        if chk is None:
            self._log.warning("No checksum file in this release; skipping verification")
            return
        with urllib.request.urlopen(chk["browser_download_url"]) as resp:  # noqa: S310
            text = resp.read().decode()
        m = re.search(rf"^SHA2-256\({re.escape(asset_name)}\)=\s*([0-9a-f]{{64}})", text, re.M)
        if not m:
            self._log.warning(f"No SHA-256 for {asset_name}; skipping verification")
            return
        h = hashlib.sha256()
        with open(pkg, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != m[1]:
            msg = f"checksum mismatch for {asset_name}"
            raise RuntimeError(msg)
        self._log.debug(f"Verified SHA-256 of {asset_name}")

    @staticmethod
    def _fetch_json(url: str) -> Dict[str, Any]:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})  # noqa: S310
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            return json.load(resp)

    def _selfupdate(self, data: Any, defaults: Dict[str, Any]) -> bool:
        if not data:
            return True
        if not self._have_port(defaults) and not self._context.dry_run():
            self._log.error("MacPorts is not installed")
            return False
        cmd = f"{self._sudo(defaults)}{self._port_bin(defaults)} selfupdate"
        self._action(f"Update MacPorts [{cmd}]")
        if self._context.dry_run():
            return True
        if self._run(cmd, defaults) != 0:
            self._log.error("port selfupdate failed")
            return False
        self._log.info("MacPorts has been updated")
        return True

    def _port(self, data: Any, defaults: Dict[str, Any]) -> bool:
        if not isinstance(data, list):
            self._log.error("port expects a list of ports")
            return False
        if not self._have_port(defaults) and not self._context.dry_run():
            self._log.error("MacPorts is not installed (add `install-macports: true` first)")
            return False

        success = True
        for spec in data:
            if not isinstance(spec, str) or not spec.strip():
                self._log.error(f"Invalid port entry: {spec!r}")
                success = False
                continue
            name, variants, rest = self._parse_spec(spec)
            if self._is_satisfied(name, variants, defaults):
                self._log.debug(f"{spec} is already installed")
                continue

            args = " ".join(shlex.quote(t) for t in [name, *variants, *rest])
            cmd = f"{self._sudo(defaults)}{self._port_bin(defaults)} -N install {args}"
            self._action(f"Install {spec}")
            if self._context.dry_run():
                continue
            if self._run(cmd, defaults) != 0:
                self._log.warning(f"Failed to install [{spec}]")
                success = False
            else:
                # re-read on next check so default variants are picked up too
                self._installed_cache = None

        if success:
            self._log.info("All ports have been installed")
        else:
            self._log.error("Some ports were not installed")
        return success

    def _portfile(self, data: Any, defaults: Dict[str, Any]) -> bool:
        """
        Install ports listed in plain-text files, one spec per line. Blank
        lines and anything after a `#` are ignored. A file in the format of
        `port -q installed requested` output also works.
        """
        if isinstance(data, str):
            data = [data]
        specs: List[str] = []
        for path in data:
            full = os.path.join(self._context.base_directory(), os.path.expanduser(path))
            try:
                with open(full) as f:
                    lines = f.read().splitlines()
            except OSError as e:
                self._log.error(f"Could not read port file {path}: {e}")
                return False
            for line in lines:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                # accept "name @version+variants (active)" from `port installed`
                m = re.match(r"(\S+)\s+@\S*?((?:\+[A-Za-z0-9_]+)*)(?:\s|$)", line)
                if m:
                    line = m[1] + (" " + m[2] if m[2] else "")
                specs.append(line)
        return self._port(specs, defaults)

    def _port_select(self, data: Any, defaults: Dict[str, Any]) -> bool:
        if not isinstance(data, dict):
            self._log.error("port-select expects a mapping of group: version")
            return False
        if not self._have_port(defaults) and not self._context.dry_run():
            self._log.error("MacPorts is not installed")
            return False

        success = True
        for group, selection in data.items():
            if self._selected(group, defaults) == selection:
                self._log.debug(f"{group} is already set to {selection}")
                continue
            cmd = (
                f"{self._sudo(defaults)}{self._port_bin(defaults)} select --set "
                f"{shlex.quote(str(group))} {shlex.quote(str(selection))}"
            )
            self._action(f"Select {selection} for {group}")
            if self._context.dry_run():
                continue
            if self._run(cmd, defaults) != 0:
                self._log.warning(f"Failed to select {selection} for {group}")
                success = False

        if success:
            self._log.info("All port selections have been set")
        else:
            self._log.error("Some port selections were not set")
        return success

    def _selected(self, group: str, defaults: Mapping[str, Any]) -> Optional[str]:
        if not self._have_port(defaults):
            return None
        out = subprocess.run(  # noqa: S603
            [self._port_bin(defaults), "-q", "select", "--show", str(group)],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        # "The currently selected version for 'python' is 'python312'."
        m = re.search(r"'([^']+)'\.?\s*$", out.strip())
        return m[1] if m else (out.strip() or None)
