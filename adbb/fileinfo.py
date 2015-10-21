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
import difflib
import functools
import re
import hashlib
import os
import xml.etree.cElementTree as etree

ep_nr_re = [
        re.compile(r'[Ss]([0-9]+)[ ._-]*e([0-9]+)()', re.I), #foo.s01.e01, foo.s01_e01, S01E02 foo, S01 - E02
        re.compile(r'[\._ -]()ep_?([0-9]+)()', re.I), # foo.ep01, foo.EP_01
        re.compile(r'[\\/\._ \[\(-]([0-9]{1,2})x([0-9]+)()', re.I), # foo.1x09* or just /1x09*
        re.compile(r'[/\._ \-](s)p?(?:pecials?)?[\._ \-]{0,3}([0-9]{1,2})([._ 0-9-]*)', re.I), # specials
        re.compile(r'[/\._ \-]{2}()([0-9]{1,4})([._ 0-9-]*)', re.I), # match '- nr' '-_nr' etc.
        re.compile(r'[/\._ \-]()([0-9]{1,4})([._ 0-9-]*)', re.I) # if everything else fails, just match the first number(s)
]
multiep_re = re.compile(r'[0-9]+')


# http://www.radicand.org/blog/orz/2010/2/21/edonkey2000-hash-in-python/
def get_file_hash(filePath):
    """ Returns the ed2k hash of a given file."""
    if not filePath:
        return None
    md4 = hashlib.new('md4').copy

    def gen(f):
        while True:
            x = f.read(9728000)
            if x: yield x
            else: return

    def md4_hash(data):
        m = md4()
        m.update(data)
        return m

    with open(filePath, 'rb') as f:
        a = gen(f)
        hashes = [md4_hash(data).digest() for data in a]
        if len(hashes) == 1:
            return hashes[0].encode("hex")
        else: return md4_hash(functools.reduce(lambda a,d: a + d, hashes)).hexdigest()
        
        
def get_file_size(path):
    size = os.path.getsize(path)
    return size
