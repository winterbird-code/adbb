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
import random
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

class AniDBObj(object):

    def __init__(self):
        self._anidb_link = adbb._anidb
        self._updated = threading.Event()
        self._updating = threading.Lock()
        self.db_data = None

    def _fetch_anidb_data(self, block):
        adbb._log.debug("Seding anidb request for {}".format(self))
        thread = threading.Thread(
                target=self._send_anidb_update_req,
                kwargs={'prio': block})
        thread.start()
        if block:
            thread.join()

    def update(self, block=False):
        locked = self._updating.acquire(False)
        if not locked:
            if block:
                self._updating.acquire(True)
                self._updating.release()
            return
        self._fetch_anidb_data(block=block)

    def _extra_refresh_probability(self):
        return 0

    def update_if_old(self, block=False):
        if not self.db_data:
            self.update(block=True)
        else:
            age = datetime.datetime.now()-self.db_data.updated
            ref = datetime.timedelta()
            # never update twice the same day...
            if age < datetime.timedelta(days=1):
                return
            # probability is in percent
            # start with any extra refresh probability-parameters this class
            # implements. Default is 0, meaning it will never request the same
            # data twice the first week, no matter how many times you request
            # the data.
            # 
            # add 5 % probability for each of the first 4 weeks, and then
            # additional 10% for each month (or 30 days) after that.
            class_probability = self._extra_refresh_probability()
            refresh_probability = class_probability
            while refresh_probability < 100:
                age -= datetime.timedelta(weeks=1)
                if age < ref:
                    break
                refresh_probability += 5
            while refresh_probability < 100:
                age -= datetime.timedelta(days=30)
                if age < ref:
                    break
                refresh_probability += 10
            refresh_probability = min(100, refresh_probability)
            adbb._log.debug("Probability of updating {}: {}% ({}% from class rules)".format(
                    self, refresh_probability, class_probability))
            if random.randrange(100) < refresh_probability:
                self.update(block=block)


    def _send_anidb_update_req(self):
        raise Exception("Not implemented")

    def _close_db_session(self, session):
        session.close()

    def _get_db_session(self):
        return adbb.get_session()

    def _db_commit(self, session):
        try:
            session.commit()
        except sqlalchemy.exc.DBAPIError as e:
            if self.db_data:
                adbb._log.warning("Failed to update data {}: {}".format(
                    self.db_data, e))
            else:
                adbb._log.warning("Failed to update db: {}".format(e))
            session.rollback()

    def __getattr__(self, name):
        self._updating.acquire()
        self._updating.release()
        if not self.db_data:
            self.update(block=True)
        self.update_if_old()
        return getattr(self.db_data, name, None)


class Anime(AniDBObj):

    def __init__(self, init):
        super(Anime, self).__init__()
        self.aid = None
        self.titles = None
        self.title = None

        if isinstance(init, int):
            self.aid, self.titles, score, best_title = adbb.anames.get_titles(
                    aid=init)[0]
        elif isinstance(init, str):
            self.aid, self.titles, score, best_title = adbb.anames.get_titles(
                    name=init)[0]

        self.title = [x.title for x in self.titles 
                if x.lang == None and x.titletype == 'main'][0]
        self._get_db_data()

    def _extra_refresh_probability(self):
        now = datetime.datetime.now()
        ref = datetime.timedelta()
        # The shorter time there is between when anidb updated this
        # anime and we fetched our data, the more likely is it that it has
        # changed again. So we start at 60%, and removes 20% for each week
        probability = 60
        data_age = self.db_data.updated-self.db_data.anidb_updated
        while probability > 0:
            data_age -= datetime.timedelta(weeks=1)
            if data_age < ref:
                break
            probability -= 20
        return max(probability, 0)

    def _get_db_data(self, close=True):
        sess = self._get_db_session()
        res = sess.query(AnimeTable).filter_by(aid=self.aid).all()
        if len(res) > 0:
            self.db_data = res[0]
        if close:
            self._close_db_session(sess)

    def _db_data_callback(self, res):
        sess = self._get_db_session()
        ainfo = res.datalines[0]
        relations = []
        new = None

        if all([x in ainfo and ainfo[x] for x in ['related_aid_list', 'related_aid_type']]):
            relations = zip(
                    ainfo['related_aid_list'].split("'"), 
                    ainfo['related_aid_type'].split("'"))
        if 'related_aid_list' in ainfo:
            del ainfo['related_aid_list']
        if 'related_aid_type' in ainfo:
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
            self.db_data = sess.merge(self.db_data)
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
                    sess.delete(r)
            self.db_data.relations = new_relations
        else:
            new = AnimeTable(**ainfo)
            new.updated = datetime.datetime.now()

            new.relations = relations

            # commit to sql database
            sess.add(new)

        if new:
            self.db_data = new
        self._db_commit(sess)
        self._close_db_session(sess)
        self._updated.set()

    def _send_anidb_update_req(self, prio=False):
        self._updated.clear()
        req = AnimeCommand(
                aid=str(self.aid), 
                amask=adbb.mapper.getAnimeBitsA(adbb.mapper.anime_map_a))
        self._anidb_link.request(req, self._db_data_callback, prio=prio)
        self._updated.wait()
        self._updating.release()

    @property
    def relations(self):
        self.update_if_old()
        return [(x.relation_type, Anime(x.related_aid)) \
                    for x in self.db_data.relations]

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
        if not self.db_data or not self.db_data.eid:
            self.update(block=True)
        return self.db_data.eid

    @property
    def anime(self):
        if self._anime:
            return self._anime
        if not self.db_data or not self.db_data.aid:
            self.update(block=True)
        self._anime = Anime(self.db_data.aid)
        return self._anime

    @property
    def episode_number(self):
        if self._episode_number:
            return self._episode_number
        if not self.db_data or not self.db_data.epno:
            self.update(block=True)
        return self.db_data.epno

    def __init__(self, anime=None, epno=None, eid=None):
        super(Episode, self).__init__()

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
        self._get_db_data()

    def _extra_refresh_probability(self):
        probability = 0
        # For episdes, add 50% probability if the episode name is 
        # "Episode <epnr>"
        if re.match("Episode {}".format(self.db_data.epno), self.db_data.title_eng):
            probability = 50
        return max(probability, 0)

    def _get_db_data(self):
        sess = self._get_db_session()
        if self._eid:
            res = sess.query(EpisodeTable).filter_by(eid=self._eid).all()
        else:
            res = sess.query(EpisodeTable).filter_by(
                    aid=self._anime.aid,
                    epno=self.episode_number).all()
        if len(res) > 0:
            self.db_data = res[0]
            adbb._log.debug("Found db_data for episode: {}".format(self.db_data))
        self._close_db_session(sess)

    def _anidb_data_callback(self, res):
        sess = self._get_db_session()
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
            self.db_data = sess.merge(self.db_data)
            self.db_data.update(**einfo)
            self.db_data.updated = datetime.datetime.now()
        else:
            new = EpisodeTable(**einfo)
            new.updated = datetime.datetime.now()
            sess.add(new)

        if new:
            self.db_data = new

        self._db_commit(sess)
        self._close_db_session(sess)
        self._updated.set()

    def _send_anidb_update_req(self, prio=False):
        self._updated.clear()
        if self._eid:
            req = EpisodeCommand(eid=self._eid)
        else:
            req = EpisodeCommand(aid=self._anime.aid, epno=self.episode_number)
        self._anidb_link.request(req, self._anidb_data_callback, prio=prio)
        self._updated.wait()
        self._updating.release()
        

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
        # wait for any update process to finish
        self._updating.acquire()
        self._updating.release()
        if not self.db_data or not self.db_data.aid:
            self.update(block=True)
        if self.db_data.aid:
            self._anime = Anime(self.db_data.aid)
        return self._anime

    @property
    def episode(self):
        if self._episode:
            return self._episode
        # wait for any update process to finish
        self._updating.acquire()
        self._updating.release()
        if not self.db_data or not self.db_data.eid:
            self.update(block=True)
        if self.db_data.eid:
            self._episode = Episode(eid=self.db_data.eid)
        elif self._multiep and self._anime:
            self._episode = Episode(anime=self._anime, epno=_multiep[0])
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
        # wait for any update process to finish
        self._updating.acquire()
        self._updating.release()
        if not self.db_data or not self.db_data.fid:
            self.update_if_old(block=True)
        return self.db_data.fid

    @property
    def path(self):
        if self._path:
            return self._path
        elif self.db_data:
            return self.db_data.path
        return None

    @property
    def size(self):
        if self._size:
            return self._size
        if self.path:
            self._mtime, self._size = adbb.fileinfo.get_file_stats(
                    self.path,
                    self.nfs_obj)
        elif self.db_data and self.db_data.size:
            self._size = self.db_data.size
        return self._size

    @property
    def mtime(self):
        if self._mtime:
            return self._mtime
        if self.path:
            self._mtime, self._size = adbb.fileinfo.get_file_stats(
                    self.path,
                    self.nfs_obj)
        elif self.db_data and self.db_data.mtime:
            self._mtime = self.db_data.mtime
        return self._mtime

    @property
    def ed2khash(self):
        if self._ed2khash:
            return self._ed2khash
        elif self.path:
            if self.db_data and \
                    self.db_data.mtime and \
                    self.db_data.size and \
                    self.db_data.ed2khash:
                mtime, size = adbb.fileinfo.get_file_stats(
                        self.path,
                        self.nfs_obj)
                if mtime == self.db_data.mtime and size == self.db_data.size:
                    self._ed2khash = self.db_data.ed2khash

            if not self._ed2khash:
                self._ed2khash = adbb.fileinfo.get_file_hash(
                        self._path,
                        self.nfs_obj)
            return self._ed2khash

        # wait for any update process to finish
        self._updating.acquire()
        self._updating.release()
        if not self.db_data and not self.db_data.ed2khash:
            self.update_if_old(block=True)
        if self.db_data:
            self._ed2khash = self.db_data.ed2khash
        return self._ed2khash

    def __init__(
            self, 
            path=None, 
            fid=None, 
            anime=None, 
            episode=None,
            nfs_obj=None):
        super(File, self).__init__()
        self._file_updated = threading.Event()
        self._mylist_updated = threading.Event()
        if not path and not fid and not (anime and episode):
            raise AniDBError("File must be created with either filname, fid "\
                    "or anime and episode.")

        self.nfs_obj = nfs_obj
        if path:
            self._path = path
            self._mtime, self._size = adbb.fileinfo.get_file_stats(
                    self._path,
                    self.nfs_obj)
            adbb._log.debug("Created File {} - size: {}, mtime: {}".format(self._path, self._size, self._mtime))
        if fid:
            self._fid = int(fid)
        if anime:
            if isinstance(anime, Anime):
                self._anime = anime
            else:
                self._anime = Anime(anime)
            if episode:
                if isinstance(episode, Episode):
                    self._episode = episode
                else:
                    self._episode = Episode(anime=self._anime, epno=episode)
        self._get_db_data()

    def _extra_refresh_probability(self):
        probability = 0
        # For files, add 10% probability if it is a generic file that is in
        # mylist.
        if self.db_data.is_generic and self.db_data.mylist_state:
            probability = 10
        return max(probability, 0)

    def _get_db_data(self):
        sess = self._get_db_session()
        if self._fid:
            res = sess.query(FileTable).filter_by(fid=self._fid).all()
        elif self._path:
            res = sess.query(FileTable).filter_by(path=self._path).all()
            if res and res[0].size != self._size:
                sess.delete(res[0])
                self._db_commit(sess)
                res = []
        else:
            # When created with just Anime and Episode, we want file
            # information from mylist first
            return
        if len(res) > 0:
            self.db_data = res[0]
            adbb._log.debug("Found db_data for file: {}".format(self.db_data))
        self._close_db_session(sess)


    def _anidb_file_data_callback(self, res):
        sess = self._get_db_session()
        new = None
        update_mylist = False
        if res.rescode in ('340', '320'):
            finfo = {'is_generic': True}
        else: 
            finfo = res.datalines[0]
            state = None

            # if this file previously was generic, the file has probably been
            # added to anidb. We should remove any generic file from mylist and
            # add this instead.
            if self.db_data and self.db_data.is_generic: 
                update_mylist = True

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

        if update_mylist:
            finfo['mylist_state'] = self.db_data.mylist_state
            finfo['mylist_viewed'] = self.db_data.mylist_viewed
            finfo['mylist_viewdate'] = self.db_data.mylist_viewdate
            finfo['mylist_source'] = self.db_data.mylist_source
            finfo['mylist_other'] = self.db_data.mylist_other
            finfo['lid'] = None
            self.remove_from_mylist()

        if self.db_data:
            self.db_data = sess.merge(self.db_data)
            self.db_data.update(**finfo)
            self.db_data.updated = datetime.datetime.now()
        else:
            new = FileTable(**finfo)
            new.updated = datetime.datetime.now()
            sess.add(new)

        if new:
            self.db_data = new
        self._db_commit(sess)
        self._close_db_session(sess)
        self._file_updated.set()

        if update_mylist:
            self.add_to_mylist(
                    state = self.db_data.mylist_state,
                    watched = self.db_data.mylist_viewedate,
                    source = self.db_data.mylist_source,
                    other = self.db_data.mylist_other)


    def _anidb_mylist_data_callback(self, res):
        sess = self._get_db_session()
        new = None
        if res.rescode == '312':
            raise AniDBFileError("adbb currently does not support multiple mylist "\
                    "entries for a single episode")
        elif res.rescode == '321':
            finfo = {'is_generic': True}
            if self._anime:
                finfo['aid'] = self._anime.aid
            if self._episode:
                finfo['eid'] = self._episode.eid
        else:
            finfo = res.datalines[0]
            if 'date' in finfo:
                del finfo['date']
            for attr, data in finfo.items():
                finfo[attr] = adbb.mapper.mylist_map_converters[attr](data)
            finfo['is_generic'] = False
        
        if self.db_data:
            # if we have a path, but don't have a fid and still got a fid from the mylist
            # command, it is not this file that is in mylist
            if self.path and (not self.db_data.fid or self.db_data.fid != finfo['fid']):
                finfo = {}
            self.db_data = sess.merge(self.db_data)
            self.db_data.update(**finfo)
            self.db_data.updated = datetime.datetime.now()
        else:
            new = FileTable(**finfo)
            new.updated = datetime.datetime.now()
            sess.add(new)

        if new:
            self.db_data = new
        self._db_commit(sess)
        self._close_db_session(sess)
        self._mylist_updated.set()
            

    def _send_anidb_update_req(self, prio=False):
        adbb._log.debug("updating - fid: {}, size: {}, path: {}".format(
                self._fid,
                self._size,
                self._path))
        if self._fid:
            self._file_updated.clear()
            req = FileCommand(
                    fid=self._fid,
                    fmask=adbb.mapper.getFileBitsF(adbb.mapper.file_map_f),
                    amask=adbb.mapper.getFileBitsA([])
                    )
            adbb._log.debug("sending file request with fid")
            self._anidb_link.request(req, self._anidb_file_data_callback,
                    prio=prio)
            self._file_updated.wait()
        elif self._size and self._path:
            self._file_updated.clear()
            req = FileCommand(
                    size=self._size, 
                    ed2k=self.ed2khash, 
                    fmask=adbb.mapper.getFileBitsF(adbb.mapper.file_map_f),
                    amask=adbb.mapper.getFileBitsA([]))
            adbb._log.debug("sending file request with size and hash")
            self._anidb_link.request(req, self._anidb_file_data_callback,
                    prio=prio)
            self._file_updated.wait()
        if self._fid:
            adbb._log.debug("fetching mylist with fid")
            req = MyListCommand(fid=self._fid)
        else:
            if self._path:
                if self.db_data and self.db_data.aid:
                    self._anime = Anime(self.db_data.aid)
                if self.db_data and self.db_data.eid:
                    self._episode = Episode(eid=self.db_data.eid)
                if not (self._anime and self._episode):
                    aid, self._multiep = self._guess_anime_ep_from_file(aid=self._anime.aid)
                    if aid and self._multiep:
                        sess = self._get_db_session()
                        if self.db_data and not self.db_data.aid:
                            self.db_data.aid = aid
                        if not self._anime:
                            self._anime = Anime(aid)
                        if not self._episode:
                            self._episode = Episode(anime=self._anime,epno=self._multiep[0])
                        if self.db_data and not self.db_data.eid:
                            self.db_data.eid = self._episode.eid
                            self._db_commit(sess)
                            self._close_db_session(sess)
            adbb._log.debug("fetching mylist with aid and epno")
            req = MyListCommand(
                    aid=self._anime.aid,
                    epno=self.episode.episode_number)
        adbb._log.debug("sending mylist request")
        self._anidb_link.request(req, self._anidb_mylist_data_callback,
                prio=prio)
        self._mylist_updated.wait()
        self._updating.release()

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

        if self.db_data and self.db_data.fid:
            req = MyListDelCommand(fid=self.db_data.fid)
            self._anidb_link.request(req, _mylistdel_callback, prio=True)
        elif self.is_generic:
            if self._multiep:
                episodes = self._multiep
            else:
                episodes = [self.episode.episode_number]
            for ep in episodes:
                wait.clear()
                req = MyListDelCommand(
                        aid=self._anime.aid,
                        epno=self.episode.episode_number)
                self._anidb_link.request(req, _mylistdel_callback, prio=True)
                wait.wait()
        else:
            req = MyListDelCommand(
                    size=self.size,
                    ed2k=self.ed2khash)
            self._anidb_link.request(req, _mylistdel_callback, prio=True)
        wait.wait()

    def update_mylist(
            self, 
            state=None,
            watched=None,
            source=None,
            other=None):
        wait = threading.Event()
        self.update_if_old()
        viewdate = None

        def _mylistadd_callback(res):
            if res.rescode in ('320', '330', '350', '310', '322', '411'):
                adbb._log.warning("Could not add file {} to mylist, anidb "\
                        "says: {}".format(self, res.rescode))
            elif res.rescode in ('210', '310', '311'):
                adbb._log.info("File {} added to mylist".format(self))
            wait.set()

        try:
            state_num = [x for x,y in adbb.mapper.mylist_state_map.items() if y==state][0]
        except IndexError:
            state_num = None

        if watched:
            if isinstance(watched, datetime.datetime):
                viewdate = int(watched.timestamp())
            viewed = 1
        elif watched == False:
            viewed = 0
        else:
            viewed = None

        if self.lid:
            req = MyListAddCommand(
                    lid=self.db_data.lid,
                    edit=1,
                    state=state_num, 
                    viewed=viewed,
                    viewdate=viewdate,
                    source=source,
                    other=other)
        if self.fid:
            req = MyListAddCommand(
                    fid=self.fid, 
                    state=state_num, 
                    viewed=viewed,
                    viewdate=viewdate,
                    source=source,
                    other=other)
            self._anidb_link.request(req, _mylistadd_callback, prio=True)
        elif self.is_generic:
            if self._multiep:
                episodes = self._multiep
            else:
                episodes = [self.episode.episode_number]
            for ep in episodes:
                wait.clear()
                req = MyListAddCommand(
                        aid=self._anime.aid,
                        epno=ep,
                        generic=1,
                        state=state_num, 
                        viewed=viewed,
                        viewdate=viewdate,
                        source=source,
                        other=other)
                self._anidb_link.request(req, _mylistadd_callback, prio=True)
                wait.wait()
        else:
            req = MyListAddCommand(
                    size=self.size,
                    ed2k=self.ed2khash,
                    state=state_num, 
                    viewed=viewed,
                    viewdate=viewdate,
                    source=source,
                    other=other)
            self._anidb_link.request(req, _mylistadd_callback, prio=True)
        wait.wait()
        sess = self._get_db_session()
        self.db_data = sess.merge(self.db_data)
        self.db_data.mylist_state=state
        self.db_data.mylist_viewed=viewed
        if watched:
            if isinstance(watched, datetime.datetime):
                self.db_data.mylist_viewdate = watched
            else:
                self.db_data.mylist_viewdate = datetime.datetime.now()
        self.db_data.mylist_source=source
        self.db_data.mylist_other=other
        self._db_commit(sess)
        self._close_db_session(sess)

    def _guess_anime_ep_from_file(self, aid=None):
        if not self.path:
            return (None, None)
        head, filename = os.path.split(self.path)
        head, parent_dir = os.path.split(head)

        # try to figure out which episodes this file contains
        episodes = self._guess_epno_from_filename(filename)
        if not episodes:
            return (None, None)
        if aid:
            return (aid, episodes)

        # first try to figure out anime by the directory name
        if parent_dir:
            series = adbb.anames.get_titles(name=parent_dir)
            if series:
                aid = series[0][0]
                adbb._log.debug("dir '{}': score {} for '{}'".format(
                        parent_dir, series[0][2], series[0][3]))
            else:
                adbb._log.debug("dir '{}': no match".format(parent_dir))

        # no confident hit on parent directory, trying filename
        if not aid:
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
            series = adbb.anames.get_titles(name=joined, score_for_match=0.5)
            if series:
                adbb._log.debug("file '{}': trimmed to '{}', score {} for '{}'"\
                        .format(filename, joined, series[0][2], series[0][3]))
                aid = series[0][0]
            else:
                adbb._log.debug("file '{}': trimmed to '{}', no match".\
                        format(filename, joined))
        if not aid:
            return (None, None)

        return (aid, episodes)

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
