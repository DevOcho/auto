# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html
# pylint: disable=invalid-name,missing-module-docstring,redefined-builtin

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information
import os
import sys

import sphinx_theme

# Setup the correct path
sys.path.insert(0, os.path.abspath("../../auto"))

project = "Auto"
copyright = "2022, Kenny Pyatt"
author = "Kenny Pyatt"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = ["sphinx.ext.autodoc", "myst_parser"]

templates_path = ["_templates"]
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output
html_theme = "stanford_theme"
html_theme_path = [sphinx_theme.get_html_theme_path("stanford-theme")]

# html_theme = 'alabaster'
html_static_path = ["_static"]
