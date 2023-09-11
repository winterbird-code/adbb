import argparse
import datetime
import netrc
import os
import random
import re
import socket
import signal
import time
import configparser
import urllib
import urllib.error
import xml.etree.ElementTree as ET

import adbb
import adbb.anames
import adbb.utils
from adbb.errors import *
import sqlalchemy.exc

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

JELLYFIN_ART_TYPES = [
        'logo',
        'clearart',
        'disc',
        'poster',
        'backdrop',
        'banner',
        'thumb'
        ]

FANART_MAP = {
        # movies
        'hdmovielogo': 'logo',
        'hdmovieclearart': 'clearart',
        'moviedisc': 'disc',
        'movielogo': 'logo',
        'movieposter': 'poster',
        'movieart': 'clearart',
        'moviebackground': 'backdrop',
        'moviebanner': 'banner',
        'moviethumb': 'thumb',

        # tv
        'clearlogo': 'logo',
        'hdtvlogo': 'logo',
        'clearart': 'clearart',
        'showbackground': 'backdrop',
        'tvthumb': 'thumb',
        'seasonposter': 'poster',
        'seasonthumb': 'thumb',
        'hdclearart': 'clearart',
        'tvbanner': 'banner',
        #'characterart': 'clearart',
        'tvposter': 'poster',
        'seasonbanner': 'banner',
        }


RE_JELLYFIN_SEASON_DIR = re.compile(r'^Season \d+$', re.I)

def write_nfo(obj, nfo_path, fetch_fanart=True, dry_run=False):
    tmpfile = f'{nfo_path}.tmp'
    if os.path.exists(nfo_path):
        stat = os.stat(nfo_path)
        if stat.st_mtime > obj.updated.timestamp():
            return
    dirname = os.path.split(nfo_path)[0]
    os.makedirs(dirname, exist_ok=True)

    adbb.log.debug(f'Update nfo {nfo_path}')
    if dry_run:
        return
    if type(obj) == adbb.File:
        eps = obj.multiep
        anime = obj.anime
        with open(tmpfile, 'w', encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n')
            for ep in eps:
                episode = adbb.Episode(anime=anime, epno=ep)
                season, tvdb_ep = episode.tvdb_episode

                if season == 'a':
                    season = '1'
                elif season == 's':
                    season = '0'

                if type(tvdb_ep) == tuple:
                    tvdb_eps = [tvdb_ep[0]]
                elif '+' in tvdb_ep:
                    tvdb_eps = tvdb_ep.split('+')
                else:
                    tvdb_eps = [tvdb_ep]
                for tep in tvdb_eps:
                    root = ET.Element('episodedetails')
                    if anime.nr_of_episodes == 1:
                        orig_title = [x.title for x in anime.titles if x.lang == 'jpn' and x.titletype == 'official']
                        if orig_title:
                            e = ET.SubElement(root, 'originaltitle')
                            e.text = orig_title[0]
                        elif episode.title_kanji:
                            e = ET.SubElement(root, 'originaltitle')
                            e.text = episode.title_kanji
                        e = ET.SubElement(root, 'name')
                        e.text = anime.title
                    else:
                        if episode.title_romaji:
                            e = ET.SubElement(root, 'name')
                            e.text = episode.title_romaji
                        elif episode.title_eng:
                            e = ET.SubElement(root, 'name')
                            e.text = episode.title_eng
                        if episode.title_kanji:
                            e = ET.SubElement(root, 'originaltitle')
                            e.text = episode.title_kanji
                    if episode.aired:
                        e = ET.SubElement(root, 'year')
                        e.text = episode.aired.strftime('%Y')
                        e = ET.SubElement(root, 'aired')
                        e.text = episode.aired.strftime('%Y-%m-%d')
                    if episode.rating:
                        e = ET.SubElement(root, 'rating')
                        e.text = str(episode.rating)
                    e = ET.SubElement(root, 'season')
                    e.text = season
                    e = ET.SubElement(root, 'episode')
                    e.text = tep
                    e = ET.SubElement(root, 'uniqueid', attrib={'type': 'anidb', 'default': 'true' })
                    e.text = str(episode.eid)
                    etree = ET.ElementTree(element=root)
                    ET.indent(etree)
                    
                    etree.write(f, encoding='unicode', xml_declaration=False)
                    f.write('\n')

    elif type(obj) == adbb.Episode:
        movie = any([obj.tmdbid, obj.imdbid])
        anime = obj.anime
        if movie:
            rootname = 'movie'
        else:
            rootname = 'tvshow'

        with open(tmpfile, 'w', encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n')
            root = ET.Element(rootname)
            orig_title = [x.title for x in anime.titles if x.lang == 'jpn' and x.titletype == 'official']
            if orig_title:
                orig_title = orig_title[0]

            e = ET.SubElement(root, 'name')
            if not movie or anime.nr_of_episodes == 1:
                e.text = anime.title
            else:
                if orig_title and obj.title_kanji:
                    orig_title = f'{orig_title} - {obj.title_kanji}'
                else:
                    orig_title = f'{orig_title} - {obj.episode_number}'

                if obj.title_romaji:
                    e.text = f'{anime.title} - {obj.title_romaji}'
                elif obj.title_kanji:
                    e.text = f'{anime.title} - {obj.title_kanji}'
                else:
                    e.text = f'{anime.title} - {obj.title_eng}'
            if orig_title:
                e = ET.SubElement(root, 'originaltitle')
                e.text = orig_title
            if movie and obj.aired:
                e = ET.SubElement(root, 'year')
                e.text = obj.aired.strftime('%Y')
                e = ET.SubElement(root, 'aired')
                e.text = obj.aired.strftime('%Y-%m-%d')
            elif anime.air_date:
                e = ET.SubElement(root, 'year')
                e.text = anime.air_date.strftime('%Y')
                e = ET.SubElement(root, 'aired')
                e.text = anime.air_date.strftime('%Y-%m-%d')
            if obj.rating:
                e = ET.SubElement(root, 'rating')
                e.text = str(obj.rating)
            if not (movie and anime.nr_of_episodes > 1):
                e = ET.SubElement(root, 'uniqueid', attrib={'type': 'anidb', 'default': 'true' })
                e.text = str(obj.aid)
            if obj.tmdbid:
                e = ET.SubElement(root, 'uniqueid', attrib={'type': 'tmdb'})
                e.text = obj.tmdbid
            if obj.imdbid:
                e = ET.SubElement(root, 'uniqueid', attrib={'type': 'imdb'})
                e.text = obj.imdbid
            if obj.tvdbid:
                e = ET.SubElement(root, 'uniqueid', attrib={'type': 'tvdb'})
                e.text = obj.tvdbid
            etree = ET.ElementTree(element=root)
            ET.indent(etree)
            
            etree.write(f, encoding='unicode', xml_declaration=False)
            f.write('\n')

        art_dir = os.path.split(nfo_path)[0]
        if fetch_fanart:
            all_arts = { k: [] for k in JELLYFIN_ART_TYPES }
            if movie:
                arts = [x for x in anime.fanart if \
                        (obj.tmdbid and 'tmdb_id' in x and x['tmdb_id'] == obj.tmdbid) or \
                        (obj.imdbid and 'imdb_id' in x and x['imdb_id'] == obj.imdbid)]
            else:
                arts = [x for x in anime.fanart if \
                        (obj.tvdbid and 'thetvdb_id' in x and x['thetvdb_id'] == obj.tvdbid)]
            for entry in arts:
                for key, value in entry.items():
                    if key in FANART_MAP:
                        art_type = FANART_MAP[key]
                        urls = [x['url'] for x in value]
                        all_arts[art_type].extend(urls)
            for art_type, urls in all_arts.items():
                items = 1
                if art_type == 'backdrop':
                    items = 5
                random.shuffle(urls)
                chosen = urls[:items]
                for item, url in enumerate(chosen, start=1):
                    ext = url.rsplit('.', 1)[-1]
                    if item > 1:
                        fname = f'{art_type}{item}.{ext}'
                    else:
                        fname = f'{art_type}.{ext}'
                    tmpart = os.path.join(art_dir, f'.{fname}.tmp')
                    try:
                        with open(tmpart, 'wb') as f:
                            adbb.download_fanart(f, url)
                    except (urllib.error.HTTPError, urllib.error.URLError) as e:
                        adbb.log.error(f'Failed to download fanart at {url}: {e}')
                        os.remove(tmpart)
                        continue
                    os.rename(tmpart, os.path.join(art_dir, fname))
            if not all_arts['poster']:
                fname = 'poster.jpg'
                tmpart = os.path.join(art_dir, f'.{fname}.tmp')
                try:
                    with open(tmpart, 'wb') as f:
                        adbb.download_image(f, anime)
                    os.rename(tmpart, os.path.join(art_dir, fname))
                except (urllib.error.HTTPError, urllib.error.URLError) as e:
                    adbb.log.error(f'Failed to download anidb image for {anime}: {e}')
                    os.remove(tmpart)

    os.rename(tmpfile, nfo_path)

def create_anime_collection(
        anime,
        xml_path,
        movie_path=None,
        tv_path=None,
        anidb_path=None,
        name=None,
        exclude=[],
        include=[]):
    if not name:
        name = anime.title

    collection_dir = os.path.join(xml_path, name.replace('/', '⁄').lstrip('.'))
    collection_xml = os.path.join(collection_dir, 'collection.xml')
    if os.path.isfile(collection_xml):
        stat = os.stat(collection_xml)
        if stat.st_mtime > anime.updated.timestamp():
            return
    if include:
        relations = adbb.utils.get_related_anime([anime] + include, exclude=exclude)
    else:
        relations = adbb.utils.get_related_anime(anime, exclude=exclude)
    if len(relations) < 2:
        return

    adbb.log.debug(f'Will create collection for {len(relations)} entries related to {anime}')
    paths = [x for x in [movie_path, tv_path] if x]
    if anidb_path:
        paths.extend([os.path.join(anidb_path, 'Movies'),
                     os.path.join(anidb_path, 'Series')])

    os.makedirs(collection_dir, exist_ok=True)
    # Fanart
    all_arts = { k: [] for k in JELLYFIN_ART_TYPES }
    for series in relations:
        for arts in series.fanart:
            for key, value in arts.items():
                if key in FANART_MAP:
                    art_type = FANART_MAP[key]
                    urls = [x['url'] for x in value]
                    all_arts[art_type].extend(urls)
    for art_type, urls in all_arts.items():
        items = 1
        if art_type == 'backdrop':
            items = 5
        random.shuffle(urls)
        chosen = urls[:items]
        for item, url in enumerate(chosen, start=1):
            ext = url.rsplit('.', 1)[-1]
            if item > 1:
                fname = f'{art_type}{item}.{ext}'
            else:
                fname = f'{art_type}.{ext}'
            tmpfile = os.path.join(collection_dir, f'.{fname}.tmp')
            try:
                with open(tmpfile, 'wb') as f:
                    adbb.download_fanart(f, url)
            except (urllib.error.HTTPError, urllib.error.URLError) as e:
                adbb.log.error(f'Failed to download fanart at {url}: {e}')
                os.remove(tmpfile)
                continue
            os.rename(tmpfile, os.path.join(collection_dir, fname))
    if not all_arts['poster']:
        fname = 'poster.jpg'
        tmpfile = os.path.join(collection_dir, f'.{fname}.tmp')
        try:
            with open(tmpfile, 'wb') as f:
                adbb.download_image(f, anime)
            os.rename(tmpfile, os.path.join(collection_dir, fname))
        except urllib.error.HTTPError as e:
            adbb.log.error(f'Failed to download anidb image for {anime}: {e}')
            os.remove(tmpfile)


    # XML
    troot = ET.Element('Item')
    e = ET.SubElement(troot, 'LocalTitle')
    e.text = name
    if anime.air_date:
        e = ET.SubElement(troot, 'FirstAired')
        e.text = anime.air_date.strftime('%Y-%m-%d')
        e = ET.SubElement(troot, 'ProductionYear')
        e.text = anime.air_date.strftime('%Y')
    items = ET.SubElement(troot, 'CollectionItems')
    added_paths = []
    for a in relations:
        directories = []
        if a.tmdbid:
            if type(a.tmdbid) == list:
                for i in a.tmdbid:
                    directories.extend([os.path.join(x, f'adbb [tmdbid-{i}]') for x in paths])
            else:
                directories.extend([os.path.join(x, f'adbb [tmdbid-{a.tmdbid}]') for x in paths])
        if a.imdbid:
            if type(a.imdbid) == list:
                for i in a.imdbid:
                    directories.extend([os.path.join(x, f'adbb [imdbid-{i}]') for x in paths])
            else:
                directories.extend([os.path.join(x, f'adbb [imdbid-{a.imdbid}]') for x in paths])
        if a.tvdbid:
            directories.extend([os.path.join(x, f'adbb [tvdbid-{a.tvdbid}]') for x in paths])
        directories.extend([os.path.join(x, a.title.replace('/', '⁄').lstrip('.')) for x in paths])
        present_dirs = [d for d in directories if os.path.isdir(d)]
        for d in present_dirs:
            for root, dirs, files in os.walk(d):
                dirs[:] = []
                if not files:
                    continue
                if 'Movies' in root:
                    for f in [x for x in files if x.rsplit('.', 1)[-1].lower() in adbb.utils.SUPPORTED_FILETYPES]:
                        my_path = os.path.join(root, f)
                        if my_path in added_paths:
                            continue
                        i = ET.SubElement(items, 'CollectionItem')
                        e = ET.SubElement(i, 'Path')
                        e.text = os.path.join(root, f)
                        added_paths.append(my_path)
                else:
                        if root in added_paths:
                            continue
                        i = ET.SubElement(items, 'CollectionItem')
                        e = ET.SubElement(i, 'Path')
                        e.text = root
                        added_paths.append(root)

    etree = ET.ElementTree(element=troot)
    ET.indent(etree)

    adbb.log.info(f'Updating collection at {collection_xml}')
    with open(collection_xml, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="utf-8" standalone="yes" ?>\n')
        etree.write(f, encoding='unicode', xml_declaration=False)
        f.write('\n')

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
            '-o', '--collection-path',
            help="Path to jellyfin collection library, see JELLYFIN.md for details about creating collections",
            default=None
            )
    parser.add_argument(
            '-f', '--write-nfo',
            help="Write nfo-files with anidb data to linked libraries",
            action='store_true')
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

def jellyfin_anime_sync():
    import jellyfin_apiclient_python.exceptions
    args = get_jellyfin_anime_sync_args()

    log = adbb.utils.get_command_logger(debug=args.debug, syslog=args.use_syslog)
    reinit_adbb=True
    failures=0
    signal.signal(signal.SIGTERM, adbb.utils.signal_handler)
    signal.signal(signal.SIGHUP, adbb.utils.signal_handler)
    signal.signal(signal.SIGINT, adbb.utils.signal_handler)
    signal.signal(signal.SIGUSR1, adbb.utils.signal_handler)

    adbb.utils.EXTRAS_DIRS = JELLYFIN_SPECIAL_DIRS


    def link_to_library(path=None, adbb_file=None):
        if not (path and adbb_file):
            return
        d = os.path.split(path)[0]
        pdir = os.path.split(d)[-1]
        if pdir in JELLYFIN_SPECIAL_DIRS:
            return

        anime = adbb_file.anime
        episode = adbb_file.episode
        tmdbid = episode.tmdbid
        imdbid = episode.imdbid
        movie = any([tmdbid, imdbid])

        aname = anime.title.replace('/', '⁄').lstrip('.')
        ext = path.rsplit('.', 1)[-1]
        season, epno = episode.tvdb_episode

        if season == 's':
            season = '0'

        if (movie or (anime.nr_of_episodes == 1 and episode.episode_number == "1")) and adbb_file.part:
            partstr = f'-part{adbb_file.part}'
        else:
            partstr = ""

        if args.anidb_library:
            # For anidb scraper 1 episode == movie
            if adbb_file.anime.nr_of_episodes == 1:
                exclusive_dir = os.path.join(args.anidb_library, 'Movies', aname)
            else:
                exclusive_dir = os.path.join(args.anidb_library, 'Series', aname)
        else:
            exclusive_dir = None

        # Jellyfin can't handle specials in absolute order (yet?)
        # https://github.com/jellyfin/jellyfin-plugin-tvdb/pull/92
        if season and season not in ["1", "a"] and anime.tvdbid:
            if adbb.anames.tvdbid_has_absolute_order(anime.tvdbid):
                epno = None

        if tmdbid and args.moviedb_library:
            d = os.path.join(args.moviedb_library, f'adbb [tmdbid-{tmdbid}]')
            linkname = os.path.basename(path)
            link = os.path.join(d, linkname)
            adbb.utils.link_to_directory(path, link, exclusive_dir=exclusive_dir, dry_run=args.dry_run)
            if args.write_nfo:
                write_nfo(episode, os.path.join(d, 'movie.nfo'), dry_run=args.dry_run)
        elif imdbid and args.moviedb_library:
            d = os.path.join(args.moviedb_library, f'adbb [imdbid-{imdbid}]')
            linkname = os.path.basename(path)
            link = os.path.join(d, linkname)
            adbb.utils.link_to_directory(path, link, exclusive_dir=exclusive_dir, dry_run=args.dry_run)
            if args.write_nfo:
                write_nfo(episode, os.path.join(d, 'movie.nfo'), dry_run=args.dry_run)
        elif anime.tvdbid and args.tvdb_library and epno:
            if type(epno) is tuple:
                partstr = f'-part{epno[1]}'
                epno = epno[0]
            elif '+' in epno:
                epno = epno.split('+')
            d = os.path.join(args.tvdb_library, f'adbb [tvdbid-{anime.tvdbid}]')
            if type(epno) is list:
                if epno[0] == '1' and args.write_nfo and season in ['1', 'a']:
                    write_nfo(episode, os.path.join(d, 'tvshow.nfo'), dry_run=args.dry_run)
            elif epno == '1' and args.write_nfo and season in ['1', 'a']:
                write_nfo(episode, os.path.join(d, 'tvshow.nfo'), dry_run=args.dry_run)

            epnos = adbb_file.multiep
            if epnos[0] != epnos[-1]:
                last_ep = adbb.Episode(anime=anime, epno=epnos[-1])
                last_season, last_epno = last_ep.tvdb_episode
                if type(last_epno) is tuple and last_epno[0] == epno:
                        partstr=""
                elif last_epno and '+' in last_epno:
                    epno = f"{epno}-{last_epno.split('+')[-1]}"
                elif last_epno:
                    epno = f'{epno}-{last_epno}'
                else:
                    adbb.log.warning(f'No TVDB episode mapping for {last_ep}')
            elif type(epno) is list:
                epno = f'{epno[0]}-{epno[-1]}'
            if adbb.anames.tvdbid_has_absolute_order(anime.tvdbid):
                linkname = f"{aname} - {epno}{partstr}.{ext}"
            else:
                linkname = f"{aname} S{season}E{epno}{partstr}.{ext}"
            link = os.path.join(d, linkname)
            adbb.utils.link_to_directory(path, link, exclusive_dir=exclusive_dir, dry_run=args.dry_run)
            if args.write_nfo:
                basename = linkname.rsplit('.', 1)[0]
                write_nfo(adbb_file, os.path.join(d, f'{basename}.nfo'), dry_run=args.dry_run)

        elif exclusive_dir:
            linkname = os.path.basename(path)
            link = os.path.join(exclusive_dir, linkname)
            adbb.utils.link_to_directory(path, link, dry_run=args.dry_run)
        return


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
                files[:] = [ x for x in files if x.rsplit('.')[-1] in adbb.utils.SUPPORTED_FILETYPES ]
                if files:
                    full_path_list.append(root)
            random.shuffle(full_path_list)

            if args.sleep_delay:
                delay = args.sleep_delay
            elif args.sleep_delay is None and args.repeat:
                delay = max(min((22*60*60-len(full_path_list)*8.9)/len(full_path_list), 300), 0)
            else:
                delay = 0
            adbb.log.info(f"Starting sync of {len(full_path_list)} paths with {delay} seconds delay between paths.")

            for path in full_path_list:
                iterations += 1
                for root, dirs, files in os.walk(path):
                    dirs[:] = []
                    files[:] = [ x for x in files if x.rsplit('.')[-1] in adbb.utils.SUPPORTED_FILETYPES ]
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
                        adbb.utils.arrange_files(
                                [os.path.join(root, f) for f in files],
                                target_dir=pdir,
                                dry_run=args.dry_run,
                                callback=link_to_library)

                        if args.staging_path:
                            files = adbb.utils.create_filelist([args.staging_path])
                            adbb.utils.arrange_files(
                                    files, target_dir=args.path,
                                    dry_run=args.dry_run,
                                    check_previous=args.check_previous_episode,
                                    check_complete=args.check_series_complete,
                                    callback=link_to_library)

                    failures = 0
                    runtime = datetime.datetime.now()-starttime
                    adbb.utils.status_msg = f'{iterations}/{len(full_path_list)} paths processed in {str(runtime)}.'
                    time.sleep(delay)
            
            # Clean up broken symlinks/empty dirs
            links = {}
            for path in [args.tvdb_library, args.moviedb_library]:
                if not path:
                    continue
                for root, dirs, files in os.walk(path):
                    dirs[:] = [x for x in dirs if x.startswith('adbb [')]
                    adbb.utils.remove_dir_if_empty(root)
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
                        adbb.utils.remove_dir_if_empty(root)
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

            # create and update collections
            if args.collection_path:
                conf_file = os.path.join(args.collection_path, '.adbb.ini')
                collections = []
                conf = configparser.ConfigParser()
                try:
                    with open(conf_file, 'r', encoding='utf-8') as f:
                        conf.read_file(f)
                    collections = conf.sections()
                except OSError:
                    adbb.log.warning(f'Could not open ini configuration at {conf}')

                for collection in collections:
                    try:
                        anime = int(collection)
                    except ValueError:
                        anime = collection
                    name = conf.get(collection, 'name', fallback=None)
                    exclude = conf.get(collection, 'exclude', fallback=[])
                    include = conf.get(collection, 'include', fallback=[])
                    if exclude:
                        exclude = [adbb.Anime(int(x)) for x in exclude.split(',')]
                    if include:
                        include = [adbb.Anime(int(x)) for x in include.split(',')]
                    else:
                        include = None
                    create_anime_collection(adbb.Anime(anime),
                                            args.collection_path,
                                            movie_path=args.moviedb_library,
                                            tv_path=args.tvdb_library,
                                            anidb_path=args.anidb_library,
                                            name=name,
                                            exclude=exclude,
                                            include=include)

            runtime = datetime.datetime.now()-starttime
            log.info(f"Completed sync in {str(runtime)}")
        except (sqlalchemy.exc.OperationalError, jellyfin_apiclient_python.exceptions.HTTPException) as e:
            if not failures:
                failures = 1
            else:
                failures = min(failures*2, 120)
            adbb.log.warning(f"Network error, will retry in {failures} minutes: {e}")
            adbb.close()
            reinit_adbb=True
            time.sleep(failures*60)

        if not (args.repeat or failures):
            break

    adbb.close()

