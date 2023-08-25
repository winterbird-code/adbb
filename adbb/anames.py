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
absolute_order_set = None
languages = None

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
    global anilist, absolute_order_set
    anilist = {}
    absolute_order_set = set()

    xml_file = update_xml(_anime_list_url)
    if not xml_file and not anilist:
        adbb.log.critical("Missing, and unable to fetch, list of anime mappings")
        sys.exit(2)
    xml = _read_anidb_xml(xml_file)
    absolute_order = {}

    # Iterate every anime entry in XML; save attributes in the anilist dict.
    for anime in xml.iter("anime"):
        aid=anime.attrib['anidbid']
        a_attrs = anime.attrib
        del a_attrs['anidbid']

        # keep track if this anime has the absolute order flag set and if it
        # has any other non-special seasons explicitly specified
        has_absolute_order = False
        has_season_mapping = False
        if 'defaulttvdbseason' in a_attrs and 'tvdbid' in a_attrs and a_attrs['defaulttvdbseason'] == "a":
            has_absolute_order = True

        if has_absolute_order and not a_attrs['tvdbid'] in absolute_order:
            absolute_order[a_attrs['tvdbid']] = set()

        anilist[aid] = a_attrs
        mappings=anime.find('mapping-list')
        if mappings:
            anilist[aid]['map'] = []
            for m in mappings.iter("mapping"):
                attrs = m.attrib

                # Check for non-special seasons for series with
                # defaulttvdbseason set to absolute
                if has_absolute_order:
                    if all([x in attrs for x in ['tvdbseason', 'start']]) and int(attrs['tvdbseason']) > 0:
                        absolute_order[a_attrs['tvdbid']].add(int(attrs['tvdbseason']))
                        has_season_mapping = True

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

                anilist[aid]['map'].append(attrs)

        # no non-special season specified; we must treat this tvdb entry as
        # absolute order.
        if has_absolute_order and not has_season_mapping:
            absolute_order_set.add(a_attrs['tvdbid'])
            del absolute_order[a_attrs['tvdbid']]
        name=anime.find('name')
        anilist[aid]['name']=name.text

    # Now that we have gone through all entries, check if there are season
    # mappings for all seasons for the default-absolute-order series. If there
    # is, we don't need to treat is as absolute ordered.
    for tvdbid,seasons in absolute_order.items():
        season_list = sorted(seasons)
        if len(season_list) != season_list[-1]:
            absolute_order_set.add(tvdbid)


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

def get_tvdbid(aid):
    maps = anilist_maps(aid)
    if 'tvdbid' in maps:
        try:
            int(maps['tvdbid'])
        except ValueError:
            return None
        return maps['tvdbid']
    return None

def get_tmdbid(aid):
    maps = anilist_maps(aid)
    if 'tmdbid' in maps and maps['tmdbid'] not in ['', 'unknown']:
        if ',' in maps['tmdbid']:
            return maps['tmdbid'].split(',')
        return maps['tmdbid']
    return None

def get_imdbid(aid):
    maps = anilist_maps(aid)
    if 'imdbid' in maps and maps['imdbid'] not in ['', 'unknown']:
        if ',' in maps['imdbid']:
            return maps['imdbid'].split(',')
        return maps['imdbid']
    return None

def tvdbid_has_absolute_order(tvdbid):
    global absolute_order_set
    return tvdbid in absolute_order_set

def get_tvdb_episode(aid, epno):
    maps = anilist_maps(aid)
    if not 'tvdbid' in maps:
        return (None, None)

    if 'defaulttvdbseason' in maps:
        tvdb_season = maps['defaulttvdbseason']
    else:
        tvdb_season = None
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
        for m in maps['map']:
            if m['anidbseason'] != anidb_season:
                continue
            if 'epmap' in m:
                if str_epno in m['epmap']:
                    # Exact match for episode
                    tvdb_epno = m['epmap'][str_epno]
                    if tvdb_epno == "0" or type(tvdb_epno) == tuple and tvdb_epno[0] == "0":
                        tvdb_season = None
                        continue
                    if 'tvdbseason' in m:
                        tvdb_season = m['tvdbseason']
                    return (tvdb_season, tvdb_epno)
            if tvdbid_has_absolute_order(maps['tvdbid']) and 'tvdbseason' in m and m['tvdbseason'] != "0":
                # Do not mix absolute and seasoned order...
                continue
            if not all([ x in m for x in ['start', 'end']]):
                continue
            if 'start' in m and int_epno < int(m['start']):
                continue
            if 'end' in m and int_epno > int(m['end']):
                continue
            if 'tvdbseason' in m:
                tvdb_season = m['tvdbseason']
            if 'offset' in m:
                ret_epno = int(m['offset']) + int_epno
                if ret_epno < 1:
                    return (None, None)
                return (tvdb_season, str(ret_epno))
    if not tvdb_season:
        # No season specified or episode mapped to 0
        return (None, None)
    if anidb_season == "0":
        # special, but not explicitly mapped in anime-list
        return ("s", str_epno)

    if 'episodeoffset' in maps:
        ret_epno = int(maps['episodeoffset']) + int_epno
        if ret_epno < 1:
            return (None, None)
        return (tvdb_season, str(int(maps['episodeoffset']) + int_epno))
    return (tvdb_season, str_epno)

