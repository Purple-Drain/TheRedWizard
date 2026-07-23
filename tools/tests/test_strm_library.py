# Offline exercise of strm_library's new gate / two-strike prune logic.
# Kodi's modules don't exist off-device, so stub the four it imports.
import os, sys, types, shutil, tempfile, json

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   os.pardir, os.pardir, "plugin.video.redlight", "resources", "lib")
sys.path.insert(0, LIB)

PROFILE = tempfile.mkdtemp(prefix="rl-profile-")
ROOT = tempfile.mkdtemp(prefix="rl-lib-")

xbmcgui = types.ModuleType("xbmcgui"); xbmcgui.ALPHANUM_HIDE_INPUT = 0; xbmcgui.INPUT_NUMERIC = 0
sys.modules["xbmcgui"] = xbmcgui
xbmc = types.ModuleType("xbmc")
RPC = []
xbmc.executeJSONRPC = lambda s: RPC.append(json.loads(s)["method"])
sys.modules["xbmc"] = xbmc

caches = types.ModuleType("caches"); caches.__path__ = []
sc = types.ModuleType("caches.settings_cache")
SETTINGS = {}
sc.get_setting = lambda k, d=None: SETTINGS.get(k, d)
sc.set_setting = lambda k, v: SETTINGS.__setitem__(k, v)
sys.modules["caches"] = caches; sys.modules["caches.settings_cache"] = sc

mods = types.ModuleType("modules"); mods.__path__ = [os.path.join(LIB, "modules")]
sys.modules["modules"] = mods
ku = types.ModuleType("modules.kodi_utils")
LOG = []
ku.notification = lambda *a, **k: None
ku.kodi_dialog = lambda: None
ku.ok_dialog = lambda **k: None
ku.logger = lambda tag, msg: LOG.append("%s: %s" % (tag, msg))
ku.addon_profile = lambda: PROFILE
sys.modules["modules.kodi_utils"] = ku
su = types.ModuleType("modules.source_utils")
su.supported_video_extensions = lambda: [".mkv", ".mp4", ".avi"]
sys.modules["modules.source_utils"] = su

from modules import strm_library as L  # noqa

# --- fake WebDAV tree -------------------------------------------------------
# RD-shaped: /torrents/<name>/<file>. TREE is mutated between runs to simulate
# adds, deletions and the observed silent-loss behaviour.
TREE = {
    "/": (["/torrents/", "/links/"], []),
    "/links/": ([], []),
    "/torrents/": ([], []),
}
DROP = set()      # paths whose listing comes back empty (silent loss)
CALLS = []


def make_tree(shows):
    t = {"/": (["/torrents/", "/links/"], []), "/links/": ([], [])}
    subs = ["/torrents/%s/" % s for s in shows]
    t["/torrents/"] = (subs, [])
    for s in shows:
        d = "/torrents/%s/" % s
        t[d] = ([], [("%s%s.S01E01.1080p.mkv" % (d, s), 1000)])
    return t


def fake_propfind(auth, host, path):
    CALLS.append(path)
    if path in DROP:
        return [], []
    if path not in TREE:
        raise IOError("404")
    return TREE[path]


L._propfind = fake_propfind
L._propfind_safe = lambda a, h, p: fake_propfind(a, h, p) if p not in DROP else ([], [])
L.close_connections = lambda: None
SETTINGS["redlight.library_sync.root"] = ROOT
for k, v in (("host", "dav.example"), ("user", "u"), ("pass", "p")):
    SETTINGS["redlight.library_sync.webdav.rd.%s" % k] = v

def strms():
    out = []
    for dp, _dn, fn in os.walk(ROOT):
        out += [os.path.join(dp, f) for f in fn if f.endswith(".strm")]
    return sorted(os.path.relpath(p, ROOT) for p in out)

def episodes():
    return [p for p in strms() if p.startswith("TV Shows")]

fails = []
def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  <- " + detail) if detail and not cond else ""))
    if not cond: fails.append(label)

# --- 1. first run: no state => full walk, populates ------------------------
TREE = make_tree(["alpha", "bravo", "charlie"])
print("\n1. cold start (no persisted state)")
print("   ->", L.run_sync())
check("populated 3 episodes", len(episodes()) == 3, str(episodes()))
check("state file written", os.path.exists(L._state_path()))
st = L._load_state()
check("fingerprint persisted", bool(st.get("gates", {}).get("Real-Debrid")))
check("last_walk_files recorded", st.get("last_walk_files") == 3, str(st.get("last_walk_files")))

# --- 2. nothing changed => gate skips the walk entirely --------------------
print("\n2. unchanged account")
CALLS.clear()
r = L.run_sync()
print("   ->", r, "| requests:", len(CALLS), CALLS)
check("reported no changes", r == "no changes")
check("only gate requests (no walk)", len(CALLS) == 2, str(CALLS))
check("library untouched", len(episodes()) == 3)

# --- 3. an add moves the fingerprint => full walk --------------------------
print("\n3. one torrent added")
TREE = make_tree(["alpha", "bravo", "charlie", "delta"])
CALLS.clear()
r = L.run_sync()
print("   ->", r, "| requests:", len(CALLS))
check("gate fired, walked", r != "no changes")
check("new episode present", len(episodes()) == 4, str(episodes()))

# --- 4. THE BUG: a walk silently loses a directory -------------------------
# Membership is unchanged so the gate would skip -- force past it the way a
# real changed-elsewhere run would, and confirm nothing is deleted on the
# first sighting.
print("\n4. silent loss (listing truncated, no error) -- must NOT prune")
DROP.add("/torrents/delta/")
r = L.run_sync(force=True)
print("   ->", r)
check("nothing pruned on first sighting", len(episodes()) == 4, str(episodes()))
# one lost episode yields three .strm: TV Shows, Browse/By Provider, Browse/By Title
check("orphans recorded as pending", len(L._load_state().get("pending_prune") or []) == 3,
      str(L._load_state().get("pending_prune")))

print("\n5. the loss recovers next run -- pending must clear, still no prune")
DROP.clear()
r = L.run_sync(force=True)
print("   ->", r)
check("still 4 episodes", len(episodes()) == 4, str(episodes()))
check("pending cleared", not L._load_state().get("pending_prune"),
      str(L._load_state().get("pending_prune")))

# --- 6. a REAL deletion: gone twice in a row => pruned ---------------------
print("\n6. real deletion -- pruned on the second consecutive absence")
TREE = make_tree(["alpha", "bravo", "charlie"])
r = L.run_sync()
print("   run A ->", r)
check("not pruned yet (first absence)", len(episodes()) == 4, str(episodes()))
r = L.run_sync(force=True)
print("   run B ->", r)
check("pruned on second absence", len(episodes()) == 3, str(episodes()))

# --- 7. shrink guard: a walk that comes back way short defers the prune ----
print("\n7. shrink guard -- walk returns <90% of last run")
TREE = make_tree(["alpha"])       # 3 -> 1 file, a 67% drop
r = L.run_sync()
print("   ->", r)
check("prune deferred by shrink guard", len(episodes()) == 3, str(episodes()))
check("guard logged", any("deferring prune" in m for m in LOG), LOG[-1] if LOG else "")
check("baseline moved to new count", L._load_state().get("last_walk_files") == 1)
r = L.run_sync(force=True)
print("   next run ->", r)
check("prunes on the following run", len(episodes()) == 1, str(episodes()))

# --- 8. wiped state must force a walk, never 'no changes' ------------------
print("\n8. state wiped (simulating a reinstall)")
os.remove(L._state_path())
CALLS.clear()
r = L.run_sync()
print("   ->", r, "| requests:", len(CALLS))
check("did NOT skip", r != "no changes")
check("walked", len(CALLS) > 2)

# --- 9. corrupt state file behaves the same -------------------------------
print("\n9. corrupt state file")
open(L._state_path(), "w").write("{not json")
r = L.run_sync()
check("did NOT skip on corrupt state", r != "no changes", r)

# --- 10. gate must ignore /links churn ------------------------------------
print("\n10. /links churns but /torrents does not")
L.run_sync()                                   # settle
TREE["/links/"] = ([], [("/links/fresh-link.mkv", 1)])
CALLS.clear()
r = L.run_sync()
print("   ->", r, "| requests:", len(CALLS))
check("/links churn did not trigger a walk", r == "no changes", r)

# --- 11. unreachable provider must not be read as 'unchanged' -------------
print("\n11. provider unreachable during the gate")
saved = dict(TREE); TREE.clear()
r = L.run_sync()
print("   ->", r)
check("treated as changed, not skipped", r != "no changes", r)
TREE.update(saved)

# --- 12. Clean-without-Scan on an error-free no-op run --------------------
print("\n12. ghost self-heal: error-free run with nothing written")
print("   settle A ->", L.run_sync(force=True))
print("   settle B ->", L.run_sync(force=True))   # drain any pending prune
RPC.clear()
print("   no-op    ->", L.run_sync(force=True))
check("VideoLibrary.Clean issued", "VideoLibrary.Clean" in RPC, str(RPC))
check("VideoLibrary.Scan skipped", "VideoLibrary.Scan" not in RPC, str(RPC))

shutil.rmtree(PROFILE, ignore_errors=True); shutil.rmtree(ROOT, ignore_errors=True)
print("\n" + ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
