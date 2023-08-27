# Jellyfin and AniDB

[AniDB](https://anidb.net/) is awesome. I've been tracking my Anime-habits for almost two decades using their database and have built up a sizeable collection.

The [Jellyfin](https://jellyfin.org/) project is awesome. An open-source self-hosted private streaming service is the perfect tool to enjoy Anime wherever you might be.

Unfortunately these two awesome projects don't play very well together. Jellyfin have a [AniDB-plugin](https://github.com/jellyfin/jellyfin-plugin-anidb) that does a decent job fetching metadata, and primary image, from AniDB, but there are still problems:

  - There is no support for syncing AniDB mylist data with jellyfin (The third party [jellyfin-ani-sync-plugin](https://github.com/vosmiic/jellyfin-ani-sync) supports some other anime lists, but AniDB-support is [not likely](https://github.com/vosmiic/jellyfin-ani-sync/issues/38) to be added).
  - AniDB will IP ban you from the API for at least 24 hours if you try to fetch metadata for more then a few hundered series (no one but the AniDB people know for sure where the limit is).
  - AniDB does not have any aditional artwork except for the primary poster (reasonable for what they want to be, a bit boring for jellyfin users)
  - Jellyfin is built on the traditional idea that every TV-series is segmented in "seasons", while AniDB instead describes relations between series/seasons and movies which I very much prefer.

So what can be done about those things?

  - Mylist? Update manually or use an external tool.
  - IP Bans? There are a few fancy ways to handle it, but it's there for a reason: AniDB doesn't want heavy usage of the API. The only polite solution is to avoid the API when possible.
  - Artwork? Use [jellyfin-plugin-tvdb](https://github.com/jellyfin/jellyfin-plugin-tvdb) in combination with [jellyfin-plugin-fanart](https://github.com/jellyfin/jellyfin-plugin-fanart). But bewere, if you add the same tvdbid to multiple series [jellyfin will be confused about the episodes watched status](https://github.com/jellyfin/jellyfin/issues/8485).
  - The seasons/relations thing? It's not perfect, but you can add related series/movies to a [collection](#collections).

That was the main problem I wanted to solve with this tool, and fortunately there is a third awesome project, [Anime-Lists](https://github.com/Anime-Lists/anime-lists), that makes it possible.

## The jellyfin_anime_sync tool

This tool is installed with the adbb library and has three main purposes:

  - Sync mylist status, including watched status, with AniDB.
  - Import new files to the libraries.
  - Maintain 5 (or 6, or 7, depending on how you count) different libraries:
    - "main" library organized after AniDB metadata,  this is where all the actual files are located. If it contains the subfolders "Movies" and "Series" the files will be sorted accordingly. Can be added to jellyfin if you only want to use the AniDB plugin.
    - "TVDB" library organized after TVDB mappings in Anime-Lists, use with [jellyfin-plugin-tvdb](https://github.com/jellyfin/jellyfin-plugin-tvdb)
    - "MOVIEDB" library organized after TMDB and IMDB mappings in Anime-Lists, use with the built in TMDB scraper
    - "ANIDB" library organized after AniDB metadata, but contains only files that could not be mapped to TVDB or MOVIEDB libraries, contains sub-libraries "Movies" and "Series" use with the [AniDB-plugin](https://github.com/jellyfin/jellyfin-plugin-anidb)
    - "collection"-meta library with related series/movies 

Some important notes:

  - jellyfin_anime_sync has an additional dependency on [jellyfin-apiclient-python](https://github.com/jellyfin/jellyfin-apiclient-python)
  - The tool is only tested on Linux
  - The tool is designed to eventually get the job done. For my ~3000 series it takes about 7 hours to do a complete sync, and that's when the cache is well maintained. The tool will also do exponential backoff if there is a timeout communicating with the API or if the [API tells it to](https://wiki.anidb.net/UDP_API_Definition#General) (starting with 30 minutes).
  - When you first sync/import your library, and don't have a populated cache, the tool will need to do lots of API requests and if you have a large library you may get IP banned from the AniDB UDP API and the tool will crash or hang (all according to the [API specification](https://wiki.anidb.net/UDP_API_Definition#General)). If this happens you should wait at least 24 hours before retrying the import. If you're still banned after that you should probably ask the staff in the [AniDB forum](https://anidb.net/forum/19/thread) and let me know if there is any need for changes in this library. Also read the First run section below.
  - The naming schemas for files is hardcoded. There are many pitfalls in file naming, but I'm open for PRs for templating this as long as you manage to avoid those :slightly_smiling_face:
  - Only one file per episode/movie is supported, variants/versions of the same episode/movie will not appear in the TVDB or MOVIEDB libraries and adbb has trouble with multiple file (mylist-)entries for the same episode.
  - The path to the main library must be given as the "real" absolute path (ie. no symlinks along the way)
  - The only situation where this tool will remove anything from your mylist is when it finds a new file for an episode already in your mylist; In this case it will remove the old mylist entry and replace it with the new file.

### First run
When trying out this tool for the first time, make sure to read these instruction before running it:

  1. Decide how you want to use the tool and figure out what flags to use (see examples below and `jellyfin_anime_sync --help`):
    - Managing jellyfin libraries for the anidb scraper
    - Managing jellyfin libraries for the tvdb/tmdb scrapers
    - Just syncing mylist and watched status
    - Syncing mylist and managing libraries
  2. If you haven't used adbb before, or don't have a fresh cache database, make sure to set `--sleep-delay` to at least a couple of minutes (300 is probably a good start) to avoid API bans. If you get banned anyway, do *not* restart
     the tool until at least 24 hours has passed, and make sure to bump the sleep delay a couple of more minutes.
  3. Run with the `--dry-run` flag first and check the output/logs that the tool does what you expect it to.
  4. Be patient, for a resonable sized library it will probably take at least 24 hours, and possibly weeks, to run the first iteration. You can send the `USR1` signal with `killall -USR1 jellyfin_anime_sync` to make it log the current status.
     You can also use `--debug` to get *very* verbose information about what's going on.
  5. If you decide to use this tool for managing the jellyfin libraries, make sure to use the `--no-watched` flag until the jellyfin libraries has been populated and you've updated watched status in jellyfin.

### Aborting
If you want to stop jellyfin_anime_sync it is safe to use CTRL+C or kill (with SIGTERM); the signal handler will properly log out from the API and exit.

### Example usage - Managing libraries
```
jellyfin_anime_sync \
  --sql-url "sqlite:///home/user/.adbb.db" \
  --authfile /home/user/.netrc \
  --jellyfin-url "https://my.jellyfin.server" \
  --rearrange \
  --tvdb-library /var/lib/jellyfin/libraries/TV\ Series/ \
  --moviedb-library /var/lib/jellyfin/libraries/Movies/ \
  --anidb-library /var/lib/jellyfin/libraries/Anime \
  --staging-path /media/to_import/ \
  /media/Anime
```
In this example you have your main media file library under /media/Anime, possibly (preferably) with the subdirectories /media/Anime/Movies and /media/Anime/Series:
```
/media/Anime/Movies/Clover/Clover [youtube].mkv
/media/Anime/Movies/Tenki no Ko/Tenki no Ko [BD].mkv
...
/media/Anime/Series/Aa! Megami-sama! Sorezore no Tsubasa/Aa! Megami-sama! Sorezore no Tsubasa S0E1 - Aa! Sorezore no Unmei! [DVD].avi
/media/Anime/Series/Aa! Megami-sama! Sorezore no Tsubasa/Aa! Megami-sama! Sorezore no Tsubasa S0E2 - Aa! Suki wa Kokoro o Yusaburu Uta! [DVD].avi
/media/Anime/Series/Aa! Megami-sama! Sorezore no Tsubasa/Aa! Megami-sama! Sorezore no Tsubasa S1E01 - Aa! Negai yo, Mou Ichido! [DVD].avi
/media/Anime/Series/Aa! Megami-sama! Sorezore no Tsubasa/Aa! Megami-sama! Sorezore no Tsubasa S1E02 - Aa! Nayameru Fukushuu no Joou-sama! [DVD].avi
...
```
These will be linked to from the TVDB, MOVIEDB and ANIDB media libraries:
```
# Tenki no Ko is mapped to a IMDB entry and is added to the MOVIEDB media library
/var/lib/jellyfin/libraries/Movies/adbb [imdbid-tt9426210]/Tenki no Ko [BD].mkv -> /media/Anime/Movies/Tenki no Ko/Tenki no Ko [BD].mkv
# Clover does not have a matching TVDB/TMDB/IMDB entry and is added to the Anime Movie library
/var/lib/jellyfin/libraries/Anime/Movies/Clover/Clover [youtube].mkv' -> '/media/Anime/Movies/Clover/Clover [youtube].mkv'
...
# Aa! Megami-sama! Sorezore no Tsubasa is mapped to the second season of the TVDB series
'/var/lib/jellyfin/libraries/TV Series/adbb [tvdbid-78920]/Aa! Megami-sama! Sorezore no Tsubasa S2E1.avi' -> '/media/Anime/Series/Aa! Megami-sama! Sorezore no Tsubasa/Aa! Megami-sama! Sorezore no Tsubasa S1E01 - Aa! Negai yo, Mou Ichido! [DVD].avi'
'/var/lib/jellyfin/libraries/TV Series/adbb [tvdbid-78920]/Aa! Megami-sama! Sorezore no Tsubasa S2E2.avi' -> '/media/Anime/Series/Aa! Megami-sama! Sorezore no Tsubasa/Aa! Megami-sama! Sorezore no Tsubasa S1E02 - Aa! Nayameru Fukushuu no Joou-sama! [DVD].avi'
...
'/var/lib/jellyfin/libraries/TV Series/adbb [tvdbid-78920]/Aa! Megami-sama! Sorezore no Tsubasa S2E23.avi' -> '/media/Anime/Series/Aa! Megami-sama! Sorezore no Tsubasa/Aa! Megami-sama! Sorezore no Tsubasa S0E1 - Aa! Sorezore no Unmei! [DVD].avi'
'/var/lib/jellyfin/libraries/TV Series/adbb [tvdbid-78920]/Aa! Megami-sama! Sorezore no Tsubasa S2E24.avi' -> '/media/Anime/Series/Aa! Megami-sama! Sorezore no Tsubasa/Aa! Megami-sama! Sorezore no Tsubasa S0E2 - Aa! Suki wa Kokoro o Yusaburu Uta! [DVD].avi'
```
When files are detected in the `staging-path` they will be identified by adbb, imported to the main library and linked to from the proper jellyfin library. This path is polled after each other directory is traversed (so roughly every `sleep-delay` + a few seconds). I would recommend to add files atomicaly to this path (first place them somewhere else on the same filesystem and then use `mv` to move them to the staging directory). To help adbb to identify the file it should preferably be added to a sub-directory named after the anidb title.

if the `--Xdb-library` arguments are obmitted only the main library will be sorted and can be used with the AniDB Jellyfin plugin. To only sync mylist state you obmit the `--rearrange` flag; see example below.

### Example usage - Syncing mylist/watched status

```
jellyfin_anime_sync \
  --sql-url "sqlite:///home/user/.adbb.db" \
  --authfile /home/user/.netrc \
  --jellyfin-url "https://my.jellyfin.server" \
  /media/Anime
```
In this example no file will be renamed, the tool will just search your jellyfin library for files where realpath is under `/media/Anime` and update mylist and watched status for those (files located anywhere else is ignored).
This should work with most naming formats.

### Example usage - "daemon"-mode
```
jellyfin_anime_sync \
  --sql-url "sqlite:///home/user/.adbb.db" \
  --authfile /home/user/.netrc \
  --jellyfin-url "https://my.jellyfin.server" \
  --rearrange \
  --tvdb-library /var/lib/jellyfin/libraries/TV\ Series/ \
  --moviedb-library /var/lib/jellyfin/libraries/Movies/ \
  --anidb-library /var/lib/jellyfin/libraries/Anime \
  --staging-path /media/to_import/ \
  --repeat \
  --use-syslog \
  /media/Anime
```
With the `--repeat` flag `jellyfin_anime_sync` will not exit after traversing the Anime library, but will automatically restart and traverse the library again. By default in `--repeat`-mode the tool will automatically set a sleep delay between 0 and 300 seconds depending on the size of the media library. It's roughly tuned to do a complete run in about 22 hours with a fresh cache.

`--use-syslog` can be used to make the tool log messages to syslog instead of stdout/err. You should probably monitor this log for any WARNING, ERROR or CRITICAL messages.

It's not a true daemon as it will run in the foreground. No init-script or systemd-unit is provided. It can also be run from cron, but you should make sure to just run one instance at a time.

### Authentication

Although the tool support command line parameters for anidb/jellyfin/database credentials, it's highly recommended to use a [`.netrc`](README.md#netrc) file instead.

### Filenames

The filenames used are hardcoded for now. The naming schema for main and anidb libraries looks roughly like this (including series directory):
```
# Multi-episode entries, season is 1 for regular episodes and 0 for specials
# tag is group name if the file is registered for a group in anidb, whatever is in brackets in the beginning or end of the original file name, or "unknown" if no tag is found.
"{anime.title}/{anime.title} S{season}E{episode.episode_number} - {episode.title} [{tag}].{ext}"

# Single episode entries (Movies etc.)
"{anime.title}/{anime.title} [{tag}].{ext}"
```
For the TVDB/MOVIEDB libraries the naming schema is simplified:
```
# For titles which has a TVDB mapping
"adbb [tvdbid-{tvdb.id}]/{anime.title} S{tvdb.season}E{tvdb.episode_number}.{ext}"
# For titles which has a TMDB/IMDB mapping, source is either tmdb or imdb.
"adbb [{source}id-{source.id}]/{original filename}"
```
There is some special handling of multiep/partsfiles, but this is roughly how it will look. The idea is that the name in the main library should be mostly for human consumption (but also parseable by jellyfin, kodi or whatever media center you use); you should understand exactly what is in the file by reading it, while the TVDB/MOVIEDB names are just for Jellyfin to easily parse.

### Collections
The tool has an advanced, optional, feature to autmatically create collections of related series. If you have enabled [fanart.tv integration](README.md#fanart) random fanart from all included series/movies will automatically be downloaded.

1. Create a directory on your filesystem where you want to store the collection metadata
2. Create a new library in jellyfin. Leave the "Content type" field empty, but give it a good name (for example "Anime Collections")
3. Create a configuration file named `.adbb.ini` in the collections directory; see the configuration description below.
4. Run jellyfin-anime-sync and include the option `--collection-path /path/to/collections`. The collections are created in the last step of the sync, once it has sorted all files/links in the libraries.

The configuration file is a simple `.ini` file and it must be called `.adbb.ini` and be located in the root of the collections directory.
```ini
## The section header is the name or ID of the "main" series in the collection.
## Unless otherwise specified the name of this anime will also be the name of the collection
##
## Valid configuration keys per section:
## name - Custom name for the collection instead of the name of the "main" series
## exclude - comma-separated list of IDs that should not be considered part of this collection.
##           The dependency graphs on anidb makes these easy to identify.

# Create a collection containing all series and movies related to Boku no Hero Academia
[Boku no Hero Academia]

# Create a collection named CLAMP containing all series and movies in the CLAMP multiverse
# Using Tsubasa Chronicles as main series (but really... any would do)
[27339]
name=CLAMP

# Create a collection for Lupin III, but exclude the crossovers that otherwise would drag in
# all of Meitantei Conan and Cat's Eye as well. 
[Lupin Sansei]
exclude=6432,17634

# The include key is also allowed to include multiple dependency trees
[377]
name=Rumiko Takahashi Collection
include=288,4576,817,144,847,10936
```

### nfo-creation
The tool has optional support to create basic nfo files for series, movies and episodes. The metadata provided is limited to what can be fetched from the UDP API (excluding anime description, because I can't bring myself to implement that).
If [fanart](README.md#fanart) is enabled, random fanart will be downloaded to the series/movies directory. To enable nfo-creation, provide the `--write-nfo`-flag to your command line. This metadata information is provided:
  * title (romaji if available, english as fallback)
  * originaltitle (kanji)
  * year
  * air date
  * rating
  * anidb ID
