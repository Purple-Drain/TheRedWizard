# -*- coding: utf-8 -*-
import sys
import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin

CHAINSLIVE_ID = 'plugin.video.chainslive'
CHAINSLIVE_MAIN = 'plugin://%s/' % CHAINSLIVE_ID


def _chainslive_installed():
	try:
		xbmcaddon.Addon(CHAINSLIVE_ID)
		return True
	except Exception:
		return False


def main():
	handle = int(sys.argv[1])
	if not _chainslive_installed():
		xbmcgui.Dialog().ok(
			'Chains Live Player',
			'[B]Chains Live[/B] is not installed.\n\nInstall [COLOR goldenrod]plugin.video.chainslive[/COLOR] from the Chains repository, then open Chains Live Player again.',
		)
		xbmcplugin.endOfDirectory(handle, succeeded=False)
		return
	# Chains Live main menu: no mode param -> getSources()
	xbmc.executebuiltin('ReplaceWindow(Videos,%s,return)' % CHAINSLIVE_MAIN)
	xbmcplugin.endOfDirectory(handle, succeeded=False)


if __name__ == '__main__':
	main()
