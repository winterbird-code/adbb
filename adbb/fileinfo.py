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

from __future__ import with_statement
import datetime
import difflib
import functools
import re
import os
import xml.etree.cElementTree as etree

from Crypto.Hash import MD4

try:
    import libnfs
except ImportError:
    libnfs = None

import adbb.errors

ep_nr_re = [
    re.compile(r'[Ss]([0-9]+)[ ._-]*e([0-9]+)([0-9-]*)', re.I),  # foo.s01.e01, foo.s01_e01, S01E02 foo, S01 - E02
    re.compile(r'[\._ -]()ep_?([0-9]+)([0-9-]*)', re.I),  # foo.ep01, foo.EP_01
    re.compile(r'[\\/\._ \[\(-]([0-9]{1,2})x([0-9]+)([0-9-]*)', re.I),  # foo.1x09* or just /1x09*
    re.compile(r'[/\._ \-](s)p(?:ecials?)?[._ \-]{0,3}([0-9]{1,3})([0-9-]*)', re.I),  # specials
    re.compile(r'[/\._ \-]{2}()([0-9]{1,4})([0-9-]*)', re.I),  # match '- nr' '-_nr' etc.
    re.compile(r'[/\._ \-](s)[\._ \-]{0,3}([0-9]{1,3})([0-9-]*)', re.I),  # specials

    None,  # the following regex are fallbacks and shouldn't be run if the
    # anime only has one episode. This None marks the breakpoint
    re.compile(r'[/\._ \-](s)p?(?:ecials?)?[\._ \-]{1,3}([0-9]{0,3})([0-9-]*)', re.I),
    # specials that may not have number
    re.compile(r'[/\._ \-](?:nc)?(o)p?(?:enings?)?[\._ \-]{0,3}([0-9]{0,3})([0-9-]*)', re.I),  # openings
    re.compile(r'[/\._ \-](?:nc)?(e)d?(?:ndings?)?[\._ \-]{0,3}([0-9]{0,3})([0-9-]*)', re.I),  # endings
    re.compile(r'[/\._ \-](t|pv)(?:railers?)?[\._ \-]{0,3}([0-9]{0,3})([0-9-]*)', re.I),  # trailers
    # "others"-type not implemented for now...
    re.compile(r'[/\._ \-]()([0-9]{1,4})([0-9-]*)', re.I)  # if everything else fails, just match the first number(s)
]
partfile_re = re.compile(r'[/\._ \-](p)(?:ar)t[/\._ \-]{0,3}([0-9ivx]+)', re.I)  # part-file, not complete episode/movie
multiep_re = re.compile(r'[0-9]+')
specials_re = re.compile(r'^(S|P|C|T|O)([0-9]+)$', re.I)


# http://www.radicand.org/blog/orz/2010/2/21/edonkey2000-hash-in-python/
def get_file_hash(path, nfs_obj=None):
    if path.startswith('nfs://'):
        with NFSFile(path, 'rb', nfs_obj) as f:
            return _calculate_ed2khash(f)
    with open(path, 'rb') as f:
        return _calculate_ed2khash(f)


def _calculate_ed2khash(fileObj):
    """ Returns the ed2k hash of a given file."""
    def gen(f):
        while True:
            x = f.read(9728000)
            if x:
                yield x
            else:
                return

    def md4_hash(data):
        m = MD4.new()
        m.update(data)
        return m

    a = gen(fileObj)
    hashes = [md4_hash(data) for data in a]
    if len(hashes) == 1:
        return hashes[0].hexdigest()
    else:
        # reduce goes from left to right, but will not run digest on the first
        # entry, so we'll have to do that first.
        hashes[0] = hashes[0].digest()
        res = md4_hash(functools.reduce(lambda a, d: a + d.digest(), hashes)).hexdigest()
        return res


def get_file_stats(path, nfs_obj=None):
    """Return (mtime, size). size is in bytes, mtime is a datetime object."""
    if path.startswith('nfs://'):
        return _nfs_stats(path, nfs_obj)

    stat = os.stat(path)

    size = stat.st_size
    mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
    return (mtime, size)


class NFSFile(object):
    def __init__(self, path, mode, nfs_obj=None):
        if not libnfs:
            raise adbb.errors.AniDBPathError(
                "libnfs python module not installed, can't use nfs paths")
        self.mode = mode
        self.handle = None
        self.nfs_obj = nfs_obj

        self.path = path

        if self.nfs_obj:
            if path.startswith(nfs_obj.url):
                self.rel_path = os.path.join('/', path[len(nfs_obj.url):])
            else:
                self.rel_path = self.path

    def open(self):
        if self.nfs_obj:
            self.handle = self.nfs_obj.open(self.rel_path, self.mode)
        else:
            self.handle = libnfs.open(self.path, self.mode)
        return self.handle

    def close(self):
        if self.handle:
            self.handle.close()

    def __enter__(self):
        return self.open()

    def __exit__(self, type, value, traceback):
        self.close()


def _nfs_stats(path, nfs_obj=None):
    stats = None
    with NFSFile(path, 'r', nfs_obj) as f:
        stats = f.fstat()

    mtime = stats['mtime']['sec'] + stats['mtime']['nsec'] / 10 ** 9
    mtime = datetime.datetime.fromtimestamp(mtime)
    return (mtime, stats['size'])
