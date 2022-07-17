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

import adbb.db
from adbb.link import AniDBLink

from adbb.animeobjs import Anime, AnimeTitle, Episode, File, Group

from adbb.anames import get_titles

anidb_client_name = "adbb"
anidb_client_version = 4
anidb_api_version = 3

log = None
_anidb = None
_sessionmaker = None


def init(
        sql_db_url,
        api_user=None,
        api_pass=None,
        debug=False,
        loglevel='info',
        logger=None,
        netrc_file=None,
        outgoing_udp_port=random.randrange(9000, 10000)):

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

    global log, _anidb, _sessionmaker
    log = logger

    try:
        nrc = netrc.netrc(netrc_file)
    except FileNotFoundError:
        nrc = None

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
    _sessionmaker = adbb.db.init_db(sql_db_url)

    # unless both username and password is given; look for credentials in netrc
    if not (api_user and api_pass):
        if not nrc:
            raise Exception("User and passwords are required if no netrc file exists")
        for host in ['api.anidb.net', 'api.anidb.info', 'anidb.net']:
            try:
                username, _account, password = nrc.authenticators(host)
            except TypeError:
                pass
            if username and password:
                api_user = username
                api_pass = password
                break

    _anidb = adbb.link.AniDBLink(
        api_user,
        api_pass,
        myport=outgoing_udp_port)


def get_session():
    return _sessionmaker()


def close_session(session):
    session.close()


def close():
    global _anidb
    _anidb.stop()
