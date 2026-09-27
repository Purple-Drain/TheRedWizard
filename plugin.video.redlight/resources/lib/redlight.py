# -*- coding: utf-8 -*-
import sys
import time
_t0 = time.time()
from urllib.parse import parse_qsl
from modules.router import routing, sys_exit_check
if len(sys.argv) > 2 and 'nextep_stash_play=true' in sys.argv[2]:
	# #196: timing for the autoplay handoff; entry time is this line's log stamp minus the import cost.
	from modules.kodi_utils import logger
	logger('Red Light', 'Autoplay next episode play: plugin entry, imports %.2fs' % (time.time() - _t0))
# from modules.kodi_utils import logger

routing(sys)
params = dict(parse_qsl(sys.argv[2][1:], keep_blank_values=True)) if len(sys.argv) > 2 else {}
mode = params.get('mode', 'navigator.main')
if sys_exit_check(mode): sys.exit(1)
