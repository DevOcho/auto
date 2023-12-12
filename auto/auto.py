#!/usr/bin/env python3
################################################################################
# auto.py is the commandline utility that assists developers with setting
# up their environment for local development.
#
# Author: Kenny Pyatt
################################################################################

"""Modules"""
from autocli import commands
from pyfiglet import Figlet
from rich import print as rprint


def main():
    """This is the iniatial step of the `auto` cli"""

    # Print a fancy header
    fig = Figlet(font="small")
    rprint("[dodger_blue2]" + fig.renderText("auto"))
    commands.auto()


if __name__ == "__main__":
    main()
