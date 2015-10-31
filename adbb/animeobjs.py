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
import os
import re
import threading

import sqlalchemy 

import adbb
import adbb.anames
import adbb.mapper
import adbb.fileinfo
from adbb.db import *
from adbb.commands import *
from adbb.errors import *

class AniDBObj:

    def __init__(self):
        self._db = adbb._sql_db
        self._anidb_link = adbb._anidb
        self._updated = threading.Event()
        self.db_data = None

    def _fetch_anidb_data(self, force):
        if force: 
            old = datetime.datetime.now()
        else:
            old = datetime.datetime.now()-datetime.timedelta(days=7)
        tries = 0
        while (not self.db_data or self.db_data.updated <= old) and tries < 5:
            if tries > 0:
                adbb._log.warning("Retrying fetching of anidb-data, retry: {}"\
                        .format(tries))
            adbb._log.debug("Seding anidb request for {}".format(self))
            self._send_anidb_update_req()
            self._updated.wait()
            tries += 1
        if not self.db_data:
            adbb._log.error("Failed to save anidb data for {}".\
                    format(self))

    def update(self, force=False):
        if not self.db_data or force:
            self._get_db_data(force)

    def _send_anidb_update_req(self):
        raise Exception("Not implemented")

    def _db_commit(self):
        try:
            self._db.commit()
        except sqlalchemy.exc.DBAPIError as e:
            if self.db_data:
                adbb._log.warning("Failed to update data {}: {}".format(
                    self.db_data, e))
            else:
                adbb._log.warning("Failed to update db: {}".format(e))
            self._db.rollback()



class Anime(AniDBObj):

    def __init__(self, init):
        super().__init__()
        self.aid = None
        self.titles = None
        self.title = None

        if isinstance(init, int):
            self.aid, self.titles, score, best_title = adbb.anames.get_titles(
                    aid=init)[0]
        elif isinstance(init, str):
            self.aid, self.titles, score, best_title = adbb.anames.get_titles(
                    name=init)[0]
        elif isinstance(init, AnimeTable):
            # init with AnimeTable
            self.aid, self.titles, score, best_title = adbb.anames.get_titles(
                    aid=init.aid)[0]
            self.db_data = init

        self.title = [x.title for x in self.titles 
                if x.lang == None and x.titletype == 'main'][0]

    def _get_db_data(self, force=False):
        res = self._db.query(AnimeTable).filter_by(aid=self.aid).all()
        if len(res) > 0:
            self.db_data = res[0]
        self._fetch_anidb_data(force)

    def _db_data_callback(self, res):
        ainfo = res.datalines[0]
        relations = None
        new = None

        if all([x in ainfo for x in ['related_aid_list', 'related_aid_type']]):
            relations = zip(
                    ainfo['related_aid_list'].split("'"), 
                    ainfo['related_aid_type'].split("'"))
            del ainfo['related_aid_list']
            del ainfo['related_aid_type']
        relations = [
                AnimeRelationTable(
                    related_aid=int(x),
                    relation_type=adbb.mapper.anime_relation_map[y])
                for x, y in relations]

        # convert datatypes
        for attr, data in ainfo.items():
            if attr in adbb.mapper.anime_map_a_converters:
                ainfo[attr] = adbb.mapper.anime_map_a_converters[attr](data)

        if self.db_data:
            self.db_data.update(**ainfo)
            self.db_data.updated = datetime.datetime.now()
            new_relations = []
            for r in relations:
                found = False
                for sr in self.db_data.relations:
                    if r.related_aid == sr.related_aid:
                        found = True
                        sr.relation_type=r.relation_type
                        sr.anime_pk = self.db_data.pk
                        new_relations.append(sr)
                if not found:
                    r.anime_pk=self.db_data.pk
                    new_relations.append(r)
            for r in self.db_data.relations:
                if r not in new_relations:
                    self._db.delete(r)
            self.db_data.relations = new_relations
        else:
            new = AnimeTable(**ainfo)
            new.updated = datetime.datetime.now()

            new.relations = relations

            # commit to sql database
            self._db.add(new)

        if new:
            self.db_data = new
        self._db_commit()
        self._updated.set()

    def _send_anidb_update_req(self):
        req = AnimeCommand(
                aid=str(self.aid), 
                amask=adbb.mapper.getAnimeBitsA(adbb.mapper.anime_map_a))
        self._updated.clear()
        self._anidb_link.request(req, self._db_data_callback)


    def __getattr__(self, name):
        res = None

        if not self.db_data:
            self._get_db_data()
        if name == 'relations':
            res = [(x.relation_type, Anime(x.related_aid)) \
                        for x in self.db_data.relations]

        if res:
            return res
        return getattr(self.db_data, name, None)

    def __repr__(self):
        return "Anime(title='{}', aid={})".format(
                self.title,
                self.aid)

class AnimeTitle:
    def __init__(self, titletype, lang, title):
        self.titletype = titletype
        self.lang = lang
        self.title = title

    def __repr__(self):
        return "AnimeTitle(type='{}', lang='{}', title='{}')".format(
                self.titletype,
                self.lang,
                self.title)

class Episode(AniDBObj):

    _eid = None
    _anime = None
    _episode_number = None

    @property
    def eid(self):
        if self._eid:
            return self._eid
        if not self.db_data:
            self._get_db_data()
        return self.db_data.eid

    @property
    def anime(self):
        if self._anime:
            return self._anime
        if not self.db_data or not self.db_data.aid:
            self._get_db_data()
        self._anime = Anime(self.db_data.aid)
        return self._anime

    @property
    def episode_number(self):
        if self._episode_number:
            return self._episode_number
        if not self.db_data or not self.db_data.epno:
            self._get_db_data()
        return self.db_data.epno

    def __init__(self, anime=None, epno=None, eid=None):
        super().__init__()

        if not ((anime and epno) or eid):
            raise AniDBError(
                    "Episode must be created with either anime and epno, "\
                    "or eid.")
        if eid:
            self._eid = eid
        else:
            if isinstance(anime, Anime):
                self._anime = anime
            else:
                self._anime = Anime(anime)
            try:
                str(int(epno))
            except ValueError:
                pass
            self._episode_number = epno

    def _get_db_data(self, force=False):
        if self._eid:
            res = self._db.query(EpisodeTable).filter_by(eid=self._eid).all()
        else:
            res = self._db.query(EpisodeTable).filter_by(
                    aid=self.anime.aid,
                    epno=self.episode_number).all()
        if len(res) > 0:
            self.db_data = res[0]
        self._fetch_anidb_data(force)

    def _anidb_data_callback(self, res):
        if res.rescode == "340":
            adbb._log.warning("No such episode in anidb: {}".format(self))
            self._updated.set()
            return
        einfo = res.datalines[0]
        new = None
        for attr, data in einfo.items():
            if attr == 'epno':
                try:
                    einfo[attr] = str(int(data))
                except ValueError:
                    pass
                continue
            if attr in ('title_eng', 'title_romaji', 'title_kanji'):
                continue
            einfo[attr] = adbb.mapper.episode_map_converters[attr](data)

        if self.db_data:
            self.db_data.update(**einfo)
            self.db_data.updated = datetime.datetime.now()
        else:
            new = EpisodeTable(**einfo)
            new.updated = datetime.datetime.now()
            self._db.add(new)

        if new:
            self.db_data = new

        self._db_commit()

        self._updated.set()

    def _send_anidb_update_req(self):
        if self._eid:
            req = EpisodeCommand(eid=self._eid)
        else:
            req = EpisodeCommand(aid=self.anime.aid, epno=self.episode_number)
        self._updated.clear()
        self._anidb_link.request(req, self._anidb_data_callback)
        

    def __getattr__(self, name):
        if not self.db_data:
            self._get_db_data()
        return getattr(self.db_data, name, None)

    def __repr__(self):
        return "Episode(anime={}, episode_number='{}', eid={})".format(
                self._anime, self._episode_number, self._eid)

class File(AniDBObj):

    _anime = None
    _episode = None
    _multiep = []
    _fid = None
    _path = None
    _size = None
    _ed2khash = None
    _mtime = None

    @property
    def anime(self):
        if self._anime:
            return self._anime
        self.update()
        if not self._anime and self.db_data and self.db_data.aid:
            self._anime = Anime(self.db_data.aid)
        return self._anime

    @property
    def episode(self):
        if self._episode:
            return self._episode
        self.update()
        if not self._episode and self.db_data and self.db_data.eid:
            self._episode = Episode(eid=self.db_data.eid)
        return self._episode

    @property
    def multiep(self):
        """Return all episode numbers if there are more of them. Note that this
        is very much not reliable since this attribute is not stored in the
        database.

        FIXME: add multiep attribute to database..."""
        if self._multiep:
            return self._multiep
        else:
            return [self.episode.episode_number]

    @property
    def fid(self):
        if self._fid:
            return self._fid
        self.update()
        return self.db_data.fid

    @property
    def path(self):
        if self._path:
            return self._path
        self.update()
        return self.db_data.path

    @property
    def size(self):
        if self._size:
            return self._size
        self.update()
        return self.db_data.size

    @property
    def mtime(self):
        return self._mtime

    @property
    def ed2khash(self):
        if self._ed2khash:
            return self._ed2khash
        elif self._path:
            self._ed2khash = adbb.fileinfo.get_file_hash(self._path)
            return self._ed2khash
        adbb._log.debug("path not set, trying to fetch ed2khash from anidb")
        self.update()
        return self.db_data.ed2khash

    def __init__(self, path=None, fid=None, anime=None, episode=None):
        super().__init__()
        self._file_updated = threading.Event()
        self._mylist_updated = threading.Event()
        if not path and not fid and not (anime and episode):
            raise AniDBError("File must be created with either filname, fid "\
                    "or anime and episode.")

        if path:
            self._path = path
            self._mtime, self._size = adbb.fileinfo.get_file_stats(self._path)
        if fid:
            self._fid = int(fid)
        if anime:
            if isinstance(anime, Anime):
                self._anime = anime
            else:
                self._anime = Anime(anime)
            if isinstance(episode, Episode):
                self._episode = episode
            else:
                self._episode = Episode(anime=self._anime, epno=episode)

    def _get_db_data(self, force=False):
        adbb._log.debug("Fetching fileinfo for {}".format(self))
        if self._fid:
            res = self._db.query(FileTable).filter_by(fid=self._fid).all()
        else:
            res = self._db.query(FileTable).filter_by(path=self._path).all()
            if res and res[0].size != self._size:
                self._db.delete(res[0])
                self._db_commit()
                res = []
        if len(res) > 0:
            adbb._log.debug("Fetched file data from database: {}".format(res[0]))
            self.db_data = res[0]

        self._fetch_anidb_data(force)
        if self.db_data:
            aid = None
            episodes = None
            if not self.db_data.aid:
                if self._anime:
                    self.db_data.aid = self._anime.aid
                elif not self.db_data.aid or not self.db_data.eid:
                    aid, episodes = self._guess_anime_ep_from_file()
                    if aid:
                        self.db_data.aid = aid
            if not self.db_data.eid:
                if self._episode:
                    self.db_data.eid = self._episode.eid
                elif not episodes:
                    aid, episodes = self._guess_anime_ep_from_file()
                    if episodes:
                        self._multiep = episodes
                        self._episode = Episode(anime=aid, epno=episodes[0])
        else:
            adbb._log.debug("Could not find file in anidb.")


    def _anidb_file_data_callback(self, res):
        new = None
        if res.rescode in ('340', '320'):
            finfo = {'is_generic': True}
        else: 
            finfo = res.datalines[0]
            state = None
            if 'state' in finfo:
                state = int(finfo['state'])
                del finfo['state']

            remove = [x for x in finfo \
                    if x.startswith('mylist_') and finfo[x] == '']
            for attr in remove:
                del finfo[attr]

            for attr, data in finfo.items():
                finfo[attr] = adbb.mapper.file_map_f_converters[attr](data)

            if state & 0x1:
                finfo['crc_ok'] = True
            elif state & 0x2:
                finfo['crc_ok'] = False
            if state & 0x4:
                finfo['file_version'] = 2
            elif state & 0x8:
                finfo['file_version'] = 3
            elif state & 0x10:
                finfo['file_version'] = 4
            elif state & 0x20:
                finfo['file_version'] = 5
            else:
                finfo['file_version'] = 1
            if state & 0x40:
                finfo['censored'] = False
            elif state & 0x80:
                finfo['censored'] = True

            finfo['is_generic'] = False

        if self._path:
            finfo['path'] = self._path
            finfo['size'] = self._size
            finfo['ed2khash'] = self.ed2khash

        if not 'aid' in finfo:
            finfo['aid'] = 0
        if not 'eid' in finfo:
            finfo['eid'] = 0

        if self.db_data:
            self.db_data.update(**finfo)
            self.db_data.updated = datetime.datetime.now()
        else:
            new = FileTable(**finfo)
            new.updated = datetime.datetime.now()
            self._db.add(new)

        if new:
            self.db_data = new
        self._db_commit()
        self._file_updated.set()

    def _anidb_mylist_data_callback(self, res):
        new = None
        if res.rescode == '312':
            raise AniDBFileError("adbb currently does not support multiple mylist "\
                    "entries for a single episode")
        elif res.rescode == '321':
            finfo = {'is_generic': True}
        else:
            finfo = res.datalines[0]
            if 'lid' in finfo:
                del finfo['lid']
            if 'date' in finfo:
                del finfo['date']
            for attr, data in finfo.items():
                finfo[attr] = adbb.mapper.mylist_map_converters[attr](data)
        
        if self._path:
            finfo['path'] = self._path
            finfo['size'] = self._size
            finfo['ed2khash'] = self.ed2khash

        if self.db_data:
            self.db_data.update(**finfo)
            self.db_data.updated = datetime.datetime.now()
        else:
            new = FileTable(**finfo)
            new.updated = datetime.datetime.now()
            self._db.add(new)

        if new:
            self.db_data = new
        self._db_commit()

        self._mylist_updated.set()
            

    def _send_anidb_update_req(self):
        self._updated.clear()
        if self._fid:
            self._file_updated.clear()
            req = FileCommand(
                    fid=self._fid,
                    fmask=adbb.mapper.getFileBitsF(adbb.mapper.file_map_f),
                    amask=adbb.mapper.getFileBitsA([])
                    )
            adbb._log.debug("sending file request with fid")
            self._anidb_link.request(req, self._anidb_file_data_callback)
        elif self._size and self._path:
            self._file_updated.clear()
            req = FileCommand(
                    size=self._size, 
                    ed2k=self.ed2khash, 
                    fmask=adbb.mapper.getFileBitsF(adbb.mapper.file_map_f),
                    amask=adbb.mapper.getFileBitsA([]))
            adbb._log.debug("sending file request with size and hash")
            self._anidb_link.request(req, self._anidb_file_data_callback)
        self._file_updated.wait()
        if self._fid:
            adbb._log.debug("fetching mylist with fid")
            req = MyListCommand(fid=self._fid)
        else:
            if self._path and not (self._anime and self._episode):
                if self.db_data and self.db_data.aid:
                    self._anime = Anime(self.db_data.aid)
                if self.db_data and self.db_data.eid:
                    self._episode = Episode(eid=self.db_data.eid)
                if not (self._anime and self._episode):
                    aid, episodes = self._guess_anime_ep_from_file()
                    if aid and episodes:
                        if self.db_data and not self.db_data.aid:
                            self.db_data.aid = aid
                        if not self._anime:
                            self._anime = Anime(aid)
                        if not self._episode:
                            self._episode = Episode(anime=self._anime,epno=episodes[0])
            adbb._log.debug("fetching mylist with aid and epno")
            req = MyListCommand(
                    aid=self.anime.aid,
                    epno=self.episode.episode_number)
        adbb._log.debug("sending mylist request")
        self._anidb_link.request(req, self._anidb_mylist_data_callback)
        self._mylist_updated.wait()
        self._updated.set()

    def __getattr__(self, name):
        if not self.db_data:
            self._get_db_data()
        return getattr(self.db_data, name, None)

    def __repr__(self):
        return "File(path='{}', fid={}, anime={}, episode={})".\
                format(
                    self._path, 
                    self._fid, 
                    self._anime, 
                    self._episode)

    def remove_from_mylist(self):
        wait = threading.Event()

        def _mylistdel_callback(res):
            if res.rescode == '211':
                adbb._log.info("File {} removed from mylist".format(self))
            elif res.rescode == '411':
                adbb._log.warning("File {} was not in mylist".format(self))
            wait.set()

        if self.db and self.db_data.fid:
            req = MyListDelCommand(fid=self.data.fid)
            self._anidb_link.request(req, _mylistdel_callback)
        elif self.size and self.ed2khash:
            req = MyListDelCommand(
                    size=self.size,
                    ed2k=self.ed2khash)
            self._anidb_link.request(req, _mylistdel_callback)
        else:
            if self._multiep:
                episodes = self._multiep
            else:
                episodes = [self.episode.episode_number]
            for ep in episodes:
                wait.clear()
                req = MyListDelCommand(
                        aid=self.anime.aid,
                        epno=self.episode.episode_number)
                self._anidb_link.request(req, _mylistdel_callback)
                wait.wait()
        wait.wait()

    def add_to_mylist(
            self, 
            state='on hdd',
            watched=False,
            source=None,
            other=None):
        wait = threading.Event()
        if not self.db_data:
            self._get_db_data()

        def _mylistadd_callback(res):
            if res.rescode in ('320', '330', '350', '310'):
                adbb._log.warning("Could not add file {} to mylist, anidb "\
                        "says: {}".format(self, res.rescode))
            elif res.rescode in (210, 310):
                adbb._log.info("File {} added to mylist".format(self))
            wait.set()

        state_num = [x for x,y in adbb.mapper.mylist_state_map.items() if y==state][0]
        if watched:
            watched = 1
        else:
            watched = 0

        if self.fid:
            req = MyListAddCommand(
                    fid=self.fid, 
                    state=state_num, 
                    viewed=watched,
                    source=source,
                    other=other)
            self._anidb_link.request(req, _mylistadd_callback)
        elif self.is_generic:
            if self._multiep:
                episodes = self._multiep
            else:
                episodes = [self.episode.episode_number]
            for ep in episodes:
                wait.clear()
                req = MyListAddCommand(
                        aid=self.anime.aid,
                        epno=ep,
                        generic=1,
                        state=state_num, 
                        viewed=watched,
                        source=source,
                        other=other)
                self._anidb_link.request(req, _mylistadd_callback)
                wait.wait()
        else:
            req = MyListAddCommand(
                    size=self.size,
                    ed2k=self.ed2khash,
                    state=state_num, 
                    viewed=watched,
                    source=source,
                    other=other)
            self._anidb_link.request(req, _mylistadd_callback)
        wait.wait()
        self.db_data.mylist_state=state
        self.db_data.mylist_viewed=watched
        if watched:
            self.db_data.mylist_viewdate = datetime.datetime.now()
        self.db_data.mylist_source=source
        self.db_data.mylist_other=other
        self._db_commit()

    def _guess_anime_ep_from_file(self):
        if not self.path:
            return (None, None)
        head, filename = os.path.split(self.path)
        head, parent_dir = os.path.split(head)

        # try to figure out which episodes this file contains
        episodes = self._guess_epno_from_filename(filename)
        if not episodes:
            return (None, None)
        anime = None

        # first try to figure out anime by the directory name
        if parent_dir:
            series = adbb.anames.get_titles(name=parent_dir)
            if series:
                anime = series[0][0]
                adbb._log.debug("dir '{}': score {} for '{}'".format(
                        parent_dir, series[0][2], series[0][3]))
            else:
                adbb._log.debug("dir '{}': no match".format(parent_dir))

        # no confident hit on parent directory, trying filename
        if not anime:
            # strip away all kinds of paranthesis like
            # [<group>], (<codec>) or {<crc>}.
            stripped = re.sub(r'[{[(][^\]})]*?[})\]]', '', filename)
            # remove the file ending
            stripped, tail = stripped.rsplit('.', 1)
            # split out all words, this removes all dots, dashes and other
            # unhealthy things :)
            # Don't know if I should remove numbers here as well...
            splitted = re.findall(r'[\w]+', stripped)
            # Join back to a single string 
            joined = " ".join(splitted)
            # search anidb, but require lower score for match as this is
            # probably not very similar to the real title...
            series = adbb.anames.get_titles(name=joined, score_for_match=0.6)
            if series:
                adbb._log.debug("file '{}': trimmed to '{}', score {} for '{}'"\
                        .format(filename, joined, series[0][2], series[0][3]))
                anime = series[0][0]
            else:
                adbb._log.debug("file '{}': trimmed to '{}', no match".\
                        format(filename, joined))
        if not anime:
            return (None, None)

        return (anime, episodes)

    def _guess_epno_from_filename(self, filename):
        ret = []
        for r in adbb.fileinfo.ep_nr_re:
            res = r.search(filename)
            if res:
                eps = adbb.fileinfo.multiep_re.findall(res.group(3))
                eps.insert(0, res.group(2))
                for m in eps:
                    try:
                        ep = int(m)
                    except ValueError:
                        continue
                    if res.group(1).lower() == 's':
                        ret.append("S{}".format(ep))
                    else:
                        ret.append(str(ep))
                adbb._log.debug("file '{}': looks like episode(s) {}".format(
                        filename, ret))
                break
        if not ret:
            adbb._log.debug("file '{}': could not figure out episode number(s)"\
                    .format(filename))
        return ret
