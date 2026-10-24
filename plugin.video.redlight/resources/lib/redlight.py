# -*- coding: utf-8 -*-
import sys
from urllib.parse import parse_qsl
from modules.router import routing, sys_exit_check, finish_invocation, note_invoker_state
from modules.kodi_utils import external
# from modules.kodi_utils import logger

routing(sys)
params = dict(parse_qsl(sys.argv[2][1:], keep_blank_values=True)) if len(sys.argv) > 2 else {}
mode = params.get('mode', 'navigator.main')
finish_invocation(mode)
_exiting = sys_exit_check(mode)
note_invoker_state(_exiting, not external())
if _exiting: sys.exit(1)
