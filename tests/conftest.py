# -*- coding: utf-8 -*-
"""pytest harness for plugin.video.redlight's resources/lib (#70).

Installs the same Kodi stubs the standalone scripts use (kodi_stub.py -- the
single stub set for this repo, do not add another) and puts resources/lib on
sys.path before any test imports addon code, so pytest-style tests can import
the real shipped functions directly:

    from modules import source_utils

The pre-existing standalone scripts (each runnable as python3 tests/test_X.py)
are collected via test_standalone_scripts.py, so one command runs everything:

    python3 -m pytest tests/
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
import kodi_stub  # noqa: E402

kodi_stub.install()
sys.path.insert(0, os.path.join(ROOT, 'plugin.video.redlight', 'resources', 'lib'))

# The standalone scripts run their checks at module level and sys.exit on
# failure, which under pytest would abort collection itself. Keep pytest from
# importing them; test_standalone_scripts.py runs each in its own interpreter.
collect_ignore = [
    name for name in os.listdir(HERE)
    if name.startswith('test_') and name.endswith('.py') and name != 'test_standalone_scripts.py'
    and 'def test_' not in open(os.path.join(HERE, name), encoding='utf-8').read()
]
