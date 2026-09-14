# -*- coding: utf-8 -*-
"""One TV show list row as plain data, and the ListItem made from it (#163).

TVShows.tvshow_row() computes a row per show and build_tvshow_content() hands it to
render_tvshow_row(), which makes the same ListItem calls the indexer always made. A row holds only
JSON-safe values (cast members are name/role/thumbnail dicts, not xbmc.Actor objects), so a saved
list (modules.saved_lists) can be drawn again without any metadata, provider or network work.

Deliberately imports nothing: the saved list's hit path loads this before anything heavier.
"""


def render_tvshow_row(row, make_listitem, kodi_actor):
	listitem = make_listitem()
	listitem.setLabel(row['label'])
	listitem.addContextMenuItems([tuple(i) for i in row['cm']])
	listitem.setArt(row['art'])
	info = row['info']
	info_tag = listitem.getVideoInfoTag(True)
	info_tag.setMediaType('tvshow'), info_tag.setTitle(info['title']), info_tag.setTvShowTitle(info['title']), info_tag.setOriginalTitle(info['original_title'])
	info_tag.setUniqueIDs(info['unique_ids']), info_tag.setIMDBNumber(info['imdb'])
	info_tag.setPlot(info['plot']), info_tag.setPlaycount(info['playcount']), info_tag.setGenres(info['genres']), info_tag.setYear(info['year'])
	info_tag.setTagLine(info['tagline']), info_tag.setStudios(info['studios']), info_tag.setWriters(info['writers']), info_tag.setDirectors(info['directors'])
	info_tag.setVotes(info['votes']), info_tag.setMpaa(info['mpaa']), info_tag.setDuration(info['duration']), info_tag.setCountries(info['countries'])
	info_tag.setTrailer(info['trailer']), info_tag.setPremiered(info['premiered'])
	info_tag.setTvShowStatus(info['status']), info_tag.setRating(info['rating'])
	info_tag.setCast([kodi_actor(name=i['name'], role=i['role'], thumbnail=i['thumbnail']) for i in row['cast']])
	listitem.setProperties(row['properties'])
	return listitem
