# -*- coding: utf-8 -*-
import time
from threading import Thread
from modules.kodi_utils import addon_fanart, execute_builtin, get_visibility, kodi_player
from windows.base_window import BaseDialog
from modules.settings import avoid_episode_spoilers
# from modules.kodi_utils import logger

def _restore_fullscreen_playback(player=None):
	"""After a playback overlay closes, return to fullscreen if video is still playing.
	Otherwise Kodi can leave the previous window (Home/widgets/episodes) on top —
	especially after Autoscrape confirm / Still Watching on Android widget launches."""
	try:
		player = player or kodi_player()
		if not (player.isPlayingVideo() or player.isPlaying()):
			return
		if get_visibility('Window.IsActive(fullscreenvideo)'):
			return
		execute_builtin('ActivateWindow(fullscreenvideo)', block=False)
	except:
		pass

class NextEpisode(BaseDialog):
	episode_status_dict = {
	'season_premiere': ('Season Premiere', 'b30385b5'),
	'mid_season_premiere': ('Mid-Season Premiere', 'b385b503'),
	'series_finale': ('Series Finale', 'b38503b5'),
	'season_finale': ('Season Finale', 'b3b50385'),
	'mid_season_finale': ('Mid-Season Finale', 'b3b58503'),
	'':  (None, None)}
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.closed = False
		self.meta = kwargs.get('meta')
		self.default_action = kwargs.get('default_action', 'cancel')
		self.selected = self.default_action
		# #1 C428: shown while the OSD is open and the next episode is prepared. No countdown, the OSD
		# stays, and it closes when the OSD does; Cancel and Back only close it.
		self.osd_mode = bool(kwargs.get('osd_mode'))
		self.set_properties()
		if self.osd_mode: self.setProperty('osd_mode', 'true')
		# Say what the button does (owner: Close/Cancel were confusing). Back hides the dialog.
		self.setProperty('play_label', '▶  Play Next' if self.osd_mode else '▶  Play Now')
		# #1 redesign: Ready badge, info line, Finding Source state, countdown line.
		if kwargs.get('ready'): self.setProperty('ready', 'true')
		if kwargs.get('info_line'): self.setProperty('info_line', kwargs.get('info_line'))
		if kwargs.get('finding'): self.setProperty('finding', 'true')
		self.countdown_total = None

	def onInit(self):
		# Buttons: 10 Close | 11 Play | 12 Cancel
		# Close (10) is hidden since the owner's review; Back does its job, so focus Play for it.
		focus_map = {'play': 11, 'cancel': 12, 'pause': 11, 'close': 11}
		# OSD mode (C428): OK on the remote should play, so focus Play; Back still just closes.
		self.setFocusId(11 if self.osd_mode else focus_map.get(self.selected, 12))
		try:
			from modules.kodi_utils import logger
			logger('Red Light', 'Next episode alert open: default=%s focus=%s (back=close)' % (
				self.default_action, focus_map.get(self.selected, 12)))
		except:
			pass
		Thread(target=self.monitor, daemon=True).start()

	def run(self):
		self.doModal()
		self.clearProperties()
		player = getattr(self, 'player', None)
		self.clear_modals()
		_restore_fullscreen_playback(player)
		return self.selected

	def onAction(self, action):
		if action in self.closing_actions and self.osd_mode:
			self.selected, self.closed = 'close', True
			self.close()
			return
		if action in self.closing_actions:
			# Back/Escape = Close (dismiss; play next when episode ends). Cancel is the abort button.
			self.selected = 'close'
			try:
				from modules.kodi_utils import logger
				logger('Red Light', 'Next episode alert dismiss: action=%s -> close (default=%s)' % (
					action, self.default_action))
			except:
				pass
			self.closed = True
			self.close()

	def onClick(self, controlID):
		self.selected = {10: 'close', 11: 'play', 12: 'cancel'}[controlID]
		if self.osd_mode and self.selected == 'cancel': self.selected = 'close'
		try:
			from modules.kodi_utils import logger
			logger('Red Light', 'Next episode alert button: id=%s -> %s (default=%s)' % (
				controlID, self.selected, self.default_action))
		except:
			pass
		self.closed = True
		self.close()

	@staticmethod
	def _playlist_position():
		try:
			import xbmc
			return xbmc.PlayList(xbmc.PLAYLIST_VIDEO).getposition()
		except Exception:
			return None

	def _skip_requested(self):
		"""#1 C410: the next-episode skip arrived while this dialog is up. The player's monitor is
		blocked in this modal, so the dialog answers for it: Play, and tell request_skip_episode it was
		claimed so it does not stop playback."""
		from modules.kodi_utils import get_property, set_property, clear_property, logger
		if get_property('redlight.skip_episode_requested') != 'true': return False
		clear_property('redlight.skip_episode_requested')
		set_property('redlight.skip_episode_ack', 'true')
		self.selected = 'play'
		self.closed = True
		logger('Red Light', 'Next episode alert: skip requested, playing now')
		self.close()
		return True

	def set_properties(self):
		self.setProperty('mode', 'next_episode')
		self.setProperty('thumb', self.get_thumb())
		self.setProperty('clearlogo', self.meta.get('clearlogo', ''))
		self.setProperty('episode_label', '%s[B] | [/B]%02dx%02d[B] | [/B]%s' % (self.meta['title'], self.meta['season'], self.meta['episode'], self.meta['ep_name']))
		self.setProperty('ep_code', 'S%02dE%02d' % (int(self.meta['season']), int(self.meta['episode'])))
		self.setProperty('ep_title', self.meta.get('ep_name', '') or '')
		self.setProperty('show_title', self.meta.get('title', '') or '')
		self.setProperty('pause_timer', '')
		self.setProperty('nextep_remaining', '')
		status_label, status_highlight = self.episode_status_dict[self.meta.get('episode_type', '')]
		if status_label:
			self.setProperty('episode_status.label', status_label)
			self.setProperty('episode_status.highlight', status_highlight)

	def _format_clock(self, seconds):
		seconds = max(0, int(seconds))
		mins, secs = divmod(seconds, 60)
		return '%d:%02d' % (mins, secs)

	def get_thumb(self):
		if avoid_episode_spoilers() and int(self.meta.get('playcount') or 0) == 0: thumb = self.meta.get('fanart', '') or addon_fanart()
		else: thumb = self.meta.get('ep_thumb', None) or self.meta.get('fanart', '') or addon_fanart()
		return thumb

	def _player_active(self):
		try:
			return self.player.isPlayingVideo() or self.player.isPlaying()
		except:
			return False

	def monitor(self):
		try:
			if self._player_active():
				start_position = self._playlist_position()
				while self._player_active() and not self.closed:
					if self.osd_mode:
						# The player's monitor is free in this mode and claims a skip itself; only get out
						# of the way when the OSD closes, a skip arrives or Kodi moves on.
						from modules.kodi_utils import get_property
						moved = start_position is not None and self._playlist_position() != start_position
						if moved or get_property('redlight.skip_episode_requested') == 'true' or not get_visibility('Window.IsVisible(videoosd)'):
							self.selected, self.closed = 'close', True
							self.close()
							return
						self.sleep(300)
						continue
					if self._skip_requested(): return
					if start_position is not None and self._playlist_position() != start_position:
						# #1 C417: the Next key moved Kodi on to the queued file under this dialog.
						self.selected, self.closed = 'close', True
						self.close()
						return
					try:
						total_time = self.player.getTotalTime()
						remaining_time = max(0, round(total_time - self.player.getTime()))
						self.setProperty('nextep_remaining', self._format_clock(remaining_time))
						# Countdown line: share of the alert window left before the default action.
						if self.countdown_total is None: self.countdown_total = max(1, remaining_time)
						self.setProperty('countdown_pct', str(int(100 * remaining_time / self.countdown_total)))
						if self.selected == 'pause' and remaining_time <= 10:
							try: self.player.pause()
							except: pass
							self.sleep(500)
							break
					except:
						pass
					self.sleep(1000)
		except:
			pass
		if self.closed:
			return
		if self.selected == 'pause':
			start_time = time.time()
			end_time = start_time + 900
			current_time = start_time
			while current_time <= end_time and self.selected == 'pause' and not self.closed:
				try:
					current_time = time.time()
					pause_timer = time.strftime('%M:%S', time.gmtime(max(end_time - current_time, 0)))
					self.setProperty('pause_timer', pause_timer)
					self.sleep(1000)
				except: break
			if self.selected != 'cancel' and not self.closed:
				try: self.player.pause()
				except: pass
		if not self.closed:
			self.close()

class StillWatching(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.closed = False
		self.selected = False
		self.meta = kwargs.get('meta')
		self.check_text = kwargs.get('check_text')
		self.heading = kwargs.get('heading') or 'Still Watching?'
		right_align = kwargs.get('right_align', 'false')
		self.compact_confirm = str(right_align).lower() in ('true', '1', 'yes')
		self.set_properties()

	def onInit(self):
		self.set_properties()
		self.setFocusId(10)
		Thread(target=self.monitor, daemon=True).start()

	def run(self):
		self.doModal()
		self.clearProperties()
		player = getattr(self, 'player', None)
		self.clear_modals()
		_restore_fullscreen_playback(player)
		return self.selected

	def onAction(self, action):
		if action in self.closing_actions:
			self.selected = False
			self.closed = True
			self.close()

	def onClick(self, controlID):
		self.selected = {10: True, 11: False}[controlID]
		self.closed = True
		self.close()

	def set_properties(self):
		landscape, fanart, clearlogo = self.meta.get('landscape', ''), self.meta.get('fanart', ''), self.meta.get('clearlogo', '')
		self.setProperty('mode', 'autoscrape_confirm' if self.compact_confirm else 'still_watching')
		if self.compact_confirm:
			if avoid_episode_spoilers() and int(self.meta.get('playcount') or 0) == 0:
				thumb = fanart or addon_fanart()
			else:
				thumb = self.meta.get('ep_thumb') or fanart or addon_fanart()
			self.setProperty('thumb', thumb)
			self.setProperty('clearlogo', clearlogo)
			self.setProperty('episode_label', '%s[B] | [/B]%02dx%02d[B] | [/B]%s' % (
				self.meta['title'], self.meta['season'], self.meta['episode'], self.meta.get('ep_name', '')))
		else:
			self.setProperty('thumb', landscape or fanart)
			if not landscape: self.setProperty('clearlogo', clearlogo)
			self.setProperty('episode_label', self.check_text % self.meta['title'])
		self.setProperty('still_watching_heading', self.heading)
		self.setProperty('pause_timer', '')

	def monitor(self):
		pause_timer = 10
		try:
			while not self.closed and pause_timer >= 0:
				if self.compact_confirm:
					try:
						if not self.player.isPlayingVideo() and not self.player.isPlaying(): break
					except: pass
				self.setProperty('pause_timer', '%02d %s' % (pause_timer, 'seconds' if pause_timer > 1 else 'second'))
				self.sleep(1000)
				if self.closed: return
				if pause_timer == 0: break
				pause_timer -= 1
		except:
			pass
		if not self.closed:
			self.close()

class IntroSkipPrompt(BaseDialog):
	"""#272: shown from a few seconds before the intro until it ends. No countdown. It runs on its own
	thread (player.py), so the player's monitor is never blocked; Back or No hides it for good."""
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.closed = False
		self.selected = False
		self.timed_out = False
		self.meta = kwargs.get('meta')
		try: self.start_sec = float(kwargs.get('start_sec', 0))
		except: self.start_sec = 0.0
		try: self.end_sec = float(kwargs.get('end_sec', 0))
		except: self.end_sec = 0.0
		self.set_properties()

	def _log_intro_prompt(self, message):
		try:
			from modules.kodi_utils import logger
			logger('Red Light', 'Intro skip prompt: %s' % message)
		except:
			pass

	def onInit(self):
		# Re-apply after XML load — Window.Property(mode) from __init__ can be empty on some Android builds,
		# which hides the Skip Intro heading/buttons (only dim + thumb remain).
		self.set_properties()
		try:
			self.setFocusId(10)
		except Exception as exc:
			self._log_intro_prompt('setFocusId failed: %s' % exc)
		mode = ''
		focus_id = -1
		try: mode = self.getProperty('mode') or ''
		except: pass
		try: focus_id = self.getFocusId()
		except: pass
		fs = False
		playing = False
		try: fs = bool(get_visibility('Window.IsActive(fullscreenvideo)'))
		except: pass
		try:
			playing = bool(self.player.isPlayingVideo() or self.player.isPlaying())
		except: pass
		self._log_intro_prompt('onInit mode=%r focus=%s fullscreenvideo=%s playing=%s countdown=%ss' % (
			mode, focus_id, fs, playing, self.countdown_sec))
		Thread(target=self.monitor, daemon=True).start()

	def run(self):
		self._log_intro_prompt('doModal begin')
		self.doModal()
		self._log_intro_prompt('doModal end timed_out=%s selected=%s' % (self.timed_out, self.selected))
		self.clearProperties()
		player = getattr(self, 'player', None)
		self.clear_modals()
		# Always restore on close. Yes still waits 250ms then seeks once —
		# ActivateWindow must not share a tick with seekTime (#220). Restoring
		# here (before that wait) avoids a second fullscreen assert during the seek.
		_restore_fullscreen_playback(player)
		if self.timed_out:
			return None
		return self.selected

	def onAction(self, action):
		if action in self.closing_actions:
			self.selected = False
			self.closed = True
			self.close()

	def onClick(self, controlID):
		self.selected = {10: True, 11: False, 12: False}[controlID]
		self.closed = True
		self.close()

	def set_properties(self):
		fanart, clearlogo = self.meta.get('fanart', ''), self.meta.get('clearlogo', '')
		self.setProperty('mode', 'skip_intro')
		if avoid_episode_spoilers() and int(self.meta.get('playcount') or 0) == 0:
			thumb = fanart or addon_fanart()
		else:
			thumb = self.meta.get('ep_thumb') or fanart or addon_fanart()
		self.setProperty('thumb', thumb)
		self.setProperty('clearlogo', clearlogo)
		self.setProperty('episode_label', '%s[B] | [/B]%02dx%02d[B] | [/B]%s' % (
			self.meta['title'], self.meta['season'], self.meta['episode'], self.meta.get('ep_name', '')))
		self.setProperty('still_watching_heading', 'Skip Intro?')
		self.setProperty('pause_timer', '')

	def _status_text(self, curr):
		if curr < self.start_sec: return '  |  starts in %d s' % max(1, int(round(self.start_sec - curr)))
		remaining = max(0, int(self.end_sec - curr))
		return '  |  ends in %d:%02d' % divmod(remaining, 60)

	def monitor(self):
		from modules.kodi_utils import get_property, logger
		try:
			while not self.closed:
				# The player's monitor is free in this mode and claims a next-episode skip itself; only
				# get out of the way.
				if get_property('redlight.skip_episode_requested') == 'true':
					logger('Red Light', 'Intro skip prompt: next-episode skip requested, closing')
					self.selected, self.closed = False, True
					self.close()
					return
				if not self.player.isPlayingVideo() and not self.player.isPlaying(): break
				curr = float(self.player.getTime())
				if self.end_sec and curr >= self.end_sec: break
				self.setProperty('pause_timer', self._status_text(curr))
				self.sleep(500)
		except:
			pass
		if not self.closed:
			self.timed_out = True
			self.close()

class StingersNotification(BaseDialog):
	def __init__(self, *args, **kwargs):
		BaseDialog.__init__(self, *args)
		self.stinger_dict = {'duringcreditsstinger': {'id': 200, 'property': 'color_during'}, 'aftercreditsstinger': {'id': 201, 'property': 'color_after'}}
		self.closed = False
		self.meta = kwargs.get('meta')
		self.stingers = self.meta.get('stinger_keys')
		self.set_properties()

	def onInit(self):
		self.make_stingers()
		Thread(target=self.monitor, daemon=True).start()

	def run(self):
		self.doModal()
		self.clearProperties()
		self.clear_modals()

	def onAction(self, action):
		if action in self.closing_actions:
			self.closed = True
			self.close()

	def make_stingers(self):
		for k, v in self.stinger_dict.items():
			if k in self.stingers:
				self.setProperty(v['property'], 'green')
				self.set_image(v['id'], 'redlight_common/overlay_selected.png')
			else:
				self.setProperty(v['property'], 'red')
				self.set_image(v['id'], 'redlight_common/cross.png')

	def set_properties(self):
		self.setProperty('mode', 'stinger')
		self.setProperty('thumb', self.meta.get('fanart', '')) or addon_fanart()
		self.setProperty('clearlogo', self.meta.get('clearlogo', ''))

	def monitor(self):
		total_time = 10000
		try:
			while self.player.isPlaying() and total_time > 0 and not self.closed:
				self.sleep(1000)
				total_time -= 1000
		except:
			pass
		if not self.closed:
			self.close()
