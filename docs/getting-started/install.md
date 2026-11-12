# Install

auto runs on Linux, macOS (Apple Silicon) and Windows through WSL2. On Linux
it needs an x86-64 or 64-bit ARM machine with glibc 2.39 or newer: Ubuntu
24.04 or newer, Debian 13 or newer, or a current Fedora or Arch.

## What you need first

| Tool | Why | Required |
| --- | --- | --- |
| Docker | The cluster's nodes and the image registry run as containers. The daemon must be running and usable by your user without `sudo`. | Yes |
| [k3d](https://k3d.io) | Creates the k3s cluster in Docker. | Yes |
| kubectl | auto drives the cluster with it, and so can you. | Yes |
| Git | Clones your services' repositories. | Yes |
| Bash or Zsh | auto runs POSIX shell commands. | Yes |
| [mkcert](https://github.com/FiloSottile/mkcert) and certutil | Local HTTPS, which is on by default. certutil is in `libnss3-tools` (Debian, Ubuntu), `nss-tools` (Fedora) or `nss` (Arch, macOS Homebrew). | For HTTPS |
| [Helm](https://helm.sh) | Only for services that deploy with a Helm chart. | For Helm charts |

auto itself is a single binary, so you don't need Python. `auto start` checks
for the tools above and says what is missing.

## Install auto

```bash
curl -fsSL https://www.devocho.com/auto.sh | bash
```

The installer puts auto in `~/.auto` for your user only. No system-wide files
are written. It adds this line to `~/.bashrc` or `~/.zshrc`, whichever
matches your `$SHELL` (`~/.profile` for any other shell):

```bash
# Adding auto to the path
export PATH="$PATH:$HOME/.auto"
```

Open a new terminal, or `source` that file, and check it worked:

```bash
auto --version
```

On macOS the installer also clears the quarantine flag Gatekeeper puts on
downloaded binaries, so auto runs without a "cannot verify developer" prompt.

To install a specific release, set `AUTO_VERSION`:

```bash
curl -fsSL https://www.devocho.com/auto.sh | AUTO_VERSION=0.9.4 bash
```

## Add the registry's hostname

The cluster's image registry is reached as `k3d-registry.local`. Add it to
`/etc/hosts` once (this needs `sudo`):

```text
127.0.0.1 k3d-registry.local
```

`auto start` stops with a message if the line is missing. Each service's
`.local` hostname needs a line too. `auto start` prints the lines you are
still missing after it deploys, so you can add them as you go.

## Turn on tab completion (optional)

auto completes commands, options and pod names in Bash, Zsh and Fish:

```bash
auto autocomplete --shell bash   # prints the line to add; or zsh, fish
auto autocomplete --shell bash --install   # appends it to ~/.bashrc for you
```

## Keep auto up to date

```bash
auto update            # the latest release
auto update 0.8.3      # a specific release, up or down
auto update --dry-run  # show what would change
```

Once a day, when a command finishes in an interactive terminal, auto checks
GitHub for a newer release and prints a one-line notice. Set
`AUTO_NO_UPDATE_CHECK=1` to turn that off.

Updating keeps your `~/.auto/config/local.yaml`. Read the
[release notes](../releases/0.9.0.md) before moving to a new minor version:
some need the cluster recreated.

## Uninstall

```bash
auto stop --delete-cluster
k3d registry delete k3d-registry.local
rm -rf ~/.auto
```

Then remove the `PATH` line from your shell's rc file and the `.local` lines
from `/etc/hosts`. Your code folder is not touched.

Next: [your first cluster](first-cluster.md).
