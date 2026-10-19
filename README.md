# dotbot-macports

A [Dotbot](https://github.com/anishathalye/dotbot) plugin for [MacPorts](https://www.macports.org).
It works like [dotbot-brew](https://github.com/wren/dotbot-brew) and adds these directives:

| Directive          | What it does                                                        |
| ------------------ | ------------------------------------------------------------------- |
| `install-macports` | Installs MacPorts from the official `.pkg` if it isn't installed     |
| `port-selfupdate`  | Runs `port selfupdate`                                               |
| `port`             | Installs ports, with variants (`vim +huge`)                          |
| `portfile`         | Installs ports listed in a text file, one per line                   |
| `port-select`      | Sets `port select` groups (`python: python312`)                      |

## Features

- **Fast checks for installed ports.** The plugin calls `port installed active` once and
  reuses the result, so ports that are already installed are skipped right away.
- **Variant-aware.** `vim +huge` installs again if the active `vim` doesn't have `+huge`,
  and `-x11` installs again if the active port has `+x11`.
- **Installs MacPorts from scratch.** `install-macports` finds the installer for your
  macOS version in the latest
  [macports-base release](https://github.com/macports/macports-base/releases) (or a
  version you pin), checks its SHA-256 against the release's checksum file, and runs it
  with `installer`. Nothing happens if MacPorts is already installed.
- **Supports dry runs** (`dotbot --dry-run`).

## Installation

Add the plugin as a submodule of your dotfiles repository:

```shell
git submodule add https://github.com/andrewmiskell/dotbot-macports.git
```

Then load it from your `install` script:

```shell
"${BASEDIR}/${DOTBOT_DIR}/${DOTBOT_BIN}" -d "${BASEDIR}" --plugin-dir dotbot-macports -c "${CONFIG}" "${@}"
```

You can also use `--plugin dotbot-macports/macports.py`.

## Usage

```yaml
- defaults:
    port:
      stdout: true

- install-macports: true

- port-selfupdate: true

- port:
    - git
    - ripgrep
    - vim +huge +python312
    - python312
    - py312-pip

- portfile:
    - macports/requested.txt

- port-select:
    python: python312
    python3: python312
    pip3: pip312
```

### `install-macports`

Use `true` to install the latest release, or a mapping to pin a version:

```yaml
- install-macports:
    version: 2.12.7
```

You need the Xcode Command Line Tools (`xcode-select --install`) first. The plugin warns
if they are missing.

### `port`

A list of port specs. Each spec is a port name, then any variants and other `port install`
arguments, the same as you would type on the command line. Installs run with `-N`
(non-interactive), so MacPorts doesn't stop to ask questions.

### `portfile`

One or more text files, with paths relative to your dotfiles directory. Each line is a
port spec. Blank lines and anything after `#` are ignored. Output from
`port -q installed requested` also works as is, so you can save your current setup with:

```shell
port -q installed requested > macports/requested.txt
```

### `port-select`

A mapping from `port select` group to the selection you want. Groups that are already set
are skipped.

## Options

Set options for each directive under `defaults`. `install-macports` also takes them inline.

| Option   | Default       | Meaning                                              |
| -------- | ------------- | ---------------------------------------------------- |
| `sudo`   | `true`        | Prefix commands with `sudo`                          |
| `prefix` | `/opt/local`  | MacPorts prefix; `port` is run from `<prefix>/bin`   |
| `stdin`  | `true`        | Connect stdin (needed for the `sudo` password prompt) |
| `stdout` | `false`¹      | Show command output                                  |
| `stderr` | `true`        | Show command errors                                  |

¹ `true` for `install-macports`. Running dotbot with `-vv` shows all output.

The plugin uses `<prefix>/bin/port` directly, so it works right after `install-macports`
in the same run, before your shell `PATH` includes `/opt/local/bin`.

To avoid repeated password prompts, run `sudo -v` before dotbot, for example from your
`install` script.

## Testing

```shell
pip install dotbot pytest
pytest
```

The tests use a fake `port` script, so they don't need MacPorts or root.
