"""AINexLayer Marketplace validation library.

Run by hand only (``scripts/validate.py``, ``scripts/compile_index.py``,
``scripts/expand.py``); nothing here runs on GitHub. Standard library only,
Python 3.9 or newer.

The rules mirror AIAgentLayer, the app that installs these packages. Each
constant in :mod:`.rules` names the app file it comes from, so a change on
either side can be traced to the other.
"""
