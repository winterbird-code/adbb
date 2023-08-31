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

import os
import multiprocessing
import netrc
import logging
import logging.handlers
import sys
import random
import urllib.parse
import urllib.request

import adbb.db
import adbb.errors
from adbb.link import AniDBLink

from adbb.animeobjs import Anime, AnimeTitle, Episode, File, Group

from adbb.anames import get_titles, update_animetitles, update_anilist

anidb_client_name = "adbb"
anidb_client_version = 10
anidb_api_version = 3

log = None
_anidb = None
_sessionmaker = None
fanart_key = None

def init(
        sql_db_url,
        api_user=None,
        api_pass=None,
        debug=False,
        loglevel='info',
        logger=None,
        netrc_file=None,
        outgoing_udp_port=random.randrange(9000, 10000),
        api_key=None,
        fanart_api_key=None,
        db_only=False):

    if logger is None:
        logger = logging.getLogger(__name__)
        logger.setLevel(loglevel.upper())
        if debug:
            logger.setLevel(logging.DEBUG)
            lh = logging.StreamHandler()
            lh.setFormatter(logging.Formatter(
                '%(asctime)s %(levelname)s %(filename)s:%(lineno)d - %(message)s'))
            logger.addHandler(lh)
        if os.path.exists('/dev/log'):
            lh = logging.handlers.SysLogHandler(address='/dev/log')
        else:
            lh = logging.handlers.SysLogHandler()
        lh.setFormatter(logging.Formatter(
            'adbb %(filename)s/%(funcName)s:%(lineno)d - %(message)s'))
        logger.addHandler(lh)

    global log, _anidb, _sessionmaker, fanart_key
    log = logger
    fanart_key = fanart_api_key

    try:
        nrc = netrc.netrc(netrc_file)
    except FileNotFoundError:
        nrc = None

    # unless both username and password is given; look for credentials in netrc
    if not (api_user and api_pass) or db_only:
        if not nrc:
            raise Exception("User and passwords are required if no netrc file exists")
        for host in ['api.anidb.net', 'api.anidb.info', 'anidb.net']:
            try:
                username, account, password = nrc.authenticators(host)
            except TypeError:
                continue
            if username and password:
                api_user = username
                api_pass = password
                if account and not api_key:
                    api_key = account
                break

    if not db_only:
        _anidb = adbb.link.AniDBLink(
            api_user,
            api_pass,
            myport=outgoing_udp_port,
            api_key=api_key)

    if nrc:
        # if no password is given in sql-url we try to look it up
        # in netrc
        parts=sql_db_url.split('/')
        if parts[2] and not ':' in parts[2]:
            if '@' in parts[2]:
                username, host = parts[2].split('@')
            else:
                username, host = (None, parts[2])
            try:
                u, _account, password = nrc.authenticators(host)
            except TypeError:
                u, password = (None, None)
            if password:
                if not username:
                    username = u
                if username == u:
                    parts[2] = f'{username}:{password}@{host}'
        sql_db_url='/'.join(parts)
        
        if not fanart_key:
            for host in ['fanart.tv', 'assets.fanart.tv', 'webservice.fanart.tv', 'api.fanart.tv']:
                try:
                    username, account, password = nrc.authenticators(host)
                except TypeError:
                    continue
                key = [x for x in [account, password] if x]
                if not key:
                    continue
                log.debug('Fanart key found in netrc')
                fanart_key = key[0]

            

    _sessionmaker = adbb.db.init_db(sql_db_url)


def get_session():
    return _sessionmaker()


def close_session(session):
    session.close()

def download_image(filehandle, obj):
    if type(obj) not in (Anime, Group):
        raise adbb.errors.AniDBMissingImage(f'Object type {type(obj)} does not support images')
    if not obj.picname:
        raise adbb.errors.AniDBMissingImage(f'{obj} does not have a picture defined')
    url_base = 'https://cdn.anidb.net/images/main'
    url=f'{url_base}/{obj.picname}'
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as f:
        filehandle.write(f.read())



def download_fanart(filehandle, url, preview=False):
    if not fanart_key:
        raise adbb.errors.FanartError('No fanart key available')
    my_url = urllib.parse.urlparse(url)
    if preview:
        my_url = urllib.parse.urlparse(url)._replace(
                scheme='https',
                path=urllib.parse.quote(my_url.path.replace('/fanart/', '/preview/')))
    else:
        my_url = urllib.parse.urlparse(url)._replace(scheme='https', path=urllib.parse.quote(my_url.path))

    req = urllib.request.Request(my_url.geturl(), headers={'api-key': fanart_key})
    with urllib.request.urlopen(req) as f:
        filehandle.write(f.read())

def close():
    global _anidb
    if _anidb:
        _anidb.stop()
