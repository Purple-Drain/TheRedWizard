"""#293: debrid daily allowance cooldown. Run directly: python3 tests/test_debrid_allowance.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'plugin.video.redlight', 'resources', 'lib'))
from modules import debrid_allowance as da

store = {}
props = (lambda k: store.get(k, ''), lambda k, v: store.__setitem__(k, v), lambda k: store.pop(k, None))
checks = 0
def check(cond, label):
	global checks
	assert cond, label
	checks += 1

check(da.is_allowance_error('TorBox', {'success': False, 'error': 'DAILY_BANDWIDTH_LIMIT_EXCEEDED'}), 'tb dict')
check(da.is_allowance_error('TorBox', 'DAILY_BANDWIDTH_LIMIT_EXCEEDED'), 'tb text')
check(not da.is_allowance_error('TorBox', {'success': False, 'error': 'DATABASE_ERROR'}), 'tb other error')
check(not da.is_allowance_error('TorBox', None), 'tb none')
check(da.is_allowance_error('Real-Debrid', {'error': 'traffic_exhausted', 'error_code': 36}), 'rd traffic')
check(not da.is_allowance_error('Real-Debrid', {'error': 'too_many_requests', 'error_code': 34}), 'rd rate limit is not a cooldown')
check(not da.is_allowance_error('Real-Debrid', {'error': 'hoster_unavailable', 'error_code': 19}), 'rd hoster')

# 23:30 UTC: reset in 30 min beats the 1 h cap. 10:00 UTC: capped at 1 h.
late = 1761348600  # 2025-10-24 23:30:00 UTC
check(da.cooldown_until(late) == late + 1800, 'reset before cap')
early = late - 13.5 * 3600
check(da.cooldown_until(early) == early + 3600, 'cap before reset')

until = da.mark_spent('TorBox', now=early, props=props)
check(da.spent_until('TorBox', now=early + 10, props=props) == int(until), 'cooling')
check(da.spent_until('Real-Debrid', now=early + 10, props=props) is None, 'other provider unaffected')
check(da.spent_until('TorBox', now=until + 1, props=props) is None, 'expired')
check(da.PROP_PREFIX + 'TorBox' not in store, 'expired entry cleared')
check('retry after' in da.spent_error('TorBox', until)['error'], 'dialog text')
print('%d checks passed' % checks)
