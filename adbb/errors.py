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

class AniDBError(Exception):
    pass


class AniDBIncorrectParameterError(AniDBError):
    pass


class AniDBCommandTimeoutError(AniDBError):
    pass


class AniDBMustAuthError(AniDBError):
    pass


class AniDBPacketCorruptedError(AniDBError):
    pass


class AniDBInternalError(AniDBError):
    pass


class AniDBBannedError(AniDBError):
    pass


class AniDBFileError(AniDBError):
    pass


class AniDBPathError(AniDBError):
    pass

class IllegalAnimeObject(AniDBError):
    pass

class FanartError(AniDBError):
    pass

class AniDBMissingImage(AniDBError):
    pass
