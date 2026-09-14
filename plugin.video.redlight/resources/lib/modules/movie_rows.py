# -*- coding: utf-8 -*-
"""One movie list row as plain data, and the ListItem made from it (#163).

Movies.movie_row() computes a row per movie and build_movie_content() hands it to render_movie_row(),
which makes the same ListItem calls the indexer always made. A row holds only JSON-safe values, so a
saved list (modules.saved_lists) can be drawn again without any metadata, provider or network work.

Deliberately imports nothing: the saved list's hit path loads this before anything heavier.
"""


def render_movie_row(row, make_listitem, kodi_actor):
	listitem = make_listitem()
	info = row['info']
	info_tag = listitem.getVideoInfoTag(True)
	info_tag.setMediaType('movie'), info_tag.setTitle(info['title']), info_tag.setOriginalTitle(info['original_title']), info_tag.setGenres(info['genres'])
	info_tag.setDuration(info['duration']), info_tag.setPlaycount(info['playcount']), info_tag.setPlot(info['plot'])
	info_tag.setUniqueIDs(info['unique_ids']), info_tag.setIMDBNumber(info['imdb']), info_tag.setPremiered(info['premiered'])
	info_tag.setYear(info['year']), info_tag.setRating(info['rating']), info_tag.setVotes(info['votes']), info_tag.setMpaa(info['mpaa'])
	info_tag.setCountries(info['countries']), info_tag.setTrailer(info['trailer'])
	info_tag.setTagLine(info['tagline']), info_tag.setStudios(info['studios'])
	info_tag.setWriters(info['writers']), info_tag.setDirectors(info['directors'])
	info_tag.setCast([kodi_actor(name=i['name'], role=i['role'], thumbnail=i['thumbnail']) for i in row['cast']])
	if 'resume_seconds' in row: info_tag.setResumePoint(row['resume_seconds'])
	listitem.setLabel(row['label'])
	listitem.addContextMenuItems([tuple(i) for i in row['cm']])
	listitem.setArt(row['art'])
	listitem.setProperties(row['properties'])
	return listitem
