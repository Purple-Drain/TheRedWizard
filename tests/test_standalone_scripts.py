# -*- coding: utf-8 -*-
"""Run each standalone check script under pytest (#70).

The scripts predate the pytest harness and run module-level checks with their
own pass/fail reporting (python3 tests/test_X.py). Rather than rewriting them,
each runs in its own interpreter here, so `python3 -m pytest tests/` is the one
command that runs the whole suite. New tests should be written pytest-style
(def test_*) and import addon modules directly; conftest.py has the stubs ready.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))

STANDALONE = sorted(
    name for name in os.listdir(HERE)
    if name.startswith('test_') and name.endswith('.py') and name != os.path.basename(__file__)
    and 'def test_' not in open(os.path.join(HERE, name), encoding='utf-8').read()
)


@pytest.mark.parametrize('script', STANDALONE)
def test_standalone_script(script):
    result = subprocess.run([sys.executable, os.path.join(HERE, script)],
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, '%s failed:\n%s\n%s' % (script, result.stdout[-4000:], result.stderr[-4000:])
