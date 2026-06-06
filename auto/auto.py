#!/usr/bin/env python3
################################################################################
# auto.py is the commandline utility that assists developers with setting
# up their environment for local development.
#
# Author: Kenny Pyatt
################################################################################

"""Modules"""

import os
import sys

from autocli import commands
from pyfiglet import Figlet
from rich import print as rprint


def _force_utf8_stdout():
    """Make stdout/stderr UTF-8 on Windows so rich's emoji/box-drawing don't crash.

    The default Windows stdout encoding can be a legacy code page (e.g. cp1252)
    that can't encode characters like check marks or box-drawing glyphs; writing
    them raises UnicodeEncodeError. Reconfiguring to UTF-8 lets output render in
    Windows Terminal and degrade gracefully elsewhere.
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main():
    """This is the iniatial step of the `auto` cli"""

    _force_utf8_stdout()

    if "_AUTO_COMPLETE" in os.environ:
        # Skip banner and let Click handle completion
        pass
    else:
        # Print a fancy header
        fig = Figlet(font="small")
        rprint("[dodger_blue2]" + fig.renderText("auto"))
    commands.auto()


if __name__ == "__main__":
    main()
