"""Minimal Kodi module stubs so resources/lib can be imported outside Kodi for testing."""
import sys, types


class _Any:
    """Permissive stand-in: any attribute access or call returns another _Any."""
    def __init__(self, *a, **k): pass
    def __getattr__(self, name): return _Any()
    def __call__(self, *a, **k): return _Any()
    def __iter__(self): return iter(())
    def __bool__(self): return False
    def __str__(self): return ''


def install():
    for name in ('xbmc', 'xbmcgui', 'xbmcplugin', 'xbmcaddon', 'xbmcvfs'):
        mod = types.ModuleType(name)
        # Any attribute the addon reaches for resolves to the permissive stand-in.
        mod.__getattr__ = lambda attr: _Any()
        sys.modules[name] = mod

    xbmc = sys.modules['xbmc']
    xbmc.log = lambda *a, **k: None
    xbmc.LOGINFO = xbmc.LOGERROR = xbmc.LOGDEBUG = 1
    xbmc.translatePath = lambda p: '/tmp'
    xbmc.getInfoLabel = lambda k: ''
    xbmc.executebuiltin = lambda *a, **k: None
    xbmc.Monitor = _Any
    xbmc.Player = _Any

    gui = sys.modules['xbmcgui']
    gui.Window = _Any
    gui.WindowXML = _Any
    gui.WindowXMLDialog = _Any
    gui.ListItem = _Any
    gui.Dialog = _Any
    gui.DialogProgress = _Any
    gui.DialogProgressBG = _Any

    addon = sys.modules['xbmcaddon']
    addon.Addon = lambda *a, **k: types.SimpleNamespace(
        getSetting=lambda k: '', setSetting=lambda k, v: None,
        getAddonInfo=lambda k: '/tmp', getSettingBool=lambda k: False)

    vfs = sys.modules['xbmcvfs']
    vfs.translatePath = lambda p: '/tmp'
    vfs.exists = lambda p: False
    vfs.mkdirs = lambda p: None
