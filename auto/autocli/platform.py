"""Platform helpers: a single source of truth for OS-specific facts and paths.

This module isolates everywhere `auto` needs to know about the host operating
system, so the rest of the codebase can stay platform-agnostic. It is reachable
only as ``autocli.platform`` (Python 3 uses absolute imports, so a bare
``import platform`` elsewhere still resolves to the stdlib module). We rely on
``sys``/``os`` here and deliberately do not import the stdlib ``platform``.
"""

import os
import shutil
import sys
from pathlib import Path

# Basic OS flags used across the codebase.
IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def home():
    """Return the user's home directory."""
    return os.path.expanduser("~")


def auto_dir(*parts):
    """Build a path inside the ~/.auto data directory using the native separator.

    Replaces the ``os.path.expanduser("~") + "/.auto/..."`` string concatenation
    scattered through the codebase, which produced mixed-separator paths on
    Windows.
    """
    return os.path.join(home(), ".auto", *parts)


def hosts_path():
    """Return the path to the system hosts file for this platform."""
    if IS_WINDOWS:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        return os.path.join(system_root, "System32", "drivers", "etc", "hosts")
    return "/etc/hosts"


def read_hosts():
    """Read the system hosts file, returning "" if it can't be read.

    Reading is done in pure Python (no `cat`) and never raises: on a locked-down
    Windows box the hosts file may be unreadable without elevation, in which case
    callers fall back to DNS resolution.
    """
    try:
        with open(hosts_path(), encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def which(name):
    """Resolve an executable on PATH (cross-platform), or None if missing."""
    return shutil.which(name)


def _resolve_bin(name, posix_fallback):
    """Resolve a tool from PATH, else a known POSIX location, else the bare name.

    Returning the bare name as a last resort lets subprocess do its own PATH
    lookup; dependency checks (check_k8s/check_docker) use ``which`` directly to
    report a clear error when a tool is genuinely missing.
    """
    found = shutil.which(name)
    if found:
        return found
    if not IS_WINDOWS and os.path.exists(posix_fallback):
        return posix_fallback
    return name


def k3d_bin():
    """Path to the k3d binary (replaces the hardcoded /usr/local/bin/k3d)."""
    return _resolve_bin("k3d", "/usr/local/bin/k3d")


def docker_bin():
    """Path to the docker binary."""
    return _resolve_bin("docker", "/usr/bin/docker")


def to_mount_path(path):
    """Convert a host path into the form k3d/Docker expects as a --volume source.

    On Windows a path like ``C:\\Users\\me\\source`` must use forward slashes
    (``C:/Users/me/source``) so the drive-letter colon is not mistaken for k3d's
    ``source:dest`` separator. The value must be passed as a single argv element
    (no shell re-split) to keep the colon intact.
    """
    return Path(path).expanduser().resolve().as_posix()


def posix_path(path):
    """Expand ~ and return a forward-slash path for tool flags (-f, --registry-config).

    kubectl/k3d accept forward slashes on every platform, so this yields a value
    that is safe to hand to them whether or not a shell is involved.
    """
    return Path(path).expanduser().as_posix()


def is_admin():
    """Return True if the current process has administrative/root privileges."""
    if IS_WINDOWS:
        try:
            import ctypes  # pylint: disable=import-outside-toplevel

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # pylint: disable=broad-except
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False
