#!/bin/env python3
import argparse
import datetime
import logging
import os
import re
import shutil
import signal
import sys
import time

import adbb
import adbb.anames
import adbb.fileinfo
from adbb.errors import *
from adbb.db import *
import sqlalchemy.exc

status_msg=None

# These extensions are considered video types
SUPPORTED_FILETYPES = [
        'mkv',
        'avi',
        'mp4',
        'ogm',
        'wmv',
        'm4v',
        'webm',
        ]

EXTRAS_DIRS = [
        'extras'
        ]

# matches groupnames from paranthesis at start of filename
RE_GROUP_START = re.compile(r'^[\(\[]([^\d\]\)]+)[\)\]].*')
# matches groupnames from paranthesis at end of filename
RE_GROUP_END = re.compile(r'^.*[\(\[]([^\d\]\)]+)[\)\]].*')

# matches anidb's default english episode names
RE_DEFAULT_EPNAME = re.compile(r'Episode S?\d+', re.I)

class InfoLogFilter(logging.Filter):
    def filter(self, record):
        if record.levelno <= logging.INFO:
            return True
        return False

def get_related_anime(anime, exclude=[], only_in_mylist=True):
    if type(anime) != list:
        anime = [anime]

    res = anime.copy()
    for a in anime:
        relations = [x[1] for x in a.relations if x[1] not in exclude and (not only_in_mylist or x[1].in_mylist)]
        for r in relations:
            res.append(r)
            r_relations = [x[1] for x in r.relations if x[1] not in relations + res + exclude and (not only_in_mylist or x[1].in_mylist)]
            relations.extend(r_relations)

    return res

def get_command_logger(debug=False, syslog=False):
    logger = logging.getLogger(__name__)
    if debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    if syslog:
        if os.path.exists('/dev/log'):
            lh = logging.handlers.SysLogHandler(address='/dev/log')
        else:
            lh = logging.handlers.SysLogHandler()
        lh.setFormatter(logging.Formatter(
            f'{os.path.basename(sys.argv[0])}[%(process)d] %(levelname)s: %(message)s'))
        logger.addHandler(lh)

    else:
        lh = logging.StreamHandler(stream=sys.stdout)
        lh.setFormatter(logging.Formatter('%(asctime)s: %(message)s'))
        lh.addFilter(InfoLogFilter())
        logger.addHandler(lh)

        lh = logging.StreamHandler(stream=sys.stderr)
        lh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(filename)s:%(lineno)d - %(message)s'))
        lh.setLevel(logging.WARNING)
        logger.addHandler(lh)
    return logger


def arrange_anime_args():
    parser = argparse.ArgumentParser(description="Rearange video files in directory to media-center-parsable anime episodes")
    parser.add_argument(
            '-n', '--dry-run',
            help="do not actually move stuff..",
            action='store_true'
            )
    parser.add_argument(
            '-d', '--debug',
            help='show debug information from adbb',
            action='store_true'
            )
    parser.add_argument(
            '-u', '--username',
            help='anidb username',
            )
    parser.add_argument(
            '-p', '--password',
            help='anidb password'
            )
    parser.add_argument(
            '-s', '--sql-url',
            help='sqlalchemy compatible sql URL',
            default=f'sqlite:///{os.path.expanduser("~/.adbb.db")}'
            )
    parser.add_argument(
            '-a', '--authfile',
            help="Authfile (.netrc-file) for credentials"
            )
    parser.add_argument(
            "paths",
            help="Directories containing episodes",
            nargs="+"
            )
    parser.add_argument(
            "-t", '--target-dir',
            help="Where to put the stuff after parsing...",
            )
    parser.add_argument(
            '-c', '--check-series-complete',
            action='store_true',
            help="log warning message when adding last episode in series to mylist")
    parser.add_argument(
            '-k', '--check-previous-episode',
            action='store_true',
            help='log warning message when adding file to mylist if previous episode is not in mylist')
    parser.add_argument(
            '-M', '--disable-mylist',
            action='store_true',
            help='do not update mylist status for files')
    parser.add_argument(
            '-b', '--api-key',
            help="Enable encryption using the given API key as defined in your AniDB profile",
            default=None
            )
    return parser.parse_args()

def create_filelist(paths, recurse=True, ignore_dirs=EXTRAS_DIRS):
    filelist = []
    for path in paths:
        # find all files with supported file extensions and add them to
        # our working list if input is a directory.
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d.lower() not in ignore_dirs]
                filelist.extend([ os.path.join(root, x) for x in files if x.rsplit('.')[-1] in SUPPORTED_FILETYPES ])
                if not recurse:
                    break
    return filelist

def fsop(source, target, link=False, dry_run=False, skip_clean=False, companion_dirs=EXTRAS_DIRS):
    """ Remove, move or link files and any related file (same basename, other
    extension """
    log = logging.getLogger(__name__)

    directory, name = os.path.split(source)
    basename = name.rsplit('.', 1)[0]
    dir_content = os.listdir(directory)
    all_files = [x for x in dir_content if x.startswith(f'{basename}.')]
    specials = [x for x in dir_content if x.lower() in companion_dirs]

    if target:
        target_dir, target_name = os.path.split(target)
        if not dry_run:
            os.makedirs(target_dir, exist_ok=True)
    else:
        target_dir = None

    for f in all_files:
        path = os.path.join(directory, f)

        # Remove mode
        if not target:
            log.info(f"Remove {path}")
            if not dry_run:
                os.remove(path)
            continue

        # Move or link 
        ext = f.rsplit('.', 1)[-1]
        target_base = target_name.rsplit('.', 1)[0]
        target_name = f'{target_base}.{ext}'
        target_path = os.path.join(target_dir, target_name)

        # Move
        if not link:
            log.info(f'Move {path} -> {target_path}')
            if not dry_run:
                if 'freebsd' in sys.platform.lower():
                    shutil.copy2(path, target_path)
                    os.remove(path)
                else:
                    try:
                        shutil.move(path, target_path)
                    except OSError:
                        shutil.copy2(path, target_path)
                        os.remove(f)
            continue

        # Link
        log.info(f'Link {target_path} -> {path}')
        if not dry_run:
            tmplink = os.path.join(target_dir, f'.{target_name}.tmp')
            os.symlink(path, tmplink)
            os.rename(tmplink, target_path)
            stats = os.stat(path)
            os.utime(target_path, ns=(stats.st_atime_ns, stats.st_mtime_ns), follow_symlinks=False)

    if not link:
        # Make sure extras-directories are moved if it's all that is
        # left in the old directory
        dirs = os.listdir(directory)
        if all([x.lower() in companion_dirs for x in dirs]):
            for d in dirs:
                spath = os.path.join(directory, d)
                tpath = os.path.join(target_dir, d)
                log.info(f'Move Extras {spath} -> {tpath}')
                if not dry_run:
                    if 'freebsd' in sys.platform.lower():
                        shutil.copytree(spath, tpath)
                        shutil.rmtree(spath)
                    else:
                        try:
                            shutil.move(spath, tpath)
                        except OSError:
                            shutil.copytree(spath, tpath)
                            shutil.rmtree(spath)
    elif target_dir and os.path.isdir(target_dir):
        # Make sure to link any extras directories in the source path to the
        # target path
        target_specials = [x for x in os.listdir(target_dir) if x.lower() in companion_dirs]
        for d in specials:
            if d not in target_specials:
                spath = os.path.join(directory, d)
                tpath = os.path.join(target_dir, d)
                log.info(f'Link Extras {tpath} -> {spath}')
                if not dry_run:
                    os.symlink(spath, tpath)
                    stats = os.stat(spath)
                    os.utime(tpath, ns=(stats.st_atime_ns, stats.st_mtime_ns), follow_symlinks=False)

    # Clean up broken links at both source and target and remove directory if
    # empty
    if not skip_clean:
        remove_dir_if_empty(directory, dry_run=dry_run)
        if target_dir and os.path.isdir(target_dir):
            remove_dir_if_empty(target_dir, dry_run=dry_run)


def remove_dir_if_empty(directory, dry_run=False, extras_dirs=EXTRAS_DIRS):
    log = logging.getLogger(__name__)
    remove=False
    for root, dirs, files in os.walk(directory):
        dirs[:] = []
        for f in files:
            p = os.path.join(root,f)
            if os.path.islink(p) and not os.path.exists(os.readlink(p)):
                log.info(f"Remove broken link {p}")
                fsop(p, None, skip_clean=True)

    content = os.listdir(directory)
    if not content or all([x.lower() in extras_dirs for x in content]):
        for l in content:
            path = os.path.join(directory, l)
            if os.path.islink(path):
                log.info(f'Remove stray Extras link {path}')
                fsop(path, None, skip_clean=True)

        log.info(f"Remove empty directory {directory}")
        if not dry_run:
            try:
                os.rmdir(directory)
            except OSError as e:
                log.error(f"Could not remove directory {directory}: {e}")

def link_to_directory(target, linkname, exclusive_dir=None, dry_run=False):
    log = logging.getLogger(__name__)
    linkdir, name = os.path.split(linkname)
    targetdir, targetname = os.path.split(target)
    if os.path.islink(linkname) and os.readlink(linkname) == target:
        pass
    else:
        fsop(target, linkname, link=True, dry_run=dry_run)

    if exclusive_dir and os.path.isdir(exclusive_dir):
        for root, dirs, files in os.walk(exclusive_dir, followlinks=False):
            dirs[:] = []
            for f in files:
                p = os.path.join(root, f)
                if os.path.islink(p) and os.readlink(p) == target:
                    fsop(p, None, dry_run=dry_run)

# The callback will be called for each file after the file has been put in
# place
# The callback function should take the keyword arguments:
# path: where the file is located
# adbb_file: the adbb.File object for the current file
#
# If the callback returns it should be a string containing a new path where to
# move the file.
def arrange_files(
        filelist,
        target_dir=None,
        dry_run=False,
        check_previous=False,
        check_complete=False,
        disable_mylist=False,
        callback=None,
        ):
    log = logging.getLogger(__name__)
    for f in filelist:
        epfile = adbb.File(path=f)
        if epfile.group:
            # replace any / since it's not supported for filenames in *nix
            group = epfile.group.name.replace('/', '⁄')
        else:
            # if anidb don't know about the group we try to parse the filename
            # look for [] or ()-blocks at start or end of filename, and assume this
            # is the groupname.
            m = re.match(RE_GROUP_START, os.path.split(f)[-1])
            if not m:
                m = re.match(RE_GROUP_END, os.path.split(f)[-1])
            if m:
                # since we matched on filename previously there is no need to
                # replace any chars here.
                group = m.group(1)
            else:
                group = "unknown"

        # how many characters in an episode number? will probably return 1 (<10
        # episodes), 2 (<100 episodes), 3 (<1000 episodes) or 4 (hello Conan
        # and Luffy)
        epnr_minlen = len(str(max(epfile.anime.highest_episode_number, epfile.anime.nr_of_episodes)))

        aname = epfile.anime.title.replace('/', '⁄').lstrip('.')
        ext = f.rsplit('.')[-1]
        is_extra=False
        if epfile.anime.nr_of_episodes == 1:

            # Check if anidb knows if this is a specific version
            if epfile.file_version and epfile.file_version != 1:
                vstr = f'v{epfile.file_version}'
            else:
                vstr = ''

            # personal definition of movies: exactly 1 (main-)episode.
            # Name movies after title and append groupname to mark different
            # "versions" of the movie:
            #   https://jellyfin.org/docs/general/server/media/movies.html
            newname = f'{aname} [{group}]{vstr}.{ext}'

            # But wait, what if this is just a part of the movie?
            if epfile.part:
                newname = f'{aname} [{group}]{vstr}-part{epfile.part}.{ext}'
        else:
            # Use the first found of these titles:
            # * romanji
            # * kanji
            # * english (if set to anything other than default "Episode XX")
            #
            # If no title was found we do not append it to the filename
            if epfile.episode.title_romaji:
                title = f' - {epfile.episode.title_romaji}'.replace('/', '⁄')
            elif epfile.episode.title_kanji:
                title = f' - {epfile.episode.title_kanji}'.replace('/', '⁄')
            elif epfile.episode.title_eng and not re.match(RE_DEFAULT_EPNAME, epfile.episode.title_eng):
                title = f' - {epfile.episode.title_eng}'.replace('/', '⁄')
            else:
                title = ''

            m = re.match(adbb.fileinfo.specials_re, epfile.multiep[0])
            if m:
                epnr_minlen = len(str(epfile.anime.special_ep_count))
                if m.group(1).upper() == 'S':
                    season='0'
                else:
                    is_extra=True
            else:
                season='1'

            # check if file contains multiple episodes
            if len(epfile.multiep) > 1:
                mi = int(epfile.multiep[0].strip('SCTOPsctop'))
                ma = int(epfile.multiep[-1].strip('SCTOPsctop'))
                epstr = f'{mi:0{epnr_minlen}d}-{ma:0{epnr_minlen}d}'
            else:
                epstr = f'{int(epfile.multiep[0].strip("SCTOPsctop")):0{epnr_minlen}d}'

            # Is the file versioned?
            if epfile.file_version and epfile.file_version != 1:
                vstr = f'v{epfile.file_version}'
            else:
                vstr = ''

            if is_extra:
                newname = f'{aname} {m.group(1)}{epstr}{title} [{group}]{vstr}.{ext}'
                if len(newname.encode('utf8')) > 250:
                    newname = f'{aname} {m.group(1)}{epstr} [{group}]{vstr}.{ext}'
                newname = os.path.join('extras', newname)
            else:
                newname = f'{aname} S{season}E{epstr}{title} [{group}]{vstr}.{ext}'
                if len(newname.encode('utf8')) > 250:
                    newname = f'{aname} S{season}E{epstr} [{group}]{vstr}.{ext}'


        if target_dir:
            # Escape slash as usual, but for also remove dot-prefixes because
            # "Hidden" directories are a hastle; sorry .hack//...
            anime_dirname = epfile.anime.title.replace('/', '⁄').lstrip('.')
            movie_subdir = 'Movies'
            series_subdir = 'Series'
            # If target directory has separate subdirs for movies and series;
            # place the files there
            if os.path.isdir(os.path.join(target_dir, movie_subdir)) and epfile.anime.nr_of_episodes == 1:
                newname = os.path.join(target_dir, movie_subdir, anime_dirname, newname)
            elif os.path.isdir(os.path.join(target_dir, series_subdir)):
                newname = os.path.join(target_dir, series_subdir, anime_dirname, newname)
            else:
                newname = os.path.join(target_dir, anime_dirname, newname)

        # no action if file is already properly named and in the right place
        if f != newname:
            if os.path.exists(newname):
                adbb.log.error(f'Not moving "{f}" because file "{newname}" already exists')
                continue
            fsop(f, newname, dry_run=dry_run)

            if not (dry_run or epfile.lid or disable_mylist):
                if check_complete:
                    last_ep = epfile.multiep[-1]
                    if last_ep == str(epfile.anime.nr_of_episodes):
                        adbb.log.warning(f'Adding last episode ({last_ep}) of {epfile.anime.title} to mylist')
                if check_previous:
                    try:
                        prev_epno = int(epfile.episode.episode_number)-1
                    except ValueError:
                        prev_epno = -1
                    if prev_epno > 0:
                        prev_ep = adbb.File(anime=epfile.anime, episode=prev_epno)
                        if not prev_ep.lid:
                            adbb.log.warning(f'Adding episode {epfile.episode.episode_number} of {epfile.anime.title} to mylist, but episode {prev_epno} is not in mylist!')

                for e in epfile.multiep:
                    if str(e).lower() == str(epfile.episode.episode_number).lower():
                        epfile.update_mylist(watched=False, state='on hdd')
                    else:
                        tmpfile = adbb.File(anime=epfile.anime, episode=e)
                        tmpfile.update_mylist(watched=False, state='on hdd')

        if callback:
            callback(path=newname, adbb_file=epfile)

def arrange_anime():
    args = arrange_anime_args()
    filelist = create_filelist(args.paths, ignore_dirs=EXTRAS_DIRS)
    if not filelist:
        sys.exit(0)
    log = get_command_logger(debug=args.debug)
    adbb.init(args.sql_url, api_user=args.username, api_pass=args.password, logger=log, netrc_file=args.authfile, api_key=args.api_key)
    arrange_files(
            filelist,
            target_dir=args.target_dir,
            dry_run=args.dry_run,
            check_previous=args.check_previous_episode,
            check_complete=args.check_series_complete,
            disable_mylist=args.disable_mylist)
    adbb.close()

def signal_handler(signo, _stack_frame):
    global status_msg
    if signo == signal.SIGUSR1:
        adbb.log.info(status_msg)
        return
    adbb.log.info(f"Signal {signo} received, logging out and shutting down...")
    adbb.close()
    sys.exit(0)


def get_cache_cleaner_args():
    parser = argparse.ArgumentParser(description="Clean adbb database cache from old or unwanted entries")
    parser.add_argument(
            '-n', '--dry-run',
            help="do not remove database entries",
            action='store_true'
            )
    parser.add_argument(
            '-d', '--debug',
            help='show debug information from adbb',
            action='store_true'
            )
    parser.add_argument(
            '-u', '--username',
            help='anidb username',
            )
    parser.add_argument(
            '-p', '--password',
            help='anidb password'
            )
    parser.add_argument(
            '-s', '--sql-url',
            help='sqlalchemy compatible sql URL',
            default=f'sqlite:///{os.path.expanduser("~/.adbb.db")}'
            )
    parser.add_argument(
            '-a', '--authfile',
            help="Authfile (.netrc-file) for credentials"
            )
    parser.add_argument(
            '-b', '--api-key',
            help="Enable encryption using the given API key as defined in your AniDB profile",
            default=None
            )
    subparsers=parser.add_subparsers(dest='operation', help='Type of cache cleaning')

    parser_old=subparsers.add_parser('old', help='Remove old stuff that has not been accessed in a long time')
    parser_old.add_argument(
            '-g', '--age',
            help='How old the entry must be, in days',
            type=int,
            default=90)

    parser_anime=subparsers.add_parser('anime', help='Remove anime entries')
    parser_anime.add_argument(
            'ids',
            help='Names or IDs of Anime to remove from cache',
            nargs='+')

    parser_group=subparsers.add_parser('group', help='Remove group entries')
    parser_group.add_argument(
            'ids',
            help='Names or IDs of Group to remove from cache',
            nargs='+')

    parser_episode=subparsers.add_parser('episode', help='Remove episode entries')
    parser_episode.add_argument(
            '-a', '--anime',
            help='Anime ID or title if removing by episode number',
            default=None)
    parser_episode.add_argument(
            'ids',
            help='Episode ID:s, or episode numbers if --anime-flag is used, to remove',
            nargs='+')

    parser_file=subparsers.add_parser('file', help='Remove file entries')
    parser_file.add_argument(
            '-m', '--remove-from-mylist',
            action='store_true',
            help='Remove files from mylist as well, requires UDP API credentials to be set')
    parser_file.add_argument(
            '-r', '--remove-files',
            action='store_true',
            help='Remove files from filesystem as well')
    parser_file.add_argument(
            "files",
            help="File identifiers, can be any of (searched in order) 'path', 'ed2khash', 'lid' or 'fid'. First match is used",
            nargs="*"
            )

    return parser.parse_args()

def cache_cleaner():
    args = get_cache_cleaner_args()

    log = get_command_logger(debug=args.debug)
    adbb.init(
            args.sql_url,
            api_user=args.username,
            api_pass=args.password,
            logger=log,
            netrc_file=args.authfile,
            api_key=args.api_key,
            db_only=True)

    if args.operation == 'old':
        sess = adbb.get_session()
        for table in [AnimeTable, EpisodeTable, FileTable, GroupTable]:
            past = datetime.datetime.now()-datetime.timedelta(days=args.age)
            res = sess.query(table).filter(table.last_update_dice < past).all()
            for obj in res:
                log.info(f'Remove {obj}; last access {obj.last_update_dice}')
                if not args.dry_run:
                    sess.delete(obj)
        sess.commit()
        adbb.close_session(sess)

    elif args.operation == 'anime':
        aids = set()
        for i in args.ids:
            try:
                aids.add(int(i))
            except ValueError:
                aid, _titles, _score, _title = get_titles(name=i, max_results=1)[0]
                aids.add(int(aid))

        sess = adbb.get_session()
        res = sess.query(AnimeTable).filter(AnimeTable.aid.in_(aids)).all()
        for obj in res:
            log.info(f'Remove {obj}')
            if not args.dry_run:
                sess.delete(obj)
        sess.commit()
        adbb.close_session(sess)

    elif args.operation == 'group':
        sess = adbb.get_session()
        gids = set()
        names = set()
        for i in args.ids:
            try:
                gid = int(i)
                gids.add(gid)
            except ValueError:
                names.add(i)

        res = sess.query(GroupTable).filter(
                sqlalchemy.or_(
                    GroupTable.gid.in_(gids),
                    GroupTable.name.in_(names),
                    GroupTable.short.in_(names))).all()
        for obj in res:
            log.info(f'Remove {obj}')
            if not args.dry_run:
                sess.delete(obj)
        sess.commit()
        adbb.close_session(sess)

    elif args.operation == 'episode':
        sess = adbb.get_session()
        if args.anime:
            try:
                aid = int(args.anime)
            except ValueError:
                aid, _titles, _score, _title = get_titles(name=i, max_results=1)[0]
            res = sess.query(EpisodeTable).filter(EpisodeTable.aid == aid, EpisodeTable.epno.in_(args.ids)).all()
        else:
            ids = [int(x) for x in args.ids]
            res = sess.query(EpisodeTable).filter(EpisodeTable.eid.in_(ids)).all()
        for obj in res:
            log.info(f'Remove {obj}')
            if not args.dry_run:
                sess.delete(obj)
        sess.commit()
        adbb.close_session(sess)

    elif args.operation == 'file':
        if args.remove_from_mylist:
            adbb.close()
            adbb.init(
                    args.sql_url,
                    api_user=args.username,
                    api_pass=args.password,
                    logger=log,
                    netrc_file=args.authfile,
                    api_key=args.api_key,
                    db_only=False)
        files = set()
        ids = set()
        for file in args.files:
            if os.path.exists(file):
                ed2k = adbb.fileinfo.get_file_hash(file)
                files.add(ed2k)
            else:
                try:
                    i = int(file)
                    ids.add(i)
                except ValueError:
                    files.add(file)

        sess = adbb.get_session()
        res = sess.query(FileTable).filter(
                sqlalchemy.or_(
                    FileTable.path.in_(files),
                    FileTable.ed2khash.in_(files),
                    FileTable.lid.in_(ids),
                    FileTable.fid.in_(ids))).all()
        if args.remove_from_mylist:
            for obj in res:
                if obj.lid:
                    file = adbb.File(lid=obj.lid)
                    log.info(f'Remove {file} from mylist')
                    if not args.dry_run:
                        file.remove_from_mylist()
        if args.remove_files:
            for obj in res:
                if obj.path and os.path.exists(obj.path):
                    log.info(f'Remove file {obj.path}')
                    if not args.dry_run:
                        os.remove(obj.path)
            for path in args.files:
                if os.path.exists(path):
                    log.info(f'Remove file {path}')
                    if not args.dry_run:
                        os.remove(path)
        for obj in res:
            log.info(f'Remove {obj}')
            if not args.dry_run:
                sess.delete(obj)
        sess.commit()
        adbb.close_session(sess)

    adbb.close()

if __name__ == '__main__':
    cache_cleaner()
