# -*- coding: utf-8 -*-

import sys

from resources.lib.modules import bookmarks
from resources.lib.modules import control
from resources.lib.modules import simkl
from resources.lib.modules import trakt


def _provider():
    try:
        return simkl.getIndicatorsProvider()
    except Exception:
        return 'trakt' if trakt.getTraktIndicatorsInfo() else 'local'


def getMovieIndicators(refresh=False):
    provider = _provider()
    if provider == 'local':
        try:
            return bookmarks._indicators()
        except Exception:
            return
    if provider == 'simkl':
        try:
            return simkl.cachesyncMovies(timeout=0 if refresh else 720)
        except Exception:
            return
    try:
        if refresh == False:
            timeout = 720
        elif trakt.getWatchedActivity() < trakt.timeoutsyncMovies():
            timeout = 720
        else:
            timeout = 0
        return trakt.cachesyncMovies(timeout=timeout)
    except Exception:
        pass


def getTVShowIndicators(refresh=False):
    provider = _provider()
    if provider == 'local':
        try:
            return bookmarks._indicators()
        except Exception:
            return
    if provider == 'simkl':
        try:
            timeout = 0 if refresh else 720
            return simkl.cachesyncTVShows(timeout=timeout)
        except Exception:
            return
    try:
        if refresh == False:
            timeout = 720
        elif trakt.getWatchedActivity() < trakt.timeoutsyncTVShows():
            timeout = 720
        else:
            timeout = 0
        return trakt.cachesyncTVShows(timeout=timeout)
    except Exception:
        pass


def getSeasonIndicators(imdb):
    provider = _provider()
    if provider == 'simkl':
        try:
            return simkl.syncSeason(imdb)
        except Exception:
            return
    try:
        if provider != 'trakt':
            raise Exception()
        return trakt.syncSeason(imdb)
    except Exception:
        pass


def getMovieOverlay(indicators_, imdb):
    try:
        if _provider() == 'local':
            overlay = bookmarks._get_watched('movie', imdb, '', '')
            return str(overlay)
        playcount = [i for i in indicators_ if i == imdb]
        overlay = 7 if len(playcount) > 0 else 6
        return str(overlay)
    except:
        return '6'


def getTVShowOverlay(indicators_, imdb, tmdb):
    try:
        if _provider() == 'local':
            playcount = bookmarks._get_watched('tvshow', imdb, '', '')
            return str(playcount)
        playcount = [i[0] for i in indicators_ if i[0] == tmdb and len(i[2]) >= int(i[1])]
        playcount = 7 if len(playcount) > 0 else 6
        return str(playcount)
    except:
        return '6'


def getSeasonOverlay(indicators_, imdb, season):
    try:
        if _provider() == 'local':
            playcount = bookmarks._get_watched('season', imdb, season, '')
            return str(playcount)
        playcount = [i for i in indicators_ if int(season) == int(i)]
        playcount = 7 if len(playcount) > 0 else 6
        return str(playcount)
    except:
        return '6'


def getEpisodeOverlay(indicators_, imdb, tmdb, season, episode):
    try:
        if _provider() == 'local':
            overlay = bookmarks._get_watched('episode', imdb, season, episode)
            return str(overlay)
        playcount = [i[2] for i in indicators_ if i[0] == tmdb]
        playcount = playcount[0] if len(playcount) > 0 else []
        playcount = [i for i in playcount if int(season) == int(i[0]) and int(episode) == int(i[1])]
        overlay = 7 if len(playcount) > 0 else 6
        return str(overlay)
    except:
        return '6'


def markMovieDuringPlayback(imdb, watched, tmdb=None):
    provider = _provider()
    try:
        if provider == 'trakt':
            if int(watched) == 7:
                trakt.markMovieAsWatched(imdb)
            else:
                trakt.markMovieAsNotWatched(imdb)
            trakt.cachesyncMovies()
            if trakt.getTraktAddonMovieInfo() == True:
                trakt.markMovieAsNotWatched(imdb)
        elif provider == 'simkl':
            if int(watched) == 7:
                simkl.markMovieAsWatched(imdb, tmdb=tmdb)
            else:
                simkl.markMovieAsNotWatched(imdb, tmdb=tmdb)
            simkl.cachesyncMovies(timeout=0)
            if simkl.getSimklAddonMovieInfo() == True:
                simkl.markMovieAsNotWatched(imdb, tmdb=tmdb)
    except:
        pass
    try:
        if int(watched) == 7:
            bookmarks.reset(1, 1, 'movie', imdb, '', '')
    except:
        pass


def markEpisodeDuringPlayback(imdb, tmdb, season, episode, watched):
    provider = _provider()
    try:
        if provider == 'trakt':
            if int(watched) == 7:
                trakt.markEpisodeAsWatched(imdb, season, episode)
            else:
                trakt.markEpisodeAsNotWatched(imdb, season, episode)
            trakt.cachesyncTVShows()
            if trakt.getTraktAddonEpisodeInfo() == True:
                trakt.markEpisodeAsNotWatched(imdb, season, episode)
        elif provider == 'simkl':
            if int(watched) == 7:
                simkl.markEpisodeAsWatched(imdb, season, episode, tmdb=tmdb)
            else:
                simkl.markEpisodeAsNotWatched(imdb, season, episode, tmdb=tmdb)
            simkl.cachesyncTVShows(timeout=0)
            if simkl.getSimklAddonEpisodeInfo() == True:
                simkl.markEpisodeAsNotWatched(imdb, season, episode, tmdb=tmdb)
    except:
        pass
    try:
        if int(watched) == 7:
            bookmarks.reset(1, 1, 'episode', imdb, season, episode)
    except:
        pass


def movies(imdb, watched, tmdb=None):
    control.busy()
    provider = _provider()
    try:
        if provider == 'trakt':
            if int(watched) == 7:
                trakt.markMovieAsWatched(imdb)
            else:
                trakt.markMovieAsNotWatched(imdb)
            trakt.cachesyncMovies()
            control.refresh()
            control.idle()
        elif provider == 'simkl':
            if int(watched) == 7:
                simkl.markMovieAsWatched(imdb, tmdb=tmdb)
            else:
                simkl.markMovieAsNotWatched(imdb, tmdb=tmdb)
            simkl.cachesyncMovies(timeout=0)
            control.refresh()
            control.idle()
        else:
            raise Exception()
    except:
        pass
    try:
        if int(watched) == 7:
            bookmarks.reset(1, 1, 'movie', imdb, '', '')
        else:
            bookmarks._delete_record('movie', imdb, '', '')
        if provider == 'local':
            control.refresh()
        control.idle()
    except:
        pass


def episodes(imdb, tmdb, season, episode, watched):
    control.busy()
    provider = _provider()
    try:
        if provider == 'trakt':
            if int(watched) == 7:
                trakt.markEpisodeAsWatched(imdb, season, episode)
            else:
                trakt.markEpisodeAsNotWatched(imdb, season, episode)
            trakt.cachesyncTVShows()
            control.refresh()
            control.idle()
        elif provider == 'simkl':
            if int(watched) == 7:
                simkl.markEpisodeAsWatched(imdb, season, episode, tmdb=tmdb)
            else:
                simkl.markEpisodeAsNotWatched(imdb, season, episode, tmdb=tmdb)
            simkl.cachesyncTVShows(timeout=0)
            control.refresh()
            control.idle()
        else:
            raise Exception()
    except:
        pass
    try:
        if int(watched) == 7:
            bookmarks.reset(1, 1, 'episode', imdb, season, episode)
        else:
            bookmarks._delete_record('episode', imdb, season, episode)
        if provider == 'local':
            control.refresh()
        control.idle()
    except:
        pass


def tvshows(tvshowtitle, imdb, tmdb, season, watched):
    control.busy()
    provider = _provider()
    try:
        if provider != 'local':
            raise Exception()
        from resources.lib.indexers import episodes
        name = control.addonInfo('name')
        dialog = control.progressDialogBG
        dialog.create(str(name), str(tvshowtitle))
        dialog.update(0, str(name), str(tvshowtitle))
        items = []
        if season:
            items = episodes.episodes().get(tvshowtitle, '0', imdb, tmdb, meta=None, season=season, idx=False)
            items = [i for i in items if int('%01d' % int(season)) == int('%01d' % int(i['season']))]
            items = [{'label': '%s S%02dE%02d' % (tvshowtitle, int(i['season']), int(i['episode'])), 'season': int('%01d' % int(i['season'])), 'episode': int('%01d' % int(i['episode'])), 'unaired': i['unaired']} for i in items]
            for i in range(len(items)):
                if control.monitor.abortRequested():
                    return sys.exit()
                dialog.update(int((100 / float(len(items))) * i), str(name), str(items[i]['label']))
                _season, _episode, unaired = items[i]['season'], items[i]['episode'], items[i]['unaired']
                if int(watched) == 7:
                    if not unaired == 'true':
                        bookmarks.reset(1, 1, 'episode', imdb, _season, _episode)
                else:
                    bookmarks._delete_record('episode', imdb, _season, _episode)
        else:
            seasons = episodes.seasons().get(tvshowtitle, '0', imdb, tmdb, meta=None, idx=False)
            seasons = [i['season'] for i in seasons]
            for s in seasons:
                items = episodes.episodes().get(tvshowtitle, '0', imdb, tmdb, meta=None, season=s, idx=False)
                items = [{'label': '%s S%02dE%02d' % (tvshowtitle, int(i['season']), int(i['episode'])), 'season': int('%01d' % int(i['season'])), 'episode': int('%01d' % int(i['episode'])), 'unaired': i['unaired']} for i in items]
                for i in range(len(items)):
                    if control.monitor.abortRequested():
                        return sys.exit()
                    dialog.update(int((100 / float(len(items))) * i), str(name), str(items[i]['label']))
                    _season, _episode, unaired = items[i]['season'], items[i]['episode'], items[i]['unaired']
                    if int(watched) == 7:
                        if not unaired == 'true':
                            bookmarks.reset(1, 1, 'episode', imdb, _season, _episode)
                    else:
                        bookmarks._delete_record('episode', imdb, _season, _episode)
        try:
            dialog.close()
        except:
            pass
    except:
        try:
            dialog.close()
        except:
            pass
    try:
        if provider == 'trakt':
            if season:
                from resources.lib.indexers import episodes
                items = episodes.episodes().get(tvshowtitle, '0', imdb, tmdb, meta=None, season=season, idx=False)
                items = [(int(i['season']), int(i['episode'])) for i in items]
                items = [i[1] for i in items if int('%01d' % int(season)) == int('%01d' % i[0])]
                for i in items:
                    if int(watched) == 7:
                        trakt.markEpisodeAsWatched(imdb, season, i)
                    else:
                        trakt.markEpisodeAsNotWatched(imdb, season, i)
            else:
                if int(watched) == 7:
                    trakt.markTVShowAsWatched(imdb)
                else:
                    trakt.markTVShowAsNotWatched(imdb)
            trakt.cachesyncTVShows()
        elif provider == 'simkl':
            if season:
                from resources.lib.indexers import episodes
                items = episodes.episodes().get(tvshowtitle, '0', imdb, tmdb, meta=None, season=season, idx=False)
                items = [(int(i['season']), int(i['episode'])) for i in items]
                items = [i[1] for i in items if int('%01d' % int(season)) == int('%01d' % i[0])]
                for i in items:
                    if int(watched) == 7:
                        simkl.markEpisodeAsWatched(imdb, season, i, tmdb=tmdb)
                    else:
                        simkl.markEpisodeAsNotWatched(imdb, season, i, tmdb=tmdb)
            else:
                if int(watched) == 7:
                    simkl.markTVShowAsWatched(imdb, tmdb=tmdb)
                else:
                    simkl.markTVShowAsNotWatched(imdb, tmdb=tmdb)
            simkl.cachesyncTVShows(timeout=0)
    except:
        pass
    control.refresh()
    control.idle()
