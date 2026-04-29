#!/usr/bin/env python
#
# This file is part of adbb.
#
# adbb is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# adbb is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with adbb.  If not, see <http://www.gnu.org/licenses/>.

import datetime
import difflib
import gzip
import os
import sys
import tempfile
import time
import xml.etree.ElementTree as etree

if sys.version_info[0] < 3:
    import urllib2 as local_urllib
    urllib = local_urllib
    urllib.error = local_urllib
    urllib.request = local_urllib
else:
    import urllib
    import urllib.error
    import urllib.request

import adbb.animeobjs
from adbb.errors import AniDBError, AniDBFileError

_animetitles_useragent="adbb"
_animetitles_url="https://anidb.net/api/anime-titles.xml.gz"
_anime_list_url="https://github.com/Anime-Lists/anime-lists/raw/master/anime-list.xml"
iso_639_file=os.path.join(os.path.dirname(os.path.abspath(__file__)), "ISO-639-2_utf-8.txt")
_update_interval = datetime.timedelta(hours=36)

titles = None
anilist = None
languages = None

_tv_mappings={
        "tvdb": {
            "id": "tvdbid",
            "season": "defaulttvdbseason",
            "offset": "episodeoffset",
            "map_season": "tvdbseason" },
        "tmdb": {
            "id": "tmdbtv",
            "season": "tmdbseason",
            "offset": "tmdboffset",
            "map_season": "tmdbseason" }
        }

def update_xml(url):
    file_name = url.split('/')[-1]
    ext = url.split('.')[-1]
    if os.name == 'posix':
        cache_file = os.path.join('/var/tmp', file_name)
    else:
        cache_file = os.path.join(tempfile.gettempdir(), file_name)

    tmp_dir = os.path.dirname(cache_file)
    if not os.access(tmp_dir, os.W_OK):
        raise AniDBError("Cant get writeable temp path: %s" % tmp_dir)

    old_file_exists = os.path.isfile(cache_file)
    if old_file_exists:
        stat = os.stat(cache_file)
        file_moddate = datetime.datetime.fromtimestamp(stat.st_mtime)
        if file_moddate > (datetime.datetime.now() - _update_interval):
            return cache_file

    now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S.%f")
    tmp_file = os.path.join(os.path.dirname(cache_file), f".adbb_cache{now}.{ext}")

    try:
        with open(tmp_file, "wb") as f:
            req = urllib.request.Request(
                url,
                data=None,
                headers={
                    'User-Agent': _animetitles_useragent
                }
            )
            res = urllib.request.urlopen(req)
            adbb.log.info(f'Fetching cache file from {url}')
            f.write(res.read())
    except (IOError, urllib.error.URLError) as err:
        adbb.log.error(f"Failed to fetch {url}: {err}")
        adbb.log.info("You may be temporary ip-banned from anidb, banns will be automatically lifted after 24 hours!")
        os.remove(tmp_file)
        if old_file_exists:
            return cache_file
        return None
    
    if not _verify_xml_file(tmp_file):
        adbb.log.error("Failed to verify xml file: {}".format(tmp_file))
        return None

    os.rename(tmp_file, cache_file)
    return cache_file

def update_anilist():
    # These are the global variables we want to update
    # reset them here.
    global anilist
    anilist = {}

    xml_file = update_xml(_anime_list_url)
    if not xml_file and not anilist:
        adbb.log.critical("Missing, and unable to fetch, list of anime mappings")
        sys.exit(2)
    xml = _read_anidb_xml(xml_file)

    # Iterate every anime entry in XML; save attributes in the anilist dict.
    for anime in xml.iter("anime"):
        aid=anime.attrib['anidbid']
        a_attrs = anime.attrib
        del a_attrs['anidbid']

        anilist[aid] = a_attrs
        mappings=anime.find('mapping-list')
        if mappings:
            anilist[aid]['map'] = {}
            for m in mappings.iter("mapping"):
                attrs = m.attrib
                for source in ('tmdb', 'tvdb'):
                    if not source in anilist[aid]['map']:
                        anilist[aid]['map'][source] = []
                    if not f'{source}season' in attrs:
                        continue

                if m.text:
                    attrs['epmap'] = {}
                    episodes = m.text.strip(';').split(';')
                    for e in episodes:
                        (a, t) = e.split('-')
                        attrs['epmap'][a] = t

                    # If multiple anidb episodes are mapped to the same tvdb
                    # episode we need to figure out partnumbers; this is
                    # unfortunately broken for movies because of how anidb adds
                    # parts with episode numbers. When scraping movies the part
                    # should probably be ignored.
                    anidb_eps = sorted(attrs['epmap'].keys(), key=lambda x: int(x))
                    newmap = {}
                    for anidb_ep in anidb_eps:
                        my_epno = attrs['epmap'][anidb_ep]
                        others = [ x for x in anidb_eps if attrs['epmap'][x] == my_epno]
                        if len(others) == 1:
                            newmap[anidb_ep] = my_epno
                        else:
                            part = others.index(anidb_ep)+1
                            newmap[anidb_ep] = (my_epno, part)
                    attrs['epmap'] = newmap

                anilist[aid]['map'][source].append(attrs)

        name=anime.find('name')
        anilist[aid]['name']=name.text


def update_animetitles():
    global titles
    xml_file = update_xml(_animetitles_url)
    if not xml_file and not titles:
        adbb.log.critical("Missing, and unable to fetch, list of anime titles")
        sys.exit(2)
    titles = _read_anidb_xml(xml_file)


def _verify_xml_file(path):
    if not os.path.isfile(path):
        return False
    
    try:
        tmp_xml = _read_anidb_xml(path)
    except Exception as e:
        adbb.log.error("Exception when reading xml file: {}".format(e))
        return False

    if len(tmp_xml.findall('anime')) < 8000:
        return False
    
    return True
        

def _read_anidb_xml(filePath):
    return _read_xml_into_etree(filePath)


def _read_xml_into_etree(filePath):
        if not filePath:
            return None
        
        if filePath.split('.')[-1] == 'gz':
            with gzip.open(filePath, "rb") as f:
                data = f.read()
        else:
            with open(filePath, 'rb') as f:
                data = f.read()

        xmlASetree = etree.fromstring(data)
        return xmlASetree


def _read_language_file():
    global languages
    languages = {}
    with open(iso_639_file, "r") as f:
        for line in f:
            three, tree2, two, eng, fre = line.strip().split('|')
            if two:
                languages[two] = three


def get_lang_code(short):
    if not languages:
        _read_language_file()

    if short in languages:
        return languages[short]
    return None
    

def get_titles(name=None, aid=None, max_results=10, score_for_match=0.8):
    global titles
    res = []

    if titles is None:
        update_animetitles()
    if titles is None:
        raise AniDBFileError('Could not get valid title cache file.')

    lastAid = None
    for anime in titles.findall('anime'):
        score=0
        best_title_match=None
        exact_match=None
        if aid and aid == int(anime.get('aid')):
            exact_match=anime.get('aid')

        if name:
            name = name.replace('⁄', '/')
            for title in anime.findall('title'):
                if name.lower() in title.text.lower():
                    exact_match=title.text
                diff = difflib.SequenceMatcher(a=name, b=title.text)
                title_score = diff.ratio()
                if title_score > score:
                    score = title_score
                    best_title_match=title.text

        if score > score_for_match or exact_match:
            matched_titles = [
                    adbb.animeobjs.AnimeTitle(
                        x.get('type'),
                        get_lang_code(x.get('{http://www.w3.org/XML/1998/namespace}lang')),
                        x.text) for x in anime.findall('title')]
            res.append((int(anime.get('aid')), matched_titles, score, best_title_match))

    res.sort(key=lambda x: x[2], reverse=True)
    
    # response is a list of tuples in the form:
    #(<aid>, <list of titles>, <score of best title>, <best title>)
    return res[:max_results]


def anilist_maps(aid):
    global anilist
    if not anilist:
        update_anilist()
    if str(aid) in anilist:
        return anilist[str(aid)]
    return {}

def _get_tvid(aid, key):
    maps = anilist_maps(aid)
    if key in maps:
        try:
            int(maps[key])
        except ValueError:
            return None
        return maps[key]
    return None

def get_tvdbid(aid, id_type='tv'):
    if id_type == 'tv':
        return _get_tvid(aid, 'tvdbid')
    return None

def _get_movieid(aid, key):
    maps = anilist_maps(aid)
    if key in maps and maps[key] not in ['', 'unknown']:
        if ',' in maps[key]:
            return maps[key].split(',')
        return maps[key]
    return None

def get_tmdbid(aid, id_type='movie'):
    if id_type == 'tv':
        return _get_tvid(aid, 'tmdbtv')
    elif id_type == 'movie':
        return _get_movieid(aid, "tmdbid")
    return None

def get_imdbid(aid, id_type='movie'):
    if id_type == 'movie':
        return _get_movieid(aid, "imdbid")
    return None

# return (season, epno) where season is a int
# epno can be:
# An int for an episode number
# A tuple with episode number + partnumber when multiple anidb episodes maps to
# the same target or
# An array with episodes number if the anidb episode is mapped to multiple
# target episodes
def _db_ep_to_resp(db_epno):
    if type(db_epno) == int:
        return db_epno
    if type(db_epno) == tuple:
        return db_epno
    if type(db_epno) == str:
        eps = [int(x) for x in db_epno.split('+')]
        if len(eps) == 1:
            return eps[0]
        return eps

def _get_tv_episode(aid, epno, source):
    keys = _tv_mappings[source]
    maps = anilist_maps(aid)
    if not keys["id"] in maps:
        return (None, None)

    db_season = maps.get(keys['season'], None)
    if db_season == "a":
        db_season = "1"

    anidb_season = "1"
    anidb_special_offset = 0
    if str(epno).upper().startswith('S'):
        anidb_season = "0"
    elif str(epno).upper().startswith('T'):
        anidb_season = "0"
        anidb_special_offset = 200
    elif str(epno).upper().startswith('O'):
        anidb_season = "0"
        anidb_special_offset = 400

    try:
        int_epno = int(str(epno).upper().strip('STO')) + anidb_special_offset
    except ValueError:
        # Only specials of type Special, Trailer or Other are supported by
        # anime-lists
        return (None, None)

    str_epno = str(int_epno)

    if 'map' in maps:
        for m in maps['map'].get(source, []):
            if m['anidbseason'] != anidb_season or keys['map_season'] not in m:
                continue
            if 'epmap' in m:
                if str_epno in m['epmap']:
                    # Exact match for episode
                    db_epno = m['epmap'][str_epno]
                    if db_epno == "0" or type(db_epno) == tuple and db_epno[0] == "0":
                        db_season = None
                        continue
                    db_season = m[keys['map_season']]
                    return (int(db_season), _db_ep_to_resp(db_epno))
            if not 'start' in m or int_epno < int(m['start']):
                continue
            if 'end' in m and int_epno > int(m['end']):
                continue
            db_season = m[keys['map_season']]
            if 'offset' in m:
                ret_epno = int(m['offset']) + int_epno
                if ret_epno < 1:
                    return (None, None)
                return (int(db_season), ret_epno)
    if not db_season:
        # No season specified or episode mapped to 0
        return (None, None)
    if anidb_season == "0":
        # special, but not explicitly mapped in anime-list
        return (0, int_epno)

    if offset := int(maps.get(keys['offset'], 0)):
        ret_epno = offset + int_epno
        if ret_epno < 1:
            return (None, None)
        return (int(db_season), ret_epno)
    return (int(db_season), int_epno)


def get_tv_episode(aid, epno, source="tvdb"):
    return _get_tv_episode(aid, epno, source)

def get_tvdb_episode(aid, epno):
    return _get_tv_episode(aid, epno, "tvdb")

def get_tmdb_episode(aid, epno):
    return _get_tv_episode(aid, epno, "tmdb")
