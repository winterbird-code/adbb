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
from random import shuffle
import datetime

_blacklist = ('unused', 'retired', 'reserved', 'not_implemented')

# each line is one byte
# only chnage this if the api changes
anime_map_a_converters = {
    'aid': int,
    'nr_of_episodes': int,
    'highest_episode_number': int,
    'special_ep_count': int,
    'air_date': lambda x: datetime.date(1970, 1, 1) + datetime.timedelta(seconds=int(x)) if x and int(x) else None,
    'end_date': lambda x: datetime.date(1970, 1, 1) + datetime.timedelta(seconds=int(x)) if x and int(x) else None,
    'rating': lambda x: int(x) / 100 if x else None,
    'vote_count': int,
    'temp_rating': lambda x: int(x) / 100 if x else None,
    'temp_vote_count': int,
    'average_review_rating': lambda x: int(x) / 100 if x else None,
    'review_count': int,
    'is_18_restricted': lambda x: x == "1",
    'ann_id': int,
    'allcinema_id': int,
    'anidb_updated': lambda x: datetime.datetime.fromtimestamp(int(x)) if x and int(x) else None,
    'special_count': int,
    'credit_count': int,
    'other_count': int,
    'trailer_count': int,
    'parody_count': int
}

mylist_state_map = {
    '0': 'unknown',
    '1': 'on hdd',
    '2': 'on cd',
    '3': 'deleted'
}

mylist_filestate_map = {
    '0': 'normal/original',
    '1': 'corrupted version/invalid crc',
    '2': 'self edited',
    '10': 'self ripped',
    '11': 'on dvd',
    '12': 'on vhs',
    '13': 'on tv',
    '14': 'in theaters',
    '15': 'streamed',
    '100': 'other'
}

file_map_f_converters = {
    'fid': int,
    'aid': int,
    'eid': int,
    'lid': int,
    'gid': int,
    'is_deprecated': lambda x: x == '1',
    'size': int,
    'ed2khash': lambda x: x or None,
    'length_in_seconds': int,
    'description': lambda x: x or None,
    'aired_date': lambda x: datetime.date(1970, 1, 1) + datetime.timedelta(seconds=int(x)) if x and int(x) else None,
    'mylist_state': lambda x: mylist_state_map[x] if x else None,
    'mylist_filestate': lambda x: mylist_filestate_map[x] if x else None,
    'mylist_viewed': lambda x: x == '1',
    'mylist_viewdate': lambda x: datetime.datetime.fromtimestamp(int(x)) if x and int(x) else None,
    'mylist_storage': lambda x: x or None,
    'mylist_source': lambda x: x or None,
    'mylist_other': lambda x: x or None
}

episode_type_map = {
    '1': 'regular',
    '2': 'special',
    '3': 'credit',
    '4': 'trailer',
    '5': 'parody',
    '6': 'other'
}

episode_map_converters = {
    'eid': int,
    'aid': int,
    'length': int,
    'rating': lambda x: int(x) / 100 if x else None,
    'votes': int,
    'aired': lambda x: datetime.date(1970, 1, 1) + datetime.timedelta(seconds=int(x)) if x and int(x) else None,
    'type': lambda x: episode_type_map[x] if x else None,
}

mylist_map_converters = {
    'lid': int,
    'fid': int,
    'eid': int,
    'aid': int,
    'gid': int,
    'mylist_state': lambda x: mylist_state_map[x] if x else None,
    'mylist_viewdate': lambda x: datetime.datetime.fromtimestamp(int(x)) if x and int(x) else None,
    'mylist_storage': lambda x: x or None,
    'mylist_source': lambda x: x or None,
    'mylist_other': lambda x: x or None
}

anime_relation_map = {
    "1": "sequel",
    "2": "prequel",
    "11": "same setting",
    "12": "alternative setting",
    "21": "alternative setting",
    "22": "alternative setting",
    "31": "alternative version",
    "32": "alternative version",
    "41": "music video",
    "42": "character",
    "51": "side story",
    "52": "parent story",
    "61": "summary",
    "62": "full story",
    "100": "other"
}

group_map_converters = {
    'gid': int,
    'rating': int,
    'votes': int,
    'acount': int,
    'fcount': int,
    'founded': lambda x: datetime.datetime.fromtimestamp(int(x)) if x and int(x) else None,
    'disbanded': lambda x: datetime.datetime.fromtimestamp(int(x)) if x and int(x) else None,
    'dateflags': int,
    'last_release': lambda x: datetime.datetime.fromtimestamp(int(x)) if x and int(x) else None,
    'last_activity': lambda x: datetime.datetime.fromtimestamp(int(x)) if x and int(x) else None,
}

group_relation_map = {
    '1': 'participant in',
    '2': 'parent of',
    '3': 'lost part',
    '4': 'merged from',
    '5': 'now known as',
    '6': 'other',
    '101': 'includes',
    '102': 'child of',
    '103': 'split from',
    '104': 'merged into',
    '105': 'formerly',
    '106': 'other'
}

roman_numbering = {
    'i': 1,
    'ii': 2,
    'iii': 3,
    'iv': 4,
    'v': 5,
    'vi': 6,
    'vii': 7,
    'viii': 8,
    'ix': 9,
    'x': 10,
    'xi': 11,
    'xii': 12,
    'xiii': 13,
    'xiv': 14,
    'xv': 15,
    'xvi': 16,
    'xvii': 17,
    'xviii': 18,
    'xix': 19,
    'xx': 20,
    'xxi': 21,
    'xxii': 22,
    'xxiii': 23,
    'xxiv': 24,
    'xxv': 25,
    'xxvi': 26,
    'xxvii': 27,
    'xxviii': 28,
    'xxix': 29,
    'xxx': 30,
    # If you got this far you should really consider the regular numbering
    # system..
}

anime_map_a = ['aid', 'unused', 'year', 'type', 'related_aid_list', 'related_aid_type', 'retired', 'retired',
               'not_implemented', 'not_implemented', 'not_implemented', 'not_implemented', 'not_implemented',
               'not_implemented', 'retired', 'retired',
               'nr_of_episodes', 'highest_episode_number', 'special_ep_count', 'air_date', 'end_date', 'url', 'picname',
               'retired',
               'rating', 'vote_count', 'temp_rating', 'temp_vote_count', 'average_review_rating', 'review_count',
               'not_implemented', 'is_18_restricted',
               'retired', 'ann_id', 'allcinema_id', 'animenfo_id', 'unused', 'unused', 'unused', 'anidb_updated',
               'not_implemented', 'not_implemented', 'not_implemented', 'not_implemented', 'unused', 'unused', 'unused',
               'unused',
               'special_count', 'credit_count', 'other_count', 'trailer_count', 'parody_count', 'unused', 'unused',
               'unused']

file_map_f = ['unused', 'aid', 'eid', 'gid', 'lid', 'not_implemented', 'is_deprecated', 'state',
              'size', 'ed2khash', 'not_implemented', 'not_implemented', 'not_implemented', 'unused', 'unused',
              'reserved',
              'not_implemented', 'not_implemented', 'not_implemented', 'not_implemented', 'not_implemented',
              'not_implemented', 'not_implemented', 'not_implemented',
              'not_implemented', 'not_implemented', 'length_in_seconds', 'description', 'aired_date', 'unused',
              'unused', 'not_implemented',
              'mylist_state', 'mylist_filestate', 'mylist_viewed', 'mylist_viewdate', 'mylist_storage', 'mylist_source',
              'mylist_other', 'unused']
# 'not_implemented','not_implemented','not_implemented','not_implemented','not_implemented','not_implemented','not_implemented','unused']

file_map_a = ['anime_total_episodes', 'highest_episode_number', 'year', 'type', 'related_aid_list', 'related_aid_type',
              'category_list', 'reserved',
              'romaji_name', 'kanji_name', 'english_name', 'other_name', 'short_name_list', 'synonym_list', 'retired',
              'retired',
              'epno', 'ep_name', 'ep_romaji_name', 'ep_kanji_name', 'episode_rating', 'episode_vote_count', 'unused',
              'unused',
              'group_name', 'group_short_name', 'unused', 'unused', 'unused', 'unused', 'unused',
              'date_aid_record_updated']

def getAnimeBitsA(amask):
    bitmap = anime_map_a
    return _getBitChain(bitmap, amask)


def getAnimeCodesA(aBitChain):
    amap = anime_map_a
    return _getCodes(amap, aBitChain)


def getFileBitsF(fmask):
    fmap = file_map_f
    return _getBitChain(fmap, fmask)


def getFileCodesF(bitChainF):
    fmap = file_map_f
    return _getCodes(fmap, bitChainF)


def getFileBitsA(amask):
    amap = file_map_a
    return _getBitChain(amap, amask)


def getFileCodesA(bitChainA):
    amap = file_map_a
    return _getCodes(amap, bitChainA)


def _getBitChain(attrmap, wanted):
    """Return an hex string with the correct bit set corresponding to the wanted fields in the map
    """
    bit = 0
    for index, field in enumerate(attrmap):
        if field in wanted and not field in _blacklist:
            bit = bit ^ (1 << len(attrmap) - index - 1)

    bit = str(hex(bit)).lstrip("0x").rstrip("L")
    bit = ''.join(["0" for unused in range(int(len(attrmap) / 4) - len(bit))]) + bit
    return bit


def _getCodes(attrmap, bitChain):
    """Returns a list with the corresponding fields as set in the bitChain (hex string)
    """
    codeList = []
    bitChain = int(bitChain, 16)
    mapLength = len(attrmap)
    for i in reversed(range(mapLength)):
        if bitChain & (2 ** i):
            codeList.append(attrmap[mapLength - i - 1])
    return codeList
