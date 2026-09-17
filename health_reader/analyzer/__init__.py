"""Analyzer package.

Re-export the CLI module's public API so ``import analyzer`` resolves the
package and still exposes ``analyzer.load_config`` / ``analyzer.run_file``
(the ``analyzer/`` package and the sibling ``analyzer.py`` module would
otherwise shadow each other under pytest's package import).
"""
from .analyzer import *  # noqa: F401,F403
from .analyzer import load_config, run_file, main, CONFIG_DEFAULTS  # noqa: F401
