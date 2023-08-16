#!/bin/env python3
import argparse
import datetime
import logging
import netrc
import os
import random
import re
import shutil
import socket
import signal
import sys
import time
import urllib

import adbb
import adbb.anames
from adbb.errors import *
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

# Specials-directories as defined by jellyfin
JELLYFIN_SPECIAL_DIRS = [
        'behind the scenes',
        'deleted scenes',
        'interviews',
        'scenes',
        'samples',
        'shorts',
        'featurettes',
        'extras',
        'trailers',
        'misc'
        ]

RE_JELLYFIN_SEASON_DIR = re.compile(r'^Season \d+$', re.I)

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
    parser = argparse.ArgumentParser(description="Rearange video files in directory to jellyfin-parsable anime episodes")
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

def create_filelist(paths, recurse=True):
    filelist = []
    for path in paths:
        # find all files with supported file extensions and add them to
        # our working list if input is a directory.
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d.lower() not in JELLYFIN_SPECIAL_DIRS]
                filelist.extend([ os.path.join(root, x) for x in files if x.rsplit('.')[-1] in SUPPORTED_FILETYPES ])
                if not recurse:
                    break
    return filelist

def remove_dir_if_empty(directory):
    log = logging.getLogger(__name__)
    remove=False
    for root, dirs, files in os.walk(directory):
        dirs[:] = []
        removed = []
        for f in files:
            p = os.path.join(root,f)
            if os.path.islink(p) and not os.path.exists(os.readlink(p)):
                os.remove(p)
                log.info(f"Removed broken link {p}")
                removed.append(f.lower())
        if not files or all([x.lower() in JELLYFIN_SPECIAL_DIRS + removed for x in files]):
            for f in files:
                os.remove(f)
            remove=True
    if remove:
        log.info(f"Removing empty directory {directory}")
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
        if not dry_run:
            os.makedirs(linkdir, exist_ok=True)
            tmplink = os.path.join(linkdir, f'.{name}.tmp')
            os.symlink(target, tmplink)
            os.rename(tmplink, linkname)
            stats = os.stat(target)
            os.utime(linkname, ns=(stats.st_atime_ns, stats.st_mtime_ns), follow_symlinks=False)
        log.info(f"Link {linkname} -> {target}")
    for d in JELLYFIN_SPECIAL_DIRS:
        extrasdir_src = os.path.join(targetdir, d)
        extrasdir_lnk = os.path.join(linkdir, d)
        if os.path.isdir(extrasdir_src) and not os.path.islink(extrasdir_lnk):
            if not dry_run:
                os.symlink(extrasdir_src, extrasdir_lnk)
                stats = os.stat(extrasdir_src)
                os.utime(extrasdir_lnk, ns=(stats.st_atime_ns, stats.st_mtime_ns), follow_symlinks=False)
            log.info(f"Link extras dir {extrasdir_lnk} -> {extrasdir_src}")
    # Will never remove the linkdir from here, but will clean up any broken
    # links
    if not dry_run:
        remove_dir_if_empty(linkdir)
    if exclusive_dir and os.path.isdir(exclusive_dir):
        changed=False
        for root, dirs, files in os.walk(exclusive_dir, followlinks=False):
            dirs = []
            for f in files:
                p = os.path.join(root, f)
                if os.path.islink(p) and os.readlink(p) == target:
                    if not dry_run:
                        os.remove(p)
                        changed=True
                    log.info(f"Remove link {p} from exclusive directory; it's now linked to from {linkdir}")
        if changed and not dry_run:
            remove_dir_if_empty(exclusive_dir)

# The callback will be called for each file after the new filename has been decided, but
# before the file is actually moved. 
# The callback function should take the keyword arguments:
# new_name: where arrange_files wants to move the file
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
        link_movies_dir=None,
        link_tv_dir=None,
        link_exclusive_dir=None
        ):
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

        if callback:
            ret = callback(new_name=newname, adbb_file=epfile)
            if ret:
                newname = ret

        # no action if file is already properly named and in the right place
        if f != newname:
            if os.path.exists(newname):
                adbb.log.error(f'Not moving "{f}" because file "{newname}" already exists')
                continue
            adbb.log.info(f'Move "{f}" -> "{newname}"')
            if not dry_run:
                nd, nh = os.path.split(newname)
                os.makedirs(nd, exist_ok=True)
                if 'freebsd' in sys.platform.lower():
                    shutil.copy2(f, newname)
                    os.remove(f)
                else:
                    try:
                        shutil.move(f, newname)
                    except OSError:
                        shutil.copy2(f, newname)
                        os.remove(f)
                od, oh = os.path.split(f)
                # Make sure extras-directories are moved if it's all that is
                # left in the old directory
                for root, dirs, files in os.walk(od):
                    if not files and all([x.lower() in JELLYFIN_SPECIAL_DIRS for x in dirs]):
                        for d in dirs:
                            if 'freebsd' in sys.platform.lower():
                                shutil.copytree(os.path.join(root, d), os.path.join(nd, d))
                                shutil.rmtree(os.path.join(root, d))
                            else:
                                try:
                                    shutil.move(os.path.join(root, d), os.path.join(nd, d))
                                except OSError:
                                    shutil.copytree(os.path.join(root, d), os.path.join(nd, d))
                                    shutil.rmtree(os.path.join(root, d))
                    break
                try:
                    os.rmdir(od)
                except OSError:
                    pass
                if not epfile.lid and not disable_mylist:

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

        if not is_extra:
            season, epno = epfile.episode.tvdb_episode

            if epfile.anime.nr_of_episodes == 1 and epfile.part:
                partstr = f'-part{epfile.part}'
            else:
                partstr = ""

            if link_exclusive_dir:
                if epfile.anime.nr_of_episodes == 1:
                    exclusive_dir = os.path.join(link_exclusive_dir, 'Movies', aname)
                else:
                    exclusive_dir = os.path.join(link_exclusive_dir, 'Series', aname)
            else:
                exclusive_dir = None

            # Jellyfin can't handle specials in absolute order (yet?)
            # https://github.com/jellyfin/jellyfin-plugin-tvdb/pull/92
            if season and season not in ["1", "a"] and epfile.anime.tvdbid:
                if adbb.anames.tvdbid_has_absolute_order(epfile.anime.tvdbid):
                    epno = None

            if epfile.anime.tvdbid and link_tv_dir and epno and not ( \
                    epfile.anime.nr_of_episodes == 1 and \
                    not epfile.anime.relations and \
                    (epfile.anime.tmdbid or epfile.anime.imdb)
                    ):
                if type(epno) is tuple:
                    partstr = f'-part{epno[1]}'
                    epno = epno[0]
                elif '+' in epno:
                    epno = epno.split('+')

                d = os.path.join(link_tv_dir, f'adbb [tvdbid-{epfile.anime.tvdbid}]')
                epnos = epfile.multiep
                if epnos[0] != epnos[-1]:
                    last_ep = adbb.Episode(anime=epfile.anime, epno=epnos[-1])
                    last_season, last_epno = last_ep.tvdb_episode
                    if type(last_epno) is tuple and last_epno[0] == epnos[0][0]:
                            epno = epnos[0][0]
                    elif '+' in last_epno:
                        epno = f"{epno}-{last_epno.split('+')[-1]}"
                    else:
                        epno = f'{epno}-{last_epno}'
                elif type(epno) is list:
                    epno = f'{epno[0]}-{epno[-1]}'
                if adbb.anames.tvdbid_has_absolute_order(epfile.anime.tvdbid):
                    linkname = f"{aname} - {epno}{partstr}.{ext}"
                else:
                    linkname = f"{aname} S{season}E{epno}{partstr}.{ext}"
                link = os.path.join(d, linkname)
                link_to_directory(newname, link, exclusive_dir=exclusive_dir, dry_run=dry_run)
            elif epfile.anime.nr_of_episodes == 1 and epfile.anime.tmdbid and link_movies_dir:
                d = os.path.join(link_movies_dir, f'adbb [tmdbid-{epfile.anime.tmdbid}]')
                linkname = os.path.basename(newname)
                link = os.path.join(d, linkname)
                link_to_directory(newname, link, exclusive_dir=exclusive_dir, dry_run=dry_run)
            elif epfile.anime.nr_of_episodes == 1 and epfile.anime.imdbid and link_movies_dir:
                d = os.path.join(link_movies_dir, f'adbb [imdbid-{epfile.anime.imdbid}]')
                linkname = os.path.basename(newname)
                link = os.path.join(d, linkname)
                link_to_directory(newname, link, exclusive_dir=exclusive_dir, dry_run=dry_run)
            elif exclusive_dir:
                linkname = os.path.basename(newname)
                link = os.path.join(exclusive_dir, linkname)
                link_to_directory(newname, link, dry_run=dry_run)

def arrange_anime():
    args = arrange_anime_args()
    filelist = create_filelist(args.paths)
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


def get_jellyfin_anime_sync_args():
    parser = argparse.ArgumentParser(description="Sync mylist watched state from jellyfin")
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
            '-j', '--jellyfin-url',
            help="URL to jellyfin root"
            )
    parser.add_argument(
            '-e', '--jellyfin-user',
            help="User to log in to jellyfin"
            )
    parser.add_argument(
            '-w', '--jellyfin-password',
            help="Password for jellyfin user"
            )
    parser.add_argument(
            '-r', '--rearrange',
            help="also move files to proper adbb format",
            action="store_true"
            )
    parser.add_argument(
            '-R', '--repeat',
            help="when sync is completed, start a new sync",
            action="store_true"
            )
    parser.add_argument(
            '-t', '--tvdb-library',
            help="Maintain a tvdb-compatible library at path",
            default=None,
            )
    parser.add_argument(
            '-m', '--moviedb-library',
            help="Maintain a imdb/tmdb-compatible library at path",
            default=None,
            )
    parser.add_argument(
            '-i', '--anidb-library',
            help='Maintain a library of anidb-exclusive titles at path',
            default=None,
            )
    parser.add_argument(
            '-g', '--staging-path',
            help='Add any compatible file at path to library',
            default=None
            )
    parser.add_argument(
            '-l', '--sleep-delay',
            help='''Sleep this number of seconds between each media directory.
                    Defaults to 0 when run as a one-time operation, in repeat-mode the
                    default is an aproximate to let a complete run finish in about 22
                    hours.''',
            type=int,
            default=None
            )
    parser.add_argument(
            '-y', '--use-syslog',
            help='Silence console output and log to syslog instead.',
            action="store_true"
            )
    parser.add_argument(
            '-W', '--no-watched',
            help='Disable mylist update of watched status',
            action="store_true"
            )
    parser.add_argument(
            '-n', '--dry-run',
            help="do not actually move stuff..",
            action='store_true'
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
            '-b', '--api-key',
            help="Enable encryption using the given API key as defined in your AniDB profile",
            default=None
            )
    parser.add_argument(
            'path',
            help="Where the anime is stored"
            )
    return parser.parse_args()

def init_jellyfin(url, user, password):
    import jellyfin_apiclient_python
    APP=f"{adbb.anidb_client_name}_jellyfin"
    VER=adbb.anidb_client_version
    DEV=adbb.anidb_client_name
    DEV_ID=socket.gethostname()

    client = jellyfin_apiclient_python.JellyfinClient()
    client.config.app(APP, VER, DEV, DEV_ID)
    client.config.auth(url, user, password, True)
    client.config.http(f"{APP}/{VER}")

    client.auth.connect_to_address(url)
    client.auth.login(url, user, password)
    return client

def signal_handler(signo, _stack_frame):
    global status_msg
    if signo == signal.SIGUSR1:
        adbb.log.info(status_msg)
        return
    adbb.log.info(f"Signal {signo} received, logging out and shutting down...")
    adbb.close()
    sys.exit(0)

def jellyfin_anime_sync():
    import jellyfin_apiclient_python.exceptions
    args = get_jellyfin_anime_sync_args()
    global status_msg

    log = get_command_logger(debug=args.debug, syslog=args.use_syslog)
    reinit_adbb=True
    failures=0
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGHUP, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGUSR1, signal_handler)

    while True:
        delay=0
        iterations=0
        starttime = datetime.datetime.now()
        try:
            if not args.jellyfin_user or not args.jellyfin_password:
                parsed_url = urllib.parse.urlparse(args.jellyfin_url)
                nrc = netrc.netrc(args.authfile)
                user, _account, password = nrc.authenticators(parsed_url.hostname)
            else:
                user, password = (args.jellyfin_user, args.jellyfin_password)

            if reinit_adbb:
                adbb.init(args.sql_url, api_user=args.username, api_pass=args.password, logger=log, netrc_file=args.authfile, api_key=args.api_key)
                reinit_adbb=False
            adbb.update_anilist()
            adbb.update_animetitles()


            # we actually do not need the jellyfin client that much...
            jf_client = init_jellyfin(args.jellyfin_url, user, password)
            res = jf_client.jellyfin.user_items(params={
                    'recursive': True,
                    'mediaTypes': [ 'Video' ],
                    'collapseBoxSetItems': False,
                    'fields': 'Path',
                    })
            jf_client.stop()
            metadata = { os.path.realpath(x['Path']): x for x in res['Items'] if 'Path' in x and os.path.realpath(x['Path']).startswith(args.path) }
            adbb.log.debug(f"Found {len(metadata)} files in jellyfin")

            # search for a file in the metadata dict and return when watched
            # Will return False if not watched or if not in dict
            def get_watched_for_file(path):
                if path in metadata:
                    if 'LastPlayedDate' in metadata[path]['UserData']:
                        timestr = metadata[path]['UserData']['LastPlayedDate']
                        timestr = timestr = f"{timestr[:23].strip('Z'):0<23}+0000"
                        return datetime.datetime.strptime(
                                timestr,
                                '%Y-%m-%dT%H:%M:%S.%f%z'
                                )
                return False

            full_path_list = []
            for root, dirs, files in os.walk(args.path):
                dirs[:] = [d for d in dirs if d.lower() not in JELLYFIN_SPECIAL_DIRS]
                files[:] = [ x for x in files if x.rsplit('.')[-1] in SUPPORTED_FILETYPES ]
                if files:
                    full_path_list.append(root)
            random.shuffle(full_path_list)

            if args.sleep_delay:
                delay = args.sleep_delay
            elif args.sleep_delay is None and args.repeat:
                delay = max(min((22*60*60-len(full_path_list)*9)/len(full_path_list), 300), 0)
            else:
                delay = 0
            adbb.log.info(f"Starting sync of {len(full_path_list)} paths with {delay} seconds delay between paths.")

            for path in full_path_list:
                iterations += 1
                for root, dirs, files in os.walk(path):
                    dirs[:] = []
                    files[:] = [ x for x in files if x.rsplit('.')[-1] in SUPPORTED_FILETYPES ]
                    if '/Movies/' in root:
                        single_ep = True
                    else:
                        single_ep = False
                    
                    pdir, cdir = os.path.split(root)
                    while pdir:
                        if cdir and not re.match(RE_JELLYFIN_SEASON_DIR, cdir):
                            break
                        pdir, cdir = os.path.split(pdir)
                    
                    adbb.log.debug(f"Found {len(files)} files in folder for '{cdir}'")
                    try:
                        anime = adbb.Anime(cdir)
                    except IllegalAnimeObject as e:
                        adbb.log.error(f"Couldn't identify Anime at '{root}': {e}")
                        continue

                    if not args.no_watched:
                        for f in files:
                            fpath = os.path.join(root, f)
                            watched = get_watched_for_file(fpath)
                            adbb.log.debug(f"{fpath} watched: {watched}")
                            fo = adbb.File(path=fpath, anime=anime, force_single_episode_series=single_ep)
                            if not fo.mylist_state or fo.mylist_viewed != bool(watched):
                                for ep in fo.multiep:
                                    if str(ep).lower() == str(fo.episode.episode_number).lower() and not fo.is_generic:
                                        if not args.dry_run:
                                            fo.update_mylist(state='on hdd', watched=watched)
                                        else:
                                            adbb.log.info(f'update mylist for {fo}, watched: "{watched}"')
                                    else:
                                        mylist_fo = adbb.File(anime=anime, episode=ep)
                                        if not mylist_fo.mylist_state or mylist_fo.mylist_viewed != bool(watched):
                                            if not args.dry_run:
                                                mylist_fo.update_mylist(state='on hdd', watched=watched)
                                            else:
                                                adbb.log.info(f'update mylist for {mylist_fo}, watched: "{watched}"')

                    if args.rearrange:
                        arrange_files([os.path.join(root, f) for f in files],
                                      target_dir=pdir,
                                      dry_run=args.dry_run,
                                      link_movies_dir=args.moviedb_library,
                                      link_tv_dir=args.tvdb_library,
                                      link_exclusive_dir=args.anidb_library
                                      )
                        if args.staging_path:
                            files = create_filelist([args.staging_path])
                            arrange_files(files,
                                          target_dir=args.path,
                                          dry_run=args.dry_run,
                                          link_movies_dir=args.moviedb_library,
                                          link_tv_dir=args.tvdb_library,
                                          link_exclusive_dir=args.anidb_library,
                                          check_previous=args.check_previous_episode,
                                          check_complete=args.check_series_complete
                                          )
                    failures = 0
                    runtime = datetime.datetime.now()-starttime
                    status_msg = f'{iterations}/{len(full_path_list)} paths processed in {str(runtime)}.'
                    time.sleep(delay)

            # Clean up broken symlinks/empty dirs
            links = {}
            for path in [args.tvdb_library, args.moviedb_library]:
                if not path:
                    continue
                for root, dirs, files in os.walk(path):
                    dirs[:] = [x for x in dirs if x.startswith('adbb [')]
                    if files:
                        remove_dir_if_empty(root)
                        for link in files:
                            lp = os.path.join(root, link)
                            if os.path.islink(lp):
                                target = os.readlink(lp)
                                if not target in links:
                                    links[target] = [lp]
                                else:
                                    links[target].append(lp)
            if args.anidb_library:
                for root, dirs, files in os.walk(args.anidb_library):
                    dirs[:] = [d for d in dirs if d.lower() not in JELLYFIN_SPECIAL_DIRS]
                    if files:
                        remove_dir_if_empty(root)
                        for link in files:
                            lp = os.path.join(root, link)
                            if os.path.islink(lp):
                                target = os.readlink(lp)
                                if not target in links:
                                    links[target] = [lp]
                                else:
                                    links[target].append(lp)
            multilinked = {t: l for t,l in links.items() if len(l) > 1}
            for t, l in multilinked.items():
                adbb.log.warning(f"{t} linked to from multiple places: {l}")
            runtime = datetime.datetime.now()-starttime
            log.info(f"Completed sync in {str(runtime)}")
        except (sqlalchemy.exc.OperationalError, jellyfin_apiclient_python.exceptions.HTTPException) as e:
            if not failures:
                failures = 1
            else:
                failures *= 2
            adbb.log.warning(f"Network error, will retry in {failures} minutes: {e}")
            adbb.close()
            reinit_adbb=True
            time.sleep(failures*60)

        if not (args.repeat or failures):
            break

    adbb.close()

if __name__ == '__main__':
    main()
