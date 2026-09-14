# -*- coding: utf-8 -*-
from xbmc import Monitor
import os
import json
from time import time
from threading import Thread
from caches.settings_cache import get_setting, sync_settings
from modules import kodi_utils

pause_services_prop = 'redlight.pause_services'
current_skin_prop = 'redlight.current_skin'
trakt_service_string = 'TraktMonitor Service Update %s - %s'
trakt_success_line_dict = {'success': 'Trakt Update Performed', 'no account': '(Unauthorised) Trakt Update Performed'}
update_string = 'Next Update in %s minutes...'

def _start_daemon(target):
	Thread(target=target, daemon=True).start()

class SetAddonConstants:
	def run(self):
		kodi_utils.logger('Red Light', 'SetAddonConstants Service Starting')
		import random
		new_version = kodi_utils.addon_info('version')
		prev_version = kodi_utils.get_property('redlight.addon_version')
		if prev_version and prev_version != new_version:
			try:
				from caches.settings_cache import clear_settings_boot_state
				clear_settings_boot_state(clear_deferred=True)
			except: pass
			kodi_utils.clear_addon_xml_sync_version()
			kodi_utils.logger('Red Light', 'SetAddonConstants - version %s -> %s' % (prev_version, new_version))
		icon_choice = get_setting('addon_icon_choice', 'resources/media/addon_icons/icon.png')
		addon_path = kodi_utils.addon_info('path')
		icon_path = kodi_utils.translate_path(os.path.join(addon_path, icon_choice))
		icon_mini = os.path.join(addon_path, 'resources', 'media', 'addon_icons', 'minis', os.path.basename(icon_path))
		addon_items = [
			('redlight.playback_key', str(random.randint(1000, 10000))),
			('redlight.addon_version', new_version),
			('redlight.addon_path', addon_path),
			('redlight.addon_profile', kodi_utils.translate_path(kodi_utils.addon_info('profile'))),
			('redlight.addon_icon', icon_path),
			('redlight.addon_icon_mini', icon_mini),
			('redlight.addon_fanart', kodi_utils.addon_fanart())
					]
		for item in addon_items: kodi_utils.set_property(*item)
		kodi_utils.clear_property('redlight.widgets_refresh_scheduled')
		kodi_utils.clear_property('redlight.language_invoker_ready')
		kodi_utils.clear_property('redlight.addon_xml_applied')
		kodi_utils.clear_property(kodi_utils.SHUTTING_DOWN_PROP)
		kodi_utils.reset_boot_sync_gate()
		try:
			from modules.utils import _prune_qr_cache
			_prune_qr_cache(kodi_utils.translate_path(kodi_utils.addon_info('profile')))
		except: pass
		return kodi_utils.logger('Red Light', 'SetAddonConstants Service Finished')

class DatabaseMaintenance:
	def run(self):
		kodi_utils.logger('Red Light', 'DatabaseMaintenance Service Starting')
		from caches.base_cache import check_databases_integrity
		check_databases_integrity(silent=True)
		return kodi_utils.logger('Red Light', 'DatabaseMaintenance Service Finished')

class SyncSettings:
	def run(self):
		kodi_utils.logger('Red Light', 'SyncSettings Service Starting')
		from caches.settings_cache import sync_settings, settings_sync_needed
		if not settings_sync_needed():
			return kodi_utils.logger('Red Light', 'SyncSettings Service Skipped')
		sync_settings({'load_properties': False})
		return kodi_utils.logger('Red Light', 'SyncSettings Service Finished')

class BootstrapSettings:
	def run(self, monitor):
		from caches.settings_cache import (
			bootstrap_settings_needed, bootstrap_settings_properties,
			refresh_widgets_after_db_migration, run_deferred_setup_background_if_needed,
			service_bootstrap_needed, widgets_refresh_after_migration_needed, _properties_loaded,
		)
		if not service_bootstrap_needed():
			return
		kodi_utils.logger('Red Light', 'BootstrapSettings Service Starting')
		try:
			from modules.sources import clear_orphan_nextep_play_stash
			clear_orphan_nextep_play_stash()
		except:
			pass
		if bootstrap_settings_needed() and not _properties_loaded():
			monitor.waitForAbort(2)
		if kodi_utils.service_shutting_down(monitor): return
		try:
			if bootstrap_settings_needed():
				bootstrap_settings_properties()
			if not kodi_utils.service_shutting_down(monitor) and widgets_refresh_after_migration_needed():
				refresh_widgets_after_db_migration()
			run_deferred_setup_background_if_needed()
		except Exception as e:
			kodi_utils.logger('BootstrapSettings', str(e))
		return kodi_utils.logger('Red Light', 'BootstrapSettings Service Finished')

_custom_windows_thread_started = False

def start_custom_windows_prepare(monitor):
	global _custom_windows_thread_started
	if _custom_windows_thread_started: return
	_custom_windows_thread_started = True
	_start_daemon(lambda: CustomWindowsPrepare().run(monitor))

def run_deferred_service_setup():
	global _custom_windows_thread_started
	kodi_utils.logger('Red Light', 'Deferred Service Setup Starting')
	try:
		from windows.base_window import ExtrasUtils
		ExtrasUtils().run()
	except Exception as e: kodi_utils.logger('DeferredServiceSetup', 'ExtrasUtils: %s' % e)
	return kodi_utils.logger('Red Light', 'Deferred Service Setup Finished')

class CustomWindowsPrepare:
	def run(self, monitor):
		kodi_utils.logger('Red Light', 'CustomWindowsPrepare Service Starting')
		from windows.base_window import FontUtils
		player = kodi_utils.kodi_player()
		wait_for_abort, is_playing = monitor.waitForAbort, player.isPlayingVideo
		kodi_utils.clear_property(current_skin_prop)
		font_utils = FontUtils()
		while not monitor.abortRequested():
			font_utils.execute_custom_fonts()
			wait_for_abort(20)
		try: del player
		except: pass
		return kodi_utils.logger('Red Light', 'CustomWindowsPrepare Service Finished')

class TraktMonitor:
	def run(self, monitor):
		kodi_utils.logger('Red Light', 'TraktMonitor Service Starting')
		from apis.trakt_api import trakt_sync_activities
		from modules.settings import trakt_user_active, trakt_sync_interval
		player = kodi_utils.kodi_player()
		wait_for_abort, is_playing = monitor.waitForAbort, player.isPlayingVideo
		wait_for_abort(45)
		while not monitor.abortRequested():
			while is_playing() or kodi_utils.get_property(pause_services_prop) == 'true': wait_for_abort(10)
			wait_time = 1800
			try:
				from caches.settings_cache import sync_kodi_profile_context
				sync_kodi_profile_context()
				sync_interval, wait_time = trakt_sync_interval()
				next_update_string = update_string % sync_interval
				if trakt_user_active(): status = trakt_sync_activities()
				else: status = 'no_auth'
				if status == 'failed': kodi_utils.logger('Red Light', trakt_service_string % ('Failed. Error from Trakt', next_update_string))
				elif status == 'no_auth': kodi_utils.logger('Red Light', trakt_service_string % ('Not Run. No Current Trakt Account', next_update_string))
				else:
					if status in ('success', 'no account'):
						kodi_utils.logger('Red Light', trakt_service_string % ('Success. %s' % trakt_success_line_dict[status], next_update_string))
					else:
						kodi_utils.logger('Red Light', trakt_service_string % ('Success. No Changes Needed', next_update_string))# 'not needed'
					if status in ('success', 'not needed'):
						kodi_utils.mark_boot_trakt_sync_ready()
					if status == 'success' and not kodi_utils.service_shutting_down(monitor):
						from modules.settings import provider_sync_refresh_widgets
						if provider_sync_refresh_widgets(1):
							try:
								if not kodi_utils.playback_widget_refresh_recent(): kodi_utils.run_plugin({'mode': 'kodi_refresh'})
							except Exception as exc:
								kodi_utils.logger('Red Light', 'Trakt widget refresh skipped: %s' % exc)
			except Exception as e: kodi_utils.logger('Red Light', trakt_service_string % ('Failed', 'The following Error Occured: %s' % str(e)))
			wait_for_abort(wait_time)
		try: del player
		except: pass
		return kodi_utils.logger('Red Light', 'TraktMonitor Service Finished')

class SimklMonitor:
	def run(self, monitor):
		kodi_utils.logger('Red Light', 'SimklMonitor Service Starting')
		from apis.simkl_api import simkl_sync_activities
		from modules.settings import simkl_user_active, simkl_sync_interval
		player = kodi_utils.kodi_player()
		wait_for_abort, is_playing = monitor.waitForAbort, player.isPlayingVideo
		wait_for_abort(45)
		while not monitor.abortRequested():
			while is_playing() or kodi_utils.get_property(pause_services_prop) == 'true': wait_for_abort(10)
			wait_time = 1800
			try:
				from caches.settings_cache import sync_kodi_profile_context
				sync_kodi_profile_context()
				sync_interval, wait_time = simkl_sync_interval()
				next_update_string = 'Simkl Sync finished - Next Sync in %s minutes' % sync_interval
				if simkl_user_active(): status = simkl_sync_activities()
				else: status = 'no_auth'
				if status == 'failed': kodi_utils.logger('Red Light', 'Simkl Sync Failed')
				elif status == 'no_auth': kodi_utils.logger('Red Light', 'Simkl Sync Not Run - No Account')
				else: kodi_utils.logger('Red Light', 'Simkl Sync %s - %s' % ('OK' if status == 'success' else 'No Changes', next_update_string))
				if status == 'success' and not kodi_utils.service_shutting_down(monitor):
					from modules.settings import provider_sync_refresh_widgets
					if provider_sync_refresh_widgets(2):
						try:
							if not kodi_utils.playback_widget_refresh_recent(): kodi_utils.run_plugin({'mode': 'kodi_refresh'})
						except Exception as exc:
							kodi_utils.logger('Red Light', 'Simkl widget refresh skipped: %s' % exc)
			except Exception as e: kodi_utils.logger('Red Light', 'Simkl Sync Failed: %s' % str(e))
			wait_for_abort(wait_time)
		try: del player
		except: pass
		return kodi_utils.logger('Red Light', 'SimklMonitor Service Finished')

class MdblistMonitor:
	def run(self, monitor):
		kodi_utils.logger('Red Light', 'MDBListMonitor Service Starting')
		from apis.mdblist_api import mdblist_sync_activities
		from modules.settings import mdblist_user_active, mdblist_sync_interval
		player = kodi_utils.kodi_player()
		wait_for_abort, is_playing = monitor.waitForAbort, player.isPlayingVideo
		wait_for_abort(60)
		while not monitor.abortRequested():
			while is_playing() or kodi_utils.get_property(pause_services_prop) == 'true': wait_for_abort(10)
			wait_time = 1800
			try:
				from caches.settings_cache import sync_kodi_profile_context
				sync_kodi_profile_context()
				sync_interval, wait_time = mdblist_sync_interval()
				next_update_string = 'MDBList Sync finished - Next Sync in %s minutes' % sync_interval
				if mdblist_user_active(): status = mdblist_sync_activities()
				else: status = 'no_auth'
				if status == 'failed': kodi_utils.logger('Red Light', 'MDBList Sync Failed')
				elif status == 'no_auth': kodi_utils.logger('Red Light', 'MDBList Sync Not Run - No Account')
				else: kodi_utils.logger('Red Light', 'MDBList Sync %s - %s' % ('OK' if status == 'success' else 'No Changes', next_update_string))
				if status == 'success' and not kodi_utils.service_shutting_down(monitor):
					from modules.settings import provider_sync_refresh_widgets
					if provider_sync_refresh_widgets(3):
						try:
							if not kodi_utils.playback_widget_refresh_recent(): kodi_utils.run_plugin({'mode': 'kodi_refresh'})
						except Exception as exc:
							kodi_utils.logger('Red Light', 'MDBList widget refresh skipped: %s' % exc)
			except Exception as e: kodi_utils.logger('Red Light', 'MDBList Sync Failed: %s' % str(e))
			wait_for_abort(wait_time)
		try: del player
		except: pass
		return kodi_utils.logger('Red Light', 'MDBListMonitor Service Finished')

class PunchPlayMonitor:
	def run(self, monitor):
		kodi_utils.logger('Red Light', 'PunchPlayMonitor Service Starting')
		from apis.punchplay_api import punchplay_sync_activities
		from modules.settings import punchplay_user_active, punchplay_sync_interval
		player = kodi_utils.kodi_player()
		wait_for_abort, is_playing = monitor.waitForAbort, player.isPlayingVideo
		wait_for_abort(55)
		while not monitor.abortRequested():
			while is_playing() or kodi_utils.get_property(pause_services_prop) == 'true': wait_for_abort(10)
			wait_time = 1800
			try:
				from caches.settings_cache import sync_kodi_profile_context
				sync_kodi_profile_context()
				sync_interval, wait_time = punchplay_sync_interval()
				next_update_string = 'PunchPlay Sync finished - Next Sync in %s minutes' % sync_interval
				if punchplay_user_active(): status = punchplay_sync_activities()
				else: status = 'no_auth'
				if status == 'failed': kodi_utils.logger('Red Light', 'PunchPlay Sync Failed')
				elif status == 'no_auth': kodi_utils.logger('Red Light', 'PunchPlay Sync Not Run - No Account')
				else: kodi_utils.logger('Red Light', 'PunchPlay Sync %s - %s' % ('OK' if status == 'success' else 'No Changes', next_update_string))
				if status == 'success' and not kodi_utils.service_shutting_down(monitor):
					from modules.settings import provider_sync_refresh_widgets
					if provider_sync_refresh_widgets(4):
						try:
							if not kodi_utils.playback_widget_refresh_recent(): kodi_utils.run_plugin({'mode': 'kodi_refresh'})
						except Exception as exc:
							kodi_utils.logger('Red Light', 'PunchPlay widget refresh skipped: %s' % exc)
			except Exception as e: kodi_utils.logger('Red Light', 'PunchPlay Sync Failed: %s' % str(e))
			wait_for_abort(wait_time)
		try: del player
		except: pass
		return kodi_utils.logger('Red Light', 'PunchPlayMonitor Service Finished')

class WidgetRefresher:
	def run(self, monitor):
		kodi_utils.logger('Red Light', 'WidgetRefresher Service Starting')
		from time import time
		player = kodi_utils.kodi_player()
		wait_for_abort, self.is_playing = monitor.waitForAbort, player.isPlayingVideo
		wait_for_abort(10)
		self.set_next_refresh(time())
		while not monitor.abortRequested():
			try:
				wait_for_abort(10)
				if kodi_utils.service_shutting_down(monitor): continue
				offset = int(get_setting('redlight.widget_refresh_timer', '60'))
				if offset != self.offset:
					self.set_next_refresh(time())
					continue
				if self.condition_check(): continue
				if self.next_refresh < time():
					kodi_utils.logger('Red Light', 'WidgetRefresher Service - Widgets Refreshed')
					kodi_utils.refresh_widgets()
					self.set_next_refresh(time())
			except: pass
		try: del player
		except: pass
		return kodi_utils.logger('Red Light', 'WidgetRefresher Service Finished')

	def condition_check(self):
		if not self.external(): return True

		if self.next_refresh == None or self.is_playing() or kodi_utils.get_property(pause_services_prop) == 'true': return True
		if kodi_utils.get_property('redlight.window_loaded') == 'true': return True 
		try:
			window_stack = json.loads(kodi_utils.get_property('redlight.window_stack'))
			if window_stack or window_stack == []: return True
		except: pass
		return False

	def set_next_refresh(self, _time):
		self.offset = int(get_setting('redlight.widget_refresh_timer', '60'))
		if self.offset: self.next_refresh = _time + (self.offset*60)
		else: self.next_refresh = None

	def external(self):
		return 'plugin' not in kodi_utils.get_infolabel('Container.PluginName')

class SavedListsRevalidate:
	"""Keep the saved widget lists honest in the first minutes after start (#155, #163).

	modules.saved_lists may answer a widget from a stored list (a hit, or a stale list from an earlier
	run), and even a fresh build can be overtaken by another widget's provider sync: In Progress syncs
	MDBList on its own build and rewrites the watched table after Next Episodes has answered. Every
	answer records the key it was built for. At CHECKPOINTS seconds after the first answer, compare
	each home widget's recorded key with the current one; ask each list that is behind for one real
	build (its own rebuild request, so the others keep their lists) and refresh the widgets once.

	MAX_REBUILDS is load-bearing, not defensive: kodi_refresh() re-requests every widget, a provider
	can sync again and move a key again, and without the cap that would oscillate. A pending request is
	waited on first, including one asked at the last checkpoint, and a request nobody answers within
	REBUILD_WAIT ends the checks. Only home widgets are checked: a refresh cannot re-render an in-addon
	listing. After the last checkpoint the session's stale window ends for every list, and freshness is
	the providers' periodic syncs and WidgetRefresher's timer."""
	def run(self, monitor):
		from time import time
		from modules import saved_lists as sl
		specs = sl.specs()
		service_started, player = time(), kodi_utils.kodi_player()
		first_answer, checkpoints, rebuilds, asked = None, list(sl.CHECKPOINTS), 0, {}
		while not monitor.abortRequested():
			if monitor.waitForAbort(5) or kodi_utils.service_shutting_down(monitor): return
			now = time()
			if player.isPlayingVideo() or kodi_utils.get_property(pause_services_prop) == 'true': continue
			pending = [name for name in asked if sl.revalidate_state(name) == sl.REBUILD]
			if pending:
				if now - max(asked[name] for name in pending) < sl.REBUILD_WAIT: continue
				kodi_utils.logger('Red Light', 'SavedListsRevalidate: rebuild of %s not answered in %s s, leaving it to the next request' % (', '.join(pending), sl.REBUILD_WAIT))
				break
			if not checkpoints: break
			served = [(spec, name, info) for spec in specs for name, info in sl.served_lists(spec).items() if info.get('external')]
			if first_answer is None:
				if not served:
					if now - service_started > sl.FIRST_ANSWER_WAIT: break
					continue
				first_answer = now
			if now < max(first_answer + checkpoints[0], service_started + sl.REVALIDATE_AFTER): continue
			checkpoints.pop(0)
			behind = [name for spec, name, info in served if spec.key(info.get('external'), info.get('anime')) != info.get('key')]
			if not behind: continue
			if rebuilds >= sl.MAX_REBUILDS:
				kodi_utils.logger('Red Light', 'SavedListsRevalidate: %s still behind after %s rebuilds, leaving it to the periodic refresh' % (', '.join(behind), rebuilds))
				break
			rebuilds += 1
			for name in behind:
				kodi_utils.set_property(sl.REVALIDATE_PROP % name, sl.REBUILD)
				asked[name] = now
			kodi_utils.logger('Red Light', 'SavedListsRevalidate: %s shown from an older state, asking for a real build (%s of %s)' % (', '.join(behind), rebuilds, sl.MAX_REBUILDS))
			kodi_utils.kodi_refresh()
		for spec in specs:
			for is_external, variant in spec.lists():
				name = spec.list_name(is_external, variant)
				if sl.revalidate_state(name) in ('', sl.REBUILD): kodi_utils.set_property(sl.REVALIDATE_PROP % name, sl.DONE)

# The name #162 shipped under.
NextEpisodesRevalidate = SavedListsRevalidate

class AutoStart:
	def run(self, monitor):
		kodi_utils.logger('Red Light', 'AutoStart Service Starting')
		from modules.settings import auto_start_redlight
		if auto_start_redlight() and not kodi_utils.service_shutting_down(monitor):
			try:
				from caches.settings_cache import ensure_settings_properties_loaded
				ensure_settings_properties_loaded()
			except Exception as e:
				kodi_utils.logger('AutoStart', 'bootstrap: %s' % e)
			kodi_utils.run_addon()
		return kodi_utils.logger('Red Light', 'AutoStart Service Finished')

class ServiceExpiryAlerts:
	def run(self, monitor):
		kodi_utils.logger('Red Light', 'ServiceExpiryAlerts Service Starting')
		from modules.service_expiry import run_expiry_alerts
		monitor.waitForAbort(60)
		if not monitor.abortRequested() and not kodi_utils.service_shutting_down(monitor):
			try: run_expiry_alerts()
			except Exception as e: kodi_utils.logger('ServiceExpiryAlerts', str(e))
		return kodi_utils.logger('Red Light', 'ServiceExpiryAlerts Service Finished')

class AddonXMLCheck:
	def run(self):
		kodi_utils.logger('Red Light', 'AddonXMLCheck Service Starting')
		try: kodi_utils.reuse_language_invoker_check()
		except Exception as e: kodi_utils.logger('AddonXMLCheck', str(e))
		return kodi_utils.logger('Red Light', 'AddonXMLCheck Service Finished')

class RedLightMonitor(Monitor):
	def __init__ (self):
		Monitor.__init__(self)
		self.wake_resume = None
		try:
			from modules.wake_resume import WakeResumeWatcher
			self.wake_resume = WakeResumeWatcher(self.waitForAbort)
		except Exception as e: kodi_utils.logger('WakeResumeWatcher', str(e))
		self.startServices()

	def startServices(self):
		try: SetAddonConstants().run()
		except Exception as e: kodi_utils.logger('SetAddonConstants', str(e))
		try: DatabaseMaintenance().run()
		except Exception as e: kodi_utils.logger('DatabaseMaintenance', str(e))
		try: SyncSettings().run()
		except Exception as e: kodi_utils.logger('SyncSettings', str(e))
		try: AddonXMLCheck().run()
		except Exception as e: kodi_utils.logger('AddonXMLCheck', str(e))
		try:
			from caches.settings_cache import service_bootstrap_needed
			if service_bootstrap_needed():
				_start_daemon(lambda: BootstrapSettings().run(self))
		except Exception as e: kodi_utils.logger('BootstrapSettings', str(e))
		start_custom_windows_prepare(self)
		_start_daemon(lambda: TraktMonitor().run(self))
		_start_daemon(lambda: SimklMonitor().run(self))
		_start_daemon(lambda: MdblistMonitor().run(self))
		_start_daemon(lambda: PunchPlayMonitor().run(self))
		_start_daemon(lambda: WidgetRefresher().run(self))
		_start_daemon(lambda: SavedListsRevalidate().run(self))
		try: AutoStart().run(self)
		except Exception as e: kodi_utils.logger('AutoStart', str(e))
		_start_daemon(lambda: ServiceExpiryAlerts().run(self))

	def onNotification(self, sender, method, data):
		if method in ('GUI.OnScreensaverActivated', 'System.OnSleep'):
			kodi_utils.set_property(pause_services_prop, 'true')
			kodi_utils.logger('OnNotificationActions', 'PAUSING Red Light Services Due to Device Sleep')
		elif method in ('GUI.OnScreensaverDeactivated', 'System.OnWake'):
			kodi_utils.clear_property(pause_services_prop)
			kodi_utils.logger('OnNotificationActions', 'UNPAUSING Red Light Services Due to Device Awake')
		elif method in ('Player.OnAVStart', 'Player.OnStop'):
			# A file Kodi resumed itself on wake has no RedLightPlayer behind it (#143).
			if self.wake_resume: self.wake_resume.on_notification(method, data)
		elif method in ('Addon.OnDisabled', 'Addon.OnUninstalled'):
			try:
				info = json.loads(data)
				if info.get('id') == 'plugin.video.redlight':
					kodi_utils.prepare_service_shutdown()
					kodi_utils.logger('Red Light', 'Service shutdown - addon disabled for update or uninstall')
			except: pass

if __name__ == '__main__':
	# ----- AM Lite Trakt startup sync patch BEGIN -----
	def wait_for_am_trakt(timeout=120, max_age=180):
		import time
		import xbmc
		import xbmcaddon

		if not xbmc.getCondVisibility('System.HasAddon(script.module.acctmgr)'):
			return False

		waited = 0
		while waited < timeout:
			try:
				am = xbmcaddon.Addon('script.module.acctmgr')
				ready = am.getSetting('am_trakt_ready')
				last_prepare = am.getSetting('am_last_prepare')
				if ready == 'true' and last_prepare:
					age = int(time.time()) - int(last_prepare)
					if 0 <= age <= max_age:
						return True
			except Exception:
				pass
			xbmc.sleep(1000)
			waited += 1
		return False

	def _am_trakt_startup():
		try:
			wait_for_am_trakt()
		except Exception:
			pass

	Thread(target=_am_trakt_startup, daemon=True).start()
	# ----- AM Lite Trakt startup sync patch END -----
	kodi_utils.logger('Red Light', 'Main Monitor Service Starting')
	RedLightMonitor().waitForAbort()
	kodi_utils.logger('Red Light', 'Main Monitor Service Finished')