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

import multiprocessing
import logging
import logging.handlers
import sys

import adbb.db
from adbb.link import AniDBLink

from adbb.animeobjs import Anime, AnimeTitle, Episode, File

from adbb.anames import get_titles

anidb_client_name = "adbb"
anidb_client_version = 2
anidb_api_version = 3

_log = None
_anidb = None
_sql_db = None


def init(
        anidb_user, 
        anidb_pwd, 
        sql_db_url, 
        debug=False,
        loglevel='info',
        outgoing_udp_port=9876):

    logger = logging.getLogger(__name__)
    logger.setLevel(loglevel.upper())
    if debug:
        logger.setLevel(logging.DEBUG)
        lh = logging.StreamHandler()
        lh.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s %(filename)s:%(lineno)d - %(message)s'))
        logger.addHandler(lh)

    lh = logging.handlers.SysLogHandler(address='/dev/log')
    lh.setFormatter(logging.Formatter(
        'adbb %(filename)s/%(funcName)s:%(lineno)d - %(message)s'))
    logger.addHandler(lh)

    global _log, _anidb, _sql_db
    _log = logger
    _sql_db = adbb.db.init_db(sql_db_url)
    _anidb = adbb.link.AniDBLink(
            anidb_user, 
            anidb_pwd, 
            myport=outgoing_udp_port)


def close():
    global _anidb, _sql_db
    _sql_db.close()
    _anidb.stop()

