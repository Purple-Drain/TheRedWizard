# -*- coding: utf-8 -*-
"""#1 C447: after a source fails, try another provider before the same RD file under __magic__."""
from modules.sources import debrid_family, failover_order, prefer_other_family

RD = {'url_dl': 'dav://z/dav/__realdebrid__/Extras.S02/Extras.S02E06.mkv', 'debrid': 'folders'}
MAGIC_SAME = {'url_dl': 'dav://z/dav/__magic__/tv/Extras/Season 02/Extras.S02E06.mkv'}
MAGIC_OTHER = {'url_dl': 'dav://z/dav/__magic__/tv/Extras/Season 02/Extras.S02E06.other.mkv'}
TB = {'url_dl': 'dav://z/dav/__torbox__/Extras.S02/Extras.S02E06.mkv'}
TBC = {'url_dl': 'https://x', 'debrid': 'TorBox', 'scrape_provider': 'tb_cloud'}


def test_families():
    assert debrid_family(RD) == 'rd' and debrid_family(TB) == 'tb' and debrid_family(MAGIC_SAME) == 'magic'
    assert debrid_family(TBC) == 'tb'


def test_same_rd_file_under_magic_goes_last():
    assert failover_order(RD, [MAGIC_SAME, TB, MAGIC_OTHER, TBC]) == [TB, MAGIC_OTHER, TBC, MAGIC_SAME]


def test_prefer_other_family_for_the_stash():
    assert prefer_other_family([RD, TB], 'rd') == [TB, RD]
    assert prefer_other_family([RD, TB], None) == [RD, TB]
