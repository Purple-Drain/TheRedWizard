# -*- coding: utf-8 -*-
"""One single-episode list row as plain data, and the ListItem made from it (#155).

build_single_episode() computes a row per episode and hands it to render_episode_row(), which makes
the same ListItem calls the indexer always made. A row holds only JSON-safe values (cast members are
name/role/thumbnail dicts, not xbmc.Actor objects), so the Next Episodes list cache can store a
finished list and rebuild it without any metadata, provider or network work.

Deliberately imports nothing: the cache's hit path loads this before anything heavier.
"""


def render_episode_row(row, make_listitem, kodi_actor):
	listitem = make_listitem()
	info = row['info']
	info_tag = listitem.getVideoInfoTag(True)
	info_tag.setMediaType('episode'), info_tag.setOriginalTitle(info['original_title']), info_tag.setTvShowTitle(info['tvshow_title'])
	info_tag.setTitle(info['title']), info_tag.setGenres(info['genres'])
	info_tag.setPlaycount(info['playcount']), info_tag.setSeason(info['season']), info_tag.setEpisode(info['episode']), info_tag.setPlot(info['plot'])
	info_tag.setFirstAired(info['first_aired'])
	info_tag.setDuration(info['duration']), info_tag.setIMDBNumber(info['imdb']), info_tag.setUniqueIDs(info['unique_ids'])
	info_tag.setCountries(info['countries']), info_tag.setTrailer(info['trailer']), info_tag.setTvShowStatus(info['tvshow_status'])
	info_tag.setStudios(info['studios']), info_tag.setWriters(info['writers']), info_tag.setDirectors(info['directors'])
	info_tag.setYear(info['year']), info_tag.setRating(info['rating']), info_tag.setVotes(info['votes']), info_tag.setMpaa(info['mpaa'])
	info_tag.setCast([kodi_actor(name=i['name'], role=i['role'], thumbnail=i['thumbnail']) for i in row['cast']])
	if 'resume_seconds' in row: info_tag.setResumePoint(row['resume_seconds'])
	listitem.setLabel(row['label'])
	listitem.addContextMenuItems([tuple(i) for i in row['cm']])
	listitem.setArt(row['art'])
	listitem.setProperties(row['properties'])
	return listitem
