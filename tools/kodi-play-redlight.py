#!/usr/bin/env python3
"""Start a Redlight playback on the Shield from the LAN, through Kodi's JSON-RPC.

Prototype behind TheRedWizard#92 and google-home-automations#27 (voice: "play the next
Seinfeld"). One authenticated Player.Open with a plugin:// URL is all it takes: Kodi hands the
URL to Redlight, which scrapes, matches, resolves and plays exactly as if the widget had been
clicked. Nothing bypasses the addon. (An Android VIEW intent does not resolve plugin URLs.)

What it does not do yet, and #92 is about: ask Redlight which episode is *next*. Today you
pass season and episode yourself.

Usage:
    tools/kodi-play-redlight.py --tmdb 1400 --season 4 --episode 23
    tools/kodi-play-redlight.py --tmdb 603 --movie
    tools/kodi-play-redlight.py --url 'plugin://plugin.video.redlight/?mode=...'
    KODI_ADB_DEVICE=10.1.1.30:5555 KODI_HOST=10.1.1.30 tools/kodi-play-redlight.py ...

Credentials: Kodi's webserver user/password are read once from the device's guisettings.xml
over adb (the same thing kodi-shield-config's lib/adb.sh does). Set KODI_WS_USER /
KODI_WS_PASS to skip adb entirely, e.g. from a Home Assistant secret. The password is never
printed. The device is woken first because the webserver is unreachable while it sleeps.
"""
import argparse
import os
import re
import subprocess
import sys
import time

try:
    import requests
except ImportError:
    sys.exit('needs the requests package (pip install requests)')

DEVICE = os.environ.get('KODI_ADB_DEVICE', '10.1.1.30:5555')
HOST = os.environ.get('KODI_HOST', DEVICE.split(':')[0])
GUISETTINGS = '/sdcard/Android/data/org.xbmc.kodi/files/.kodi/userdata/guisettings.xml'


def adb(*args, timeout=30):
    return subprocess.run(['adb', '-s', DEVICE, *args], capture_output=True, text=True, timeout=timeout)


def webserver_credentials():
    user, pw, port = os.environ.get('KODI_WS_USER'), os.environ.get('KODI_WS_PASS'), os.environ.get('KODI_WS_PORT')
    if user and pw is not None and port:
        return user, pw, port
    xml = adb('shell', 'cat', GUISETTINGS).stdout

    def field(name, default=''):
        match = re.search(r'<setting id="%s"[^>]*>([^<]*)<' % re.escape(name), xml)
        return match.group(1) if match else default
    return (user or field('services.webserverusername', 'kodi') or 'kodi',
            pw if pw is not None else field('services.webserverpassword', ''),
            port or field('services.webserverport', '8080') or '8080')


def rpc(url, auth, method, params=None):
    response = requests.post(url, auth=auth, timeout=30,
                             json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
    response.raise_for_status()
    body = response.json()
    if 'error' in body:
        raise RuntimeError('%s: %s' % (method, body['error']))
    return body.get('result')


def playback_url(args):
    if args.url:
        return args.url
    if not args.tmdb:
        sys.exit('need --url, or --tmdb with --season/--episode (or --movie)')
    if args.movie:
        return 'plugin://plugin.video.redlight/?mode=playback.media&media_type=movie&tmdb_id=%s&media=media' % args.tmdb
    if args.season is None or args.episode is None:
        sys.exit('episodes need --season and --episode (see #92 for the "next episode" route)')
    return ('plugin://plugin.video.redlight/?mode=playback.media&media_type=episode&tmdb_id=%s'
            '&season=%s&episode=%s&playcount=0&media=media' % (args.tmdb, args.season, args.episode))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--tmdb', help='TMDb id of the show or movie')
    parser.add_argument('--season', type=int)
    parser.add_argument('--episode', type=int)
    parser.add_argument('--movie', action='store_true')
    parser.add_argument('--url', help='a full plugin://plugin.video.redlight/ URL instead')
    parser.add_argument('--wait', type=int, default=30, help='seconds to wait before reporting what plays')
    args = parser.parse_args()

    url = playback_url(args)
    adb('shell', 'input', 'keyevent', 'KEYCODE_WAKEUP')
    user, pw, port = webserver_credentials()
    if not pw:
        print('warning: no webserver password found; Kodi will answer 401 if one is set', file=sys.stderr)
    endpoint = 'http://%s:%s/jsonrpc' % (HOST, port)
    auth = (user, pw)

    print('Player.Open ->', url)
    print(' ', rpc(endpoint, auth, 'Player.Open', {'item': {'file': url}}))
    if args.wait <= 0:
        return 0
    time.sleep(args.wait)
    players = rpc(endpoint, auth, 'Player.GetActivePlayers') or []
    if not players:
        print('no active player after %ss: Redlight found nothing, or the sources dialog is open' % args.wait)
        return 1
    item = rpc(endpoint, auth, 'Player.GetItem', {
        'playerid': players[0]['playerid'],
        'properties': ['showtitle', 'title', 'season', 'episode', 'file']})['item']
    print('playing: %s S%02dE%02d %s' % (item.get('showtitle') or item.get('label', ''),
                                         item.get('season') or 0, item.get('episode') or 0, item.get('title', '')))
    print('file:    %s' % (item.get('file') or '').split('?')[0].split('|')[0])
    return 0


if __name__ == '__main__':
    sys.exit(main())
