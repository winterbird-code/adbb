#!/bin/env python3
import argparse
import datetime
import netrc
import os
import re
import shutil
import socket
import sys
import urllib

import adbb

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
        ]

RE_JELLYFIN_SEASON_DIR = re.compile(r'^Season \d+$', re.I)

# matches groupnames from paranthesis at start of filename
RE_GROUP_START = re.compile(r'^[\(\[]([^\d\]\)]+)[\)\]].*')
# matches groupnames from paranthesis at end of filename
RE_GROUP_END = re.compile(r'.*[\(\[]([^\d\]\)]+)[\)\]]\.\w{3,4}$')

# matches anidb's default english episode names
RE_DEFAULT_EPNAME = re.compile(r'Episode S?\d+', re.I)


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
        else:
            filelist.append(path)
    return filelist

def arrange_files(filelist, target_dir=None, dry_run=False):
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

        aname = epfile.anime.title.replace('/', '⁄')
        ext = f.rsplit('.')[-1]
        if epfile.anime.nr_of_episodes == 1:
            # personal definition of movies: exactly 1 (main-)episode.
            # Name movies after title and append groupname to mark different
            # "versions" of the movie:
            #   https://jellyfin.org/docs/general/server/media/movies.html
            newname = f'{aname} [{group}].{ext}'

            # But wait, what if this is just a part of the movie?
            if epfile.part:
                newname = f'{aname} [{group}]-part{epfile.part}.{ext}'
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

            is_extra=False
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

            if is_extra:
                newname = os.path.join('extras', f'[{group}] {aname} {m.group(1)}{epstr}{title}.{ext}')
            else:
                newname = f'[{group}] {aname} S{season}E{epstr}{title}.{ext}'

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
                adbb.log.error('Not moving "{f}" because file "{newname}" already exists')
                break
            adbb.log.info(f'Moving "{f}" -> "{newname}"')
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
                if not epfile.lid:
                    epfile.update_mylist(watched=False, state='on hdd')

def arrange_anime():
    args = arrange_anime_args()
    filelist = create_filelist(args.paths)
    adbb.init(args.sql_url, api_user=args.username, api_pass=args.password, debug=args.debug, netrc_file=args.authfile)
    arrange_files(filelist, target_dir=args.target_dir, dry_run=args.dry_run)
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
            'paths',
            help="Where the anime is stored",
            nargs="+"
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


def jellyfin_anime_sync():
    args = get_jellyfin_anime_sync_args()

    if not args.jellyfin_user or not args.jellyfin_password:
        parsed_url = urllib.parse.urlparse(args.jellyfin_url)
        nrc = netrc.netrc(args.authfile)
        user, _account, password = nrc.authenticators(parsed_url.hostname)
    else:
        user, password = (args.jellyfin_user, args.jellyfin_password)

    adbb.init(args.sql_url, api_user=args.username, api_pass=args.password, debug=args.debug, netrc_file=args.authfile)

    # we actually do not need the jellyfin client that much...
    jf_client = init_jellyfin(args.jellyfin_url, user, password)
    res = jf_client.jellyfin.user_items(params={
            'recursive': True,
            'includeItemTypes': ['Episode', 'Movie', 'Video'],
            'fields': 'Path',
            })
    jf_client.stop()
    metadata = {}
    for path in args.paths:
        metadata.update({ x['Path']: x for x in res['Items'] if x['Path'].startswith(path) })
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

    for path in args.paths:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d.lower() not in JELLYFIN_SPECIAL_DIRS]
            files[:] = [ x for x in files if x.rsplit('.')[-1] in SUPPORTED_FILETYPES ]
            if not files:
                continue
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
            anime = adbb.Anime(cdir)
            for f in files:
                fpath = os.path.join(root, f)
                watched = get_watched_for_file(fpath)
                adbb.log.debug(f"{fpath} watched: {watched}")
                fo = adbb.File(path=fpath, anime=anime, force_single_episode_series=single_ep)
                if not fo.mylist_state or fo.mylist_viewed != bool(watched):
                    for ep in fo.multiep:
                        if str(ep).lower() == str(fo.episode.episode_number).lower():
                            fo.update_mylist(state='on hdd', watched=watched)
                        elif fo.is_generic:
                            mylist_fo = adbb.File(anime=anime, episode=ep)
                            mylist_fo.update_mylist(state='on hdd', watched=watched)

            if args.rearrange:
                arrange_files([os.path.join(root, f) for f in files], target_dir=pdir)

    adbb.close()


if __name__ == '__main__':
    main()
