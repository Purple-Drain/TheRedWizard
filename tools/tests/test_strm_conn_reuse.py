# Exercises _propfind_body's real connection layer against a local server:
# keep-alive reuse, the retry when the server drops an idle connection, and
# that an HTTP error status is NOT retried.
import os, sys, types, json, socket, threading, http.client

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   os.pardir, os.pardir, "plugin.video.redlight", "resources", "lib")
sys.path.insert(0, LIB)
for name, attrs in (
    ("xbmcgui", {"ALPHANUM_HIDE_INPUT": 0, "INPUT_NUMERIC": 0}),
    ("xbmc", {"executeJSONRPC": lambda s: None}),
):
    m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m
c = types.ModuleType("caches"); c.__path__ = []; sys.modules["caches"] = c
sc = types.ModuleType("caches.settings_cache")
sc.get_setting = lambda k, d=None: d; sc.set_setting = lambda k, v: None
sys.modules["caches.settings_cache"] = sc
mods = types.ModuleType("modules"); mods.__path__ = [os.path.join(LIB, "modules")]
sys.modules["modules"] = mods
ku = types.ModuleType("modules.kodi_utils")
ku.notification = ku.kodi_dialog = ku.ok_dialog = lambda *a, **k: None
ku.logger = lambda *a: None; ku.addon_profile = lambda: "."
sys.modules["modules.kodi_utils"] = ku
su = types.ModuleType("modules.source_utils")
su.supported_video_extensions = lambda: [".mkv"]
sys.modules["modules.source_utils"] = su

from modules import strm_library as L  # noqa

BODY = (b'<?xml version="1.0"?><D:multistatus xmlns:D="DAV:">'
        b'<D:response><D:href>/ok/</D:href></D:response></D:multistatus>')

served = []          # one entry per request the server actually handled
connections = []     # one entry per accepted TCP connection
drop_after = [None]  # if set, close the connection after this many requests on it


def serve(sock):
    while True:
        try:
            conn, _ = sock.accept()
        except OSError:
            return
        connections.append(conn)
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


def handle(conn):
    conn.settimeout(5)
    f = conn.makefile("rb")
    n = 0
    try:
        while True:
            line = f.readline()
            if not line:
                return
            method, path, _ = line.decode().split()
            while f.readline().strip():
                pass
            served.append((method, path))
            n += 1
            if path == "/missing":
                conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                continue
            conn.sendall(b"HTTP/1.1 207 Multi-Status\r\nContent-Length: %d\r\n\r\n%s"
                         % (len(BODY), BODY))
            if drop_after[0] is not None and n >= drop_after[0]:
                conn.close()
                return
    except Exception:
        pass


sock = socket.socket()
sock.bind(("127.0.0.1", 0))
sock.listen(8)
PORT = sock.getsockname()[1]
threading.Thread(target=serve, args=(sock,), daemon=True).start()

# Only the transport differs from production; _propfind_body is untouched.
def _get_conn(host):
    conns = getattr(L._conn_local, "conns", None)
    if conns is None:
        conns = L._conn_local.conns = {}
    conn = conns.get(host)
    if conn is None:
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
        conns[host] = conn
        with L._all_conns_lock:
            L._all_conns.append(conn)
    return conn


L._get_conn = _get_conn

fails = []
def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  <- " + detail) if detail and not cond else ""))
    if not cond: fails.append(label)

print("\n1. keep-alive: 5 PROPFINDs must share ONE TCP connection")
for _ in range(5):
    L._propfind_body("Basic x", "h", "/ok/")
check("5 requests served", len(served) == 5, str(served))
check("on a single connection", len(connections) == 1, "%d connections" % len(connections))

print("\n2. server drops an idle keep-alive: must retry transparently")
L.close_connections(); served.clear(); connections.clear()
drop_after[0] = 1                       # server hangs up after each response
b1 = L._propfind_body("Basic x", "h", "/ok/")
b2 = L._propfind_body("Basic x", "h", "/ok/")   # first attempt hits a dead socket
check("both calls returned a body", b1 == BODY and b2 == BODY)
check("reconnected rather than raising", len(connections) >= 2, "%d connections" % len(connections))
drop_after[0] = None

print("\n3. an HTTP error status raises and is NOT retried")
L.close_connections(); served.clear(); connections.clear()
try:
    L._propfind_body("Basic x", "h", "/missing")
    raised = False
except IOError as exc:
    raised = "404" in str(exc)
check("raised IOError carrying the status", raised)
check("issued exactly one request (no retry)", len(served) == 1, str(served))

print("\n4. close_connections leaves no reusable socket behind")
L.close_connections()
check("thread-local emptied", not (getattr(L._conn_local, "conns", None) or {}))
check("global registry emptied", not L._all_conns)

sock.close()
print("\n" + ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
