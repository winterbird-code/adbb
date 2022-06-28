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
        self._illegal_object = False
        self._updated = threading.Event()
        self._updating = threading.Lock()
        self._timezone = datetime.timezone(datetime.timedelta(hours=0))
        self.db_data = None

    def _to_timezoneaware(self, obj):
        if obj.tzinfo == None or obj.tzinfo.utcoffset(obj) == None:
            return obj.replace(tzinfo=self._timezone)
        return obj

    def _fetch_anidb_data(self, block):
        adbb.log.debug("Seding anidb request for {}".format(self))
        thread = threading.Thread(
            target=self._send_anidb_update_req,
            kwargs={'prio': block})
        thread.start()
        if block:
            thread.join()
        if self._illegal_object:
            raise IllegalAnimeObject("{} is not a valid AniDB object".format(self))

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
            age = datetime.datetime.now(self._timezone) - self._to_timezoneaware(self.db_data.updated)
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
            # add 1% probability for each of the first 4 weeks, and then
            # additional 5% for each month (or 30 days) after that.
            class_probability = self._extra_refresh_probability()
            refresh_probability = class_probability
            while refresh_probability < 100:
                age -= datetime.timedelta(weeks=1)
                if age < ref:
                    break
                refresh_probability += 1
            while refresh_probability < 100:
                age -= datetime.timedelta(days=30)
                if age < ref:
                    break
                refresh_probability += 5
            refresh_probability = min(100, refresh_probability)
            adbb.log.debug("Probability of updating {}: {}% ({}% from class rules)".format(
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
            adbb.log.debug("Object saved to database: {}".format(self.db_data))
        except sqlalchemy.exc.DBAPIError as e:
            if self.db_data:
                adbb.log.warning("Failed to update data {}: {}".format(
                    self.db_data, e))
            else:
                adbb.log.warning("Failed to update db: {}".format(e))
            session.rollback()

    def __getattr__(self, name):
        local_vars = vars(self)
        local_name = "_{}".format(name)
        # adbb._log.debug("Requested attribute {} (in local_vars: {})".format(
        #    name, local_name in local_vars))
        if local_name in local_vars and local_vars[local_name]:
            return local_vars[local_name]

        self._updating.acquire()
        self._updating.release()
        self.update_if_old()
        return getattr(self.db_data, name, None)


class Anime(AniDBObj):
    def __init__(self, init):
        super(Anime, self).__init__()
        self._aid = None
        self._titles = None
        self._title = None

        if isinstance(init, int):
            self._aid, self._titles, score, best_title = adbb.anames.get_titles(
                aid=init)[0]
        elif isinstance(init, str):
            self._aid, self._titles, score, best_title = adbb.anames.get_titles(
                name=init)[0]

        self._title = [x.title for x in self.titles
                       if x.lang is None and x.titletype == 'main'][0]
        self.db_data = None
        self._get_db_data()

    def _extra_refresh_probability(self):
        now = datetime.datetime.now(self._timezone)
        ref = datetime.timedelta()
        # The shorter time there is between when anidb updated this
        # anime and we fetched our data, the more likely is it that it has
        # changed again. So we start at 60%, and removes 20% for each week
        probability = 60
        data_age = self._to_timezoneaware(self.db_data.updated) - self.db_data.anidb_updated.replace(tzinfo=self._timezone)
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
        ainfo = res.datalines[0]
        relations = []
        new = None
        if res.rescode == "330":
            self._illegal_object = True
            self.log.warning('{} is not a valid Anime object'.format(self))
            self._updated.set()
            return

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

        sess = self._get_db_session()
        if self.db_data:
            self.db_data = sess.merge(self.db_data)
            self.db_data.update(**ainfo)
            self.db_data.updated = datetime.datetime.now(self._timezone)
            new_relations = []
            for r in relations:
                found = False
                for sr in self.db_data.relations:
                    if r.related_aid == sr.related_aid:
                        found = True
                        sr.relation_type = r.relation_type
                        sr.anime_pk = self.db_data.pk
                        new_relations.append(sr)
                if not found:
                    r.anime_pk = self.db_data.pk
                    new_relations.append(r)
            for r in self.db_data.relations:
                if r not in new_relations:
                    sess.delete(r)
            self.db_data.relations = new_relations
        else:
            new = AnimeTable(**ainfo)
            new.updated = datetime.datetime.now(self._timezone)
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
        try:
            relations = [(x.relation_type, Anime(x.related_aid)) for x in self.db_data.relations]
        except sqlalchemy.orm.exc.DetachedInstanceError:
            self._get_db_data(close=False)
            sess = self._get_db_session()
            relations = [(x.relation_type, Anime(x.related_aid)) for x in self.db_data.relations]
            self._close_db_session(sess)
        return relations

    def __eq__(self, other):
        if not isinstance(other, Anime):
            return NotImplemented
        return self.aid == other.aid

    def __contains__(self, other):
        if not isinstance(other, Episode):
            return NotImplemented
        return other.aid == self.aid

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
    def episode_number(self):
        if self._episode_number:
            return self._episode_number
        return self.epno

    @property
    def eid(self):
        eid = self.__getattr__('eid')
        if eid:
            return eid
        elif self.db_data and not self.db_data.eid:
            self.update(True)
        return self.db_data.eid

    def __init__(self, anime=None, epno=None, eid=None):
        super(Episode, self).__init__()

        if not ((anime and epno) or eid):
            raise IllegalAnimeObject(
                    "Episode must be created with either anime and epno, "\
                    "or eid.")
        if eid:
            self._eid = eid
        if anime:
            if isinstance(anime, Anime):
                self._anime = anime
            else:
                self._anime = Anime(anime)
        if epno:
            try:
                epno = str(int(epno))
            except ValueError:
                pass
            self._episode_number = epno
        self.db_data = None
        self._get_db_data()

    def _extra_refresh_probability(self):
        probability = 0
        # For episdes, add 10% probability if the episode name is 
        # "Episode <epnr>"
        if re.match("Episode {}".format(self.db_data.epno), self.db_data.title_eng):
            probability = 10
        return max(probability, 0)

    def _get_db_data(self):
        sess = self._get_db_session()
        if self._eid:
            res = sess.query(EpisodeTable).filter_by(eid=self._eid).all()
        else:
            res = sess.query(EpisodeTable).filter(
                EpisodeTable.aid==self._anime.aid,
                EpisodeTable.epno.ilike(self.episode_number)).all()
        if len(res) > 0:
            self.db_data = res[0]
            adbb.log.debug("Found db_data for episode: {}".format(self.db_data))
            if self.db_data.epno:
                self._episode_number = self.db_data.epno
        self._close_db_session(sess)

    def _anidb_data_callback(self, res):
        sess = self._get_db_session()
        if res.rescode == "340":
            adbb.log.warning("No such episode in anidb: {}".format(self))
            self._illegal_object = True
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
            self.db_data.updated = datetime.datetime.now(self._timezone)
        else:
            new = EpisodeTable(**einfo)
            new.updated = datetime.datetime.now(self._timezone)
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

    def __eq__(self, other):
        if not isinstance(other, Episode):
            return NotImplemented
        if self._eid and other._eid:
            return self._eid == other._eid
        if self._lid and other._lid:
            return self._lid == other._lid
        if self._episode_number and other._episode_number:
            return self._episode_number == other._episode_number
        return self.eid == other.eid or self.lid == other.lid

    def __repr__(self):
        return "Episode(anime={}, episode_number='{}', eid={})".format(
            self._anime, self._episode_number, self._eid)


class File(AniDBObj):
    _anime = None
    _episode = None
    _group = None
    _multiep = []
    _fid = None
    _path = None
    _size = None
    _ed2khash = None
    _mtime = None
    _lid = None

    @property
    def anime(self):
        if self._anime:
            return self._anime
        self._anime = Anime(self.aid)
        return self._anime

    @property
    def episode(self):
        if self._episode:
            return self._episode
        kwargs = {}
        if self._multiep:
            kwargs['epno'] = self._multiep[0]
        if self._anime:
            kwargs['anime'] = self._anime
        if self.db_data and self.db_data.eid:
            kwargs['eid'] = self.db_data.eid
        if ('epno' in kwargs and 'anime' in kwargs) \
                or 'eid' in kwargs:
            adbb.log.debug("Creating episode with {}".format(kwargs))
            self._episode = Episode(**kwargs)
        elif self.eid:
            self._episode = Episode(eid=self.eid)
        else: 
            anime, episodes = self._guess_anime_ep_from_file(aid=self._anime.aid)
            self._episode = episodes[0]
        return self._episode

    @property
    def group(self):
        if self._group:
            return self._group
        if self.gid:
            self._group = Group(gid=self.gid)
            return self._group
        return None

    @property
    def multiep(self):
        """Return all episode numbers if there are more of them. Note that this
        is very much not reliable since this attribute is not stored in the
        database.

        FIXME: add multiep attribute to database..."""
        if self._multiep:
            return self._multiep

        # always try to parse filename first
        if self.path:
            episodes = self._guess_epno_from_filename(os.path.split(self.path)[1], self.anime)
            if episodes:
                self._multiep = [ ep.episode_number for ep in episodes ]
                return self._multiep
        self._multiep = [self.episode.episode_number]

        return self._multiep

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
        elif self._path:
            if self.db_data and \
                    self.db_data.mtime and \
                    self.db_data.size and \
                    self.db_data.ed2khash:
                mtime, size = adbb.fileinfo.get_file_stats(
                    self.path,
                    self.nfs_obj)
                if mtime == self.db_data.mtime and size == self.db_data.size:
                    self._ed2khash = self.db_data.ed2khash

            if self._ed2khash:
                return self._ed2khash

            self._ed2khash = adbb.fileinfo.get_file_hash(
                self._path,
                self.nfs_obj)
            adbb.log.debug("Calculated ed2khash: {}".format(self._ed2khash))
            return self._ed2khash

        adbb.log.debug("Trying to fetch ed2khash from anidb")
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
            lid=None,
            anime=None,
            episode=None,
            nfs_obj=None,
            force_single_episode_series=False,
            parse_dir=True):
        super(File, self).__init__()
        self.force_single_episode_series = force_single_episode_series
        self.parse_dir = parse_dir
        self._file_updated = threading.Event()
        self._mylist_updated = threading.Event()
        adbb.log.debug("path: {}, fid: {}, anime: {}, episode: {}, lid: {}".format(
            path, fid, anime, episode, lid))
        if not path and not fid and not (anime and episode) and not lid:
            raise AniDBError("File must be created with either filname, fid, lid or anime and episode.")

        self.nfs_obj = nfs_obj
        if path:
            self._path = path
            self._mtime, self._size = adbb.fileinfo.get_file_stats(
                self._path,
                self.nfs_obj)
            adbb.log.debug("Created File {} - size: {}, mtime: {}".format(self._path, self._size, self._mtime))
        if fid:
            self._fid = int(fid)
        if lid:
            self._lid = int(lid)
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
                self._multiep = [episode]
        self._get_db_data()

    def _get_db_data(self):
        sess = self._get_db_session()
        res = None
        if self._fid:
            res = sess.query(FileTable).filter_by(fid=self._fid).all()
        elif self._lid:
            res = sess.query(FileTable).filter_by(lid=self._lid).all()
        elif self._path:
            res = sess.query(FileTable).filter_by(path=self._path).all()
            if res and res[0].size != self._size:
                sess.delete(res[0])
                self._db_commit(sess)
                res = []
            if not res:
                res = sess.query(FileTable).filter_by(
                        size=self._size,
                        ed2khash=self.ed2khash).all()
        elif self._episode.eid:
            res = sess.query(FileTable).filter_by(
                aid=self._anime.aid,
                eid=self._episode.eid).all()
            if res and len(res) > 0:
                res = [x for x in res if x.lid]
        if res and len(res) > 0:
            if self._path != res[0].path:
                res[0].path = self._path
                sess.merge(res[0])
                self._db_commit(sess)
            self.db_data = res[0]
            adbb.log.debug("Found db_data for file: {}".format(self.db_data))
            self._is_generic = self.db_data.is_generic
        self._close_db_session(sess)

    def _anidb_file_data_callback(self, res):
        new = None
        update_mylist = False
        finfo = {}
        adbb.log.debug("Response from anidb about file {}".format(self))
        if res.rescode in ('340', '320'):
            adbb.log.debug('{} is not present in AniDB'.format(self))
            if not self.db_data:
                self._is_generic = True
                if self._anime:
                    anime, episodes = self._guess_anime_ep_from_file(aid=self._anime.aid)
                else:
                    anime, episodes = self._guess_anime_ep_from_file()
                if anime and episodes:
                    self._multiep = [e.episode_number for e in episodes]
                    self._anime = anime
                    self._episode = episodes[0]
                finfo['aid'] = anime.aid
                finfo['eid'] = episodes[0].eid
                finfo['is_generic'] = self._is_generic
        else: 
            finfo = res.datalines[0]
            state = None
            adbb.log.debug("{} is in anidb".format(self))

            # if this file previously was generic, the file has probably been
            # added to anidb. We should remove any generic file from mylist and
            # add this instead.
            if self.db_data and self.db_data.is_generic:
                update_mylist = True

            self._is_generic = False
            finfo['is_generic'] = self._is_generic
            if 'state' in finfo:
                state = int(finfo['state'])
                del finfo['state']
            
            adbb.log.debug("adding attrs to object")
            for attr, data in finfo.items():
                if attr in adbb.mapper.file_map_f_converters:
                    finfo[attr] = adbb.mapper.file_map_f_converters[attr](data)
                else:
                    finfo[attr] = data

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

        if self._path:
            finfo['path'] = self._path
            finfo['size'] = self._size
            finfo['ed2khash'] = self._ed2khash
            finfo['mtime'] = self._mtime

        if 'aid' not in finfo:
            finfo['aid'] = 0
        if 'eid' not in finfo:
            finfo['eid'] = 0
        if 'fid' in finfo:
            self._fid = finfo['fid']
        if 'lid' in finfo:
            self._lid = finfo['lid']
        if 'epno' in finfo:
            del finfo['epno']

        if update_mylist:
            finfo['mylist_state'] = self.db_data.mylist_state
            finfo['mylist_viewed'] = self.db_data.mylist_viewed
            finfo['mylist_viewdate'] = self.db_data.mylist_viewdate
            finfo['mylist_source'] = self.db_data.mylist_source
            finfo['mylist_other'] = self.db_data.mylist_other
            finfo['lid'] = None
            self.remove_from_mylist()

        adbb.log.debug("fetching a db session to update {}".format(self))
        sess = self._get_db_session()
        if self.db_data:
            self.db_data = sess.merge(self.db_data)
            adbb.log.debug('{}: update {}'.format(self, finfo))
            self.db_data.update(**finfo)
            self.db_data.updated = datetime.datetime.now(self._timezone)
        else:
            new = FileTable(**finfo)
            new.updated = datetime.datetime.now(self._timezone)
            sess.add(new)

        if new:
            self.db_data = new
        self._db_commit(sess)
        self._close_db_session(sess)
        self._file_updated.set()

        if update_mylist:
            self.update_mylist(
                    state = self.db_data.mylist_state,
                    watched = self.db_data.mylist_viewdate,
                    source = self.db_data.mylist_source,
                    other = self.db_data.mylist_other)

    def _anidb_mylist_data_callback(self, res):
        new = None
        if res.rescode == '312':
            self._mylist_updated.set()
            raise AniDBFileError("adbb currently does not support multiple mylist entries for a single episode")
        elif res.rescode == '321':
            self._mylist_updated.set()
            return
        else:
            finfo = res.datalines[0]
            if 'date' in finfo:
                del finfo['date']
            for attr, data in finfo.items():
                finfo[attr] = adbb.mapper.mylist_map_converters[attr](data)

        if 'mylist_viewdate' in finfo and finfo['mylist_viewdate']:
            finfo['mylist_viewed'] = True

        sess = self._get_db_session()
        if (self.db_data and self.db_data.is_generic and finfo['gid']) or \
                (self.db_data and not self.db_data.is_generic and finfo['fid'] != self.db_data.fid):
            if finfo['gid']:
                finfo['is_generic'] = False
            else:
                finfo['is_generic'] = True

            # there is something in mylist; but it's not us :/
            res = sess.query(FileTable).filter_by(lid=finfo['lid']).all()
            if not res:
                new = FileTable(**finfo)
                new.updated = datetime.datetime.now(self._timezone)
                sess.add(new)
            else:
                obj = res[0]
                obj.updated = datetime.datetime.now(self._timezone)
                obj.update(**finfo)
            self._db_commit(sess)
            self._close_db_session(sess)
            self._mylist_updated.set()
            return

        if self._path:
            finfo['path'] = self._path
            finfo['size'] = self._size
            finfo['ed2khash'] = self._ed2khash
            finfo['mtime'] = self._mtime


        if finfo['gid']:
            self._is_generic = False
        else:
            self._is_generic = True
        finfo['is_generic'] = self._is_generic
        if self.db_data:
            adbb.log.debug("New mylist info: {}".format(finfo))
            self.db_data = sess.merge(self.db_data)
            self.db_data.update(**finfo)
            self.db_data.updated = datetime.datetime.now(self._timezone)
        else:
            new = FileTable(**finfo)
            new.updated = datetime.datetime.now(self._timezone)
            adbb.log.debug("Adding mylist info: {}".format(finfo))
            sess.add(new)

        if new:
            self.db_data = new
        self._db_commit(sess)
        self._close_db_session(sess)
        self._mylist_updated.set()

    def _send_anidb_update_req(self, prio=False, req_mylist=False, req_file=True):
        adbb.log.debug("updating - fid: {}, size: {}, path: {}".format(
            self._fid,
            self._size,
            self._path))
        if req_file:
            if self._fid:
                self._file_updated.clear()
                adbb.log.debug("sending file request with fid")
                req = FileCommand(
                    fid=self._fid,
                    fmask=adbb.mapper.getFileBitsF(adbb.mapper.file_map_f),
                    amask=adbb.mapper.getFileBitsA(['epno'])
                )
                self._anidb_link.request(req, self._anidb_file_data_callback,
                                         prio=prio)
                self._file_updated.wait()
            elif self._size and self._path:
                self._file_updated.clear()
                adbb.log.debug("sending file request with size and hash")
                req = FileCommand(
                    size=self._size,
                    ed2k=self.ed2khash,
                    fmask=adbb.mapper.getFileBitsF(adbb.mapper.file_map_f),
                    amask=adbb.mapper.getFileBitsA(['epno']))
                self._anidb_link.request(req, self._anidb_file_data_callback,
                                         prio=prio)
                self._file_updated.wait()

        # We want to send a mylist request only if explicitly asked for, or if
        # we didn't get a fid from the File request
        if req_mylist or not self.db_data or not self.db_data.fid:
            if self._fid:
                adbb.log.debug("fetching mylist with fid")
                req = MyListCommand(fid=self._fid)
            elif self._lid:
                adbb.log.debug("fetching mylist with lid")
                req = MyListCommand(lid=self._lid)
            else:
                adbb.log.debug("fetching mylist with aid and epno")
                req = MyListCommand(
                    aid=self.anime.aid,
                    epno=self.episode.episode_number)
            adbb.log.debug("sending mylist request")
            self._anidb_link.request(req, self._anidb_mylist_data_callback,
                                     prio=prio)
            self._mylist_updated.wait()
        self._updating.release()

    def __repr__(self):
        return "File(path='{}', fid={}, anime={}, episode={})". \
            format(
                self._path,
                self._fid,
                self._anime,
                self._episode)

    def remove_from_mylist(self):
        wait = threading.Event()

        def _mylistdel_callback(res):
            if res.rescode == '211':
                adbb.log.info("File {} removed from mylist".format(self))
            elif res.rescode == '411':
                adbb.log.warning("File {} was not in mylist".format(self))
            wait.set()

        if self.db_data and self.db_data.fid:
            req = MyListDelCommand(fid=self.db_data.fid)
            self._anidb_link.request(req, _mylistdel_callback, prio=True)
        elif self.db_data and self.db_data.lid:
            req = MyListDelCommand(lid=self.db_data.lid)
            self._anidb_link.request(req, _mylistdel_callback, prio=True)
        elif self._is_generic:
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
        self._lid = None
        sess = self._get_db_session()
        finfo = {
            'mylist_state': None,
            'mylist_filestate': None,
            'mylist_viewed': None,
            'mylist_viewdate': None,
            'mylist_storage': None,
            'mylist_source': None,
            'mylist_other': None,
            'lid': None,
        }
        self.db_data = sess.merge(self.db_data)
        self.db_data.update(**finfo)
        self._db_commit(sess)
        self._close_db_session(sess)
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
        edit = False
        req = None

        def _mylistadd_callback(res):
            if res.rescode in ('320', '330', '350', '310', '322', '411'):
                adbb.log.warning("Could not add file {} to mylist, anidb says: {}".format(self, res.rescode))
            elif res.rescode in ('210', '310', '311'):
                adbb.log.info("File {} added to mylist".format(self))
                # if 'entrycnt' is > 1 this is actually the lid...
                # ... which is good I guess, because we want it.
                adbb.log.debug("lines from MYLISTADD command: {}".format(res.datalines))
                if 'entries' in res.datalines[0]:
                    res = int(res.datalines[0]['entries'])
                elif 'entrycnt' in res.datalines[0]:
                    res = int(res.datalines[0]['entrycnt'])
                if res > 1:
                    sess = self._get_db_session()
                    self.db_data = sess.merge(self.db_data)
                    self.db_data.update(lid=res)
                    self._db_commit(sess)
                    self._close_db_session(sess)
            wait.set()

        try:
            state_num = [x for x, y in adbb.mapper.mylist_state_map.items() if y == state][0]
        except IndexError:
            state_num = None

        if watched:
            if isinstance(watched, datetime.datetime):
                viewdate = int(watched.timestamp())
            viewed = 1
        elif watched is False:
            viewed = 0
        else:
            viewed = None

        # Make sure this episode isn't already in mylist
        if not self.lid:
            # avoid a lookup call if we have a file in our database
            sess = self._get_db_session()
            res = sess.query(FileTable).filter_by(eid=self.episode.eid).all()
            self._db_commit(sess)
            self._close_db_session(sess)
            mylist_entries = [x for x in res if x.lid]
            if mylist_entries:
                for entry in mylist_entries:
                    other_file = File(lid=entry.lid)
                    other_file.remove_from_mylist()
            else:
                # Nothing in local database; ask the API
                other_file = File(anime=self.anime, episode=self.episode)
                if other_file.lid:
                    other_file.remove_from_mylist()


        if self.lid:
            edit = True
            req = MyListAddCommand(
                lid=self.db_data.lid,
                edit=1,
                state=state_num,
                viewed=viewed,
                viewdate=viewdate,
                source=source,
                other=other)
        elif self.fid:
            req = MyListAddCommand(
                fid=self.fid,
                state=state_num,
                viewed=viewed,
                viewdate=viewdate,
                source=source,
                other=other)
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
        if edit:
            sess = self._get_db_session()
            self.db_data = sess.merge(self.db_data)
            if state:
                self.db_data.mylist_state = state
            if watched:
                self.db_data.mylist_viewed = True
                if isinstance(watched, datetime.datetime):
                    self.db_data.mylist_viewdate = watched
                else:
                    self.db_data.mylist_viewdate = datetime.datetime.now(self._timezone)
            if source:
                self.db_data.mylist_source = source
            if other:
                self.db_data.mylist_other = other
            self._db_commit(sess)
            self._close_db_session(sess)
        else:
            # Oh lord, another slowdown? 
            # Sorry, since anidb doesn't return our lid and eid when adding we
            # have to do another request here...
            locked = self._updating.acquire(False)
            if not locked:
                self._updating.acquire()
                self._updating.release()
                return
            self._send_anidb_update_req(req_file=False, req_mylist=True)

    def _guess_anime_ep_from_file(self, aid=None):
        if not self.path:
            return (None, None)
        head, filename = os.path.split(self.path)
        head, parent_dir = os.path.split(head)

        # try to figure out which episodes this file contains
        # episodes = self._guess_epno_from_filename(filename)
        # if not episodes:
        #    return (None, None)
        # if aid:
        #    return (aid, episodes)

        if not aid:
            # first try to figure out anime by the directory name
            if parent_dir and self.parse_dir:
                series = adbb.anames.get_titles(name=parent_dir)
                if series:
                    aid = series[0][0]
                    adbb.log.debug("dir '{}': score {} for '{}'".format(
                        parent_dir, series[0][2], series[0][3]))
                else:
                    adbb.log.debug("dir '{}': no match".format(parent_dir))

            # no confident hit on parent directory, trying filename
            if not aid:
                # strip away all kinds of paranthesis like
                # [<group>], (<codec>) or {<crc>}.
                stripped = re.sub(r'[{[(][^\]})]*?[})\]]', '', filename)
                # Remove episode numbers
                stripped = re.sub(r'-[ _]?\d+(-\d+)?', '', stripped)
                stripped = re.sub(r'EP?(isode)?[ _]?\d+(-\d+)?', '', stripped, re.I)
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
                    adbb.log.debug(
                        "file '{}': trimmed to '{}', score {} for '{}'".format(filename, joined, series[0][2],
                                                                               series[0][3]))
                    aid = series[0][0]
                else:
                    adbb.log.debug("file '{}': trimmed to '{}', no match".format(filename, joined))
            if not aid:
                return (None, None)

        anime = Anime(aid)
        episodes = self._guess_epno_from_filename(filename, anime)

        return (anime, episodes)

    def _search_filename(self, filename, regex, anime):
        ret = []
        res = regex.search(filename)
        if res:
            eps = adbb.fileinfo.multiep_re.findall(res.group(3))
            eps.insert(0, res.group(2))
            for m in eps:
                try:
                    ep = int(m)
                except ValueError:
                    adbb.log.warning("Got non-numeric episode number when searching '{}' with regex '{}'".format(
                        filename, regex))
                    continue
                if res.group(1).lower() in ('s', "0", "00"):
                    ret.append("S{}".format(ep))
                elif res.group(1).lower() == 'o':
                    ret.append("C{}".format(ep))
                elif res.group(1).lower() == 'e':
                    # This is error prone, but we're guessing that endings
                    # starts at half the credits count...
                    count = anime.credit_count
                    if count:
                        start = int(count / 2)
                        ep = start + ep
                    ret.append("C{}".format(ep))
                elif res.group(1).lower() in ('t', 'pv'):
                    ret.append("T{}".format(ep))
                else:
                    ret.append(str(ep))
            adbb.log.debug("file '{}': looks like episode(s) {}".format(
                filename, ret))
        return ret

    def _guess_epno_from_filename(self, filename, anime):
        count = 1
        ret = None
        for r in adbb.fileinfo.ep_nr_re:
            # abort when we reach the fallback regex; represented by a None
            # entry in the array
            if not r:
                break
            count += 1
            ret = self._search_filename(filename, r, anime)
            if ret:
                break
        if not ret:
            if self.force_single_episode_series:
                # We assume that this file belongs to an anime with just a
                # single episode
                return [Episode(anime=anime, epno=1)]
            else:
                # if this series/movie/ova only has one regular episode, we claim
                # this is it.
                if anime.nr_of_episodes == 1:
                    return [Episode(anime=anime, epno=1)]

            # multi episode series, but the regular regexp gave nothing, try
            # the fallbacks
            for r in adbb.fileinfo.ep_nr_re[count:]:
                ret = self._search_filename(filename, adbb.fileinfo.ep_nr_re[-1], anime)
                if ret:
                    break
            if not ret:
                adbb.log.debug("file '{}': could not figure out episode number(s)".format(filename))
                return []
        if len(ret) == 2:
            try:
                mi = int(ret[0])
                ma = int(ret[1])
                ret = [ str(x) for x in range(mi, ma+1) ]
            except ValueError:
                pass
        return [Episode(anime=anime, epno=e) for e in ret]

    def __eq__(self, other):
        if not isinstance(other, File):
            return NotImplemented
        if self.fid and self.fid == other.fid:
            return True
        if self._is_generic and other.is_generic:
            return self.episode == other.episode

    def __len__(self):
        return len(self.multiep)

    def __contains__(self, other):
        if not isinstance(other, Episode):
            return NotImplemented
        return other.episode_number in self.multiep

class Group(AniDBObj):
    _gid = None
    _name = None

    def __init__(self, name=None, gid=None):
        super(Group, self).__init__()
        if not (name or gid):
            raise IllegalAnimeObject("At least name or gid must be given when creating a Group object")

        if gid:
            self._gid = gid
        if name:
            self._name = name
        self.db_data = None
        self._get_db_data()

    def _anidb_data_callback(self, res):
        sess = self._get_db_session()
        if res.rescode == "350":
            if self.db_data:
                sess.delete(self.db_data)
            if self._name:
                new = GroupTable(
                        name=self._name, 
                        short=self._name,
                        updated = datetime.datetime.now(self._timezone)
                        )
                sess.add(new)
        else:
            ginfo = res.datalines[0]
            for attr, data in ginfo.items():
                if attr == 'relations':
                    relations = data.split("'")
                    relations = [
                            GroupRelationTable(
                                related_gid = x.split(',')[0],
                                relation_type = adbb.mapper.group_relation_map[x.split(',')[1]])
                            for x in relations if ',' in x]
                    ginfo['relations'] = relations
                elif attr in adbb.mapper.group_map_converters:
                    ginfo[attr] = adbb.mapper.group_map_converters[attr](data)

        sess = self._get_db_session()
        if self.db_data:
            self.db_data = sess.merge(self.db_data)
            self.db_data.update(**ginfo)
            self.db_data.updated = datetime.datetime.now(self._timezone)
            new_relations = []
            for r in ginfo['relations']:
                found = False
                for sr in self.db_data.relations:
                    if r.related_gid == sr.related_gid:
                        found = True
                        sr.relation_type = r.relation_type
                        sr.group_pk = self.db_data.pk
                        new_relations.append(sr)
                if not found:
                    r.group_pk = self.db_data.pk
                    new_relations.append(r)
            for r in self.db_data.relations:
                if r not in new_relations:
                    sess.delete(r)
            self.db_data.relations = new_relations
        else:
            new = GroupTable(**ginfo)
            new.updated = datetime.datetime.now(self._timezone)
            sess.add(new)
            self.db_data = new

        self._db_commit(sess)
        self._close_db_session(sess)
        self._updated.set()
                
    def _get_db_data(self):
        sess = self._get_db_session()
        if self._gid:
            res = sess.query(GroupTable).filter_by(gid=self._gid).all()
        else:
            res = sess.query(GroupTable).filter(sqlalchemy.or_(
                GroupTable.name.ilike(self._name), 
                GroupTable.short.ilike(self._name))).all()
        if len(res) > 0:
            self.db_data = res[0]
            adbb.log.debug("Found db_data for group: {}".format(self.db_data))
        self._close_db_session(sess)

    def _send_anidb_update_req(self, prio=False):
        self._updated.clear()
        if self._gid:
            req = GroupCommand(gid=self._gid)
        else:
            req = GroupCommand(gname=self._name)
        self._anidb_link.request(req, self._anidb_data_callback, prio=prio)
        self._updated.wait()
        self._updating.release()

    def __repr__(self):
        return "Group(gid='{}', name={})". \
            format(
                self._gid,
                self._name)
