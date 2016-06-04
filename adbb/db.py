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


from sqlalchemy import *
from sqlalchemy.orm import *
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

def init_db(url):
    engine = create_engine(url, pool_recycle=300)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    return Session

class AnimeTable(Base):
    __tablename__ = 'anime'

    pk = Column(BigInteger, primary_key=True)
    aid = Column(BigInteger, nullable=False, unique=True)
    # TODO dateflags?
    year = Column(String(16), nullable=False)
    type = Column(String(16), nullable=False)

    nr_of_episodes = Column(Integer, nullable=False)
    highest_episode_number = Column(Integer, nullable=False)
    special_ep_count = Column(Integer, nullable=False)
    air_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    url = Column(String(512), nullable=True)
    picname = Column(String(128), nullable=True)

    rating = Column(Float, nullable=True)
    vote_count = Column(Integer, nullable=False)
    temp_rating = Column(Float, nullable=True)
    temp_vote_count = Column(Integer, nullable=False)
    average_review_rating = Column(Float, nullable=True)
    review_count = Column(Integer, nullable=False)
    is_18_restricted = Column(Boolean, nullable=False)

    ann_id = Column(BigInteger, nullable=True)
    allcinema_id = Column(BigInteger, nullable=True)
    animenfo_id = Column(String(64), nullable=True)
    anidb_updated = Column(DateTime(timezone=False), nullable=False)

    special_count = Column(Integer, nullable=False)
    credit_count = Column(Integer, nullable=False)
    other_count = Column(Integer, nullable=False)
    trailer_count = Column(Integer, nullable=False)
    parody_count = Column(Integer, nullable=False)

    # TODO: ANIMEDESC
    # description = Column(Unicode(8194), nullable=True)

    updated = Column(DateTime(timezone=True), nullable=False)

    relations = relationship("AnimeRelationTable", backref='anime')

    def update(self, **kwargs):
        for key, attr in kwargs.items():
            setattr(self, key, attr)

    def __repr__(self):
        return '<AnimeTable(pk={pk}, aid={aid}, episodes={episodes}, '\
                'highest_episode_number={highest_ep}, updated='\
                '{updated})>'.format(
                pk=self.pk,
                aid=self.aid,
                episodes=self.nr_of_episodes,
                highest_ep=self.highest_episode_number,
                updated=self.updated)

class AnimeRelationTable(Base):
    __tablename__ = 'anime_relation'

    pk = Column(BigInteger, primary_key=True)
    anime_pk = Column(BigInteger, ForeignKey('anime.pk'), nullable=False)
    related_aid = Column(BigInteger, nullable=False)
    relation_type = Column(
            Enum(
                'sequel',
                'prequel',
                'same setting',
                'alternative setting',
                'alternative version',
                'music video',
                'character',
                'side story',
                'parent story',
                'summary',
                'full story',
                'other'),
            nullable=False)

    def __cmp__(self, other):
        return (
                self.anime_pk == other.anime_pk \
                and self.related_aid == other.related_aid\
                and self.relation_type == other.relation_type)

    def __repr__(self):
        return '<AnimeRelationTable(pk={pk}, anime_pk={anime}, related_aid={related}, '\
                'type={type})>'.format(
                    pk=self.pk,
                    anime=self.anime_pk,
                    related=self.related_aid,
                    type=self.relation_type)

class EpisodeTable(Base):
    __tablename__ = 'episode'

    pk = Column(BigInteger, primary_key=True)
    aid = Column(BigInteger, nullable=False, index=True)
    eid = Column(BigInteger, nullable=False, unique=True, index=True)
    length = Column(Integer, nullable=False)
    rating = Column(Float, nullable=True)
    votes = Column(Integer, nullable=False)
    epno = Column(String(8), nullable=False)
    title_eng = Column(String(256), nullable=True)
    title_romaji = Column(String(256), nullable=True)
    title_kanji = Column(Unicode(256), nullable=True)
    aired = Column(Date(), nullable=True)
    type = Column(
            Enum(
                'regular',
                'special',
                'credit',
                'trailer',
                'parody',
                'other'),
            nullable=False)
    
    updated = Column(DateTime(timezone=True), nullable=False)

    def update(self, **kwargs):
        for key, attr in kwargs.items():
            setattr(self, key, attr)

    def __repr__(self):
        return '<EpisodeTable(pk={pk}, aid={aid}, epno={epno}, '\
                'title_eng={eng}, updated={updated})>'.\
                format(
                    pk=self.pk,
                    aid=self.aid,
                    epno=self.epno,
                    eng=self.title_eng,
                    updated=self.updated)


class FileTable(Base):
    __tablename__ = 'file'

    pk = Column(BigInteger, primary_key=True)
    path = Column(Unicode(512), nullable=True)
    size = Column(BigInteger, nullable=True)
    ed2khash = Column(String(64), nullable=True)
    mtime = Column(DateTime(timezone=False), nullable=True)
    aid = Column(BigInteger, nullable=False, index=True)
    gid = Column(BigInteger, nullable=True)
    eid = Column(BigInteger, nullable=False, index=True)
    fid = Column(BigInteger, nullable=True, index=True)
    is_deprecated = Column(Boolean, nullable=True)
    is_generic = Column(Boolean, nullable=False)

    # state
    crc_ok = Column(Boolean, nullable=True)
    file_version = Column(Integer, nullable=True)
    censored = Column(Boolean, nullable=True)

    length_in_seconds = Column(Integer, nullable=True)
    description = Column(String(512), nullable=True)
    aired_date = Column(Date, nullable=True)

    mylist_state = Column(
            Enum(
                'unknown',
                'on hdd',
                'on cd',
                'deleted'),
            nullable=True)
    mylist_filestate = Column(
            Enum(
                'normal/original',
                'corrupted version/invalid crc',
                'self edited',
                'self ripped',
                'on dvd',
                'on vhs',
                'on tv',
                'in theaters',
                'streamed',
                'other'),
            nullable=True)
    mylist_viewed = Column(Boolean, nullable=True)
    mylist_viewdate = Column(DateTime(timezone=False), nullable=True)
    mylist_storage = Column(String(128), nullable=True)
    mylist_source = Column(String(128), nullable=True)
    mylist_other = Column(String(128), nullable=true)

    updated = Column(DateTime(timezone=True), nullable=True)

    def update(self, **kwargs):
        for key, attr in kwargs.items():
            setattr(self, key, attr)

    def __repr__(self):
        return '<FileTable(pk={pk}, path={path}, mylist_state={state}, '\
                'mylist_viewed={viewed}, updated={updated})>'.format(
                pk=self.pk,
                path=self.path,
                state=self.mylist_state,
                viewed=self.mylist_viewed,
                updated=self.updated)

