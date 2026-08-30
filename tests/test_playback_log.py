import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin.video.redlight", "resources", "lib"))

# No Kodi stubs needed: playback_log's module-level imports are stdlib-only.
from modules.playback_log import redact_link

TOKEN = 'AGVKJ4X7QW2ZP5MN6RT3'
fails = []

def check(label, got, want):
    ok = got == want
    print('%-52s %s' % (label, 'ok' if ok else 'FAIL\n   got:  %r\n   want: %r' % (got, want)))
    if not ok: fails.append(label)

def leaks(label, url):
    out = redact_link(url)
    ok = TOKEN not in out
    print('%-52s %s   -> %s' % (label, 'ok' if ok else 'LEAKED', out))
    if not ok: fails.append(label)

# Real-debrid shape: token sits in a middle path segment
leaks('token in path segment', 'https://x.download.real-debrid.com/d/%s/Show.S03E10.1080p.mkv' % TOKEN)
check('token in path segment redacts to host + filename',
      redact_link('https://x.download.real-debrid.com/d/%s/Show.S03E10.1080p.mkv' % TOKEN),
      'https://x.download.real-debrid.com/.../Show.S03E10.1080p.mkv')

# Query-string token
leaks('token in query string', 'https://cdn.example.com/stream/file.mkv?token=%s&u=42' % TOKEN)
check('query string is dropped entirely',
      redact_link('https://cdn.example.com/stream/file.mkv?token=%s&u=42' % TOKEN),
      'https://cdn.example.com/.../file.mkv')

# Opaque last segment (could itself be a token) -> not kept
leaks('opaque final segment', 'https://premiumize.me/dl/%s' % TOKEN)
check('opaque final segment is dropped',
      redact_link('https://premiumize.me/dl/%s' % TOKEN),
      'https://premiumize.me/...')

# Fragment
check('fragment is dropped',
      redact_link('https://host.tld/a/b/File.mkv#frag'), 'https://host.tld/.../File.mkv')

# Local path -> basename only, no directory tree
check('local path keeps only the basename',
      redact_link('/storage/emulated/0/Movies/Some.Movie.2019.mkv'), 'Some.Movie.2019.mkv')
check('windows path keeps only the basename',
      redact_link('C:\\Users\\someone\\Videos\\Some.Movie.mkv'), 'Some.Movie.mkv')

# Degenerate input
check('empty stays empty', redact_link(''), '')
check('none stays empty', redact_link(None), '')
check('bare host', redact_link('https://host.tld'), 'https://host.tld/...')

# An absurdly long final segment is not a filename
check('overlong final segment dropped',
      redact_link('https://h.tld/a/' + 'x' * 200 + '.mkv'), 'https://h.tld/...')

print()
if fails:
    print('%d FAILED: %s' % (len(fails), fails))
    sys.exit(1)
print('all redaction checks passed')
