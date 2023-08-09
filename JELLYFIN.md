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
  - The seasons/relations thing? No idea.

I decided to solve the three first points using this adbb python library since the groundwork was already done, and because there is a third awesome project, [Anime-Lists](https://github.com/Anime-Lists/anime-lists), that makes it possible.

## The jellyfin_anime_sync tool

This tool is installed with the adbb library and has three main purposes:

  - Sync mylist status, including watched status, with AniDB.
  - Maintain 4 (or 6, depending on how you count) different libraries:
    - "main" library organized after AniDB metadata,  this is where all the actual files are located. If it contains the subfolders "Movies" and "Series" the files will be sorted accordingly. Not to be added to jellyfin.
    - "TVDB" library organized after TVDB mappings in Anime-Lists, use with [jellyfin-plugin-tvdb](https://github.com/jellyfin/jellyfin-plugin-tvdb)
    - "MOVIEDB" library organized after TMDB and IMDB mappings in Anime-Lists, use with the built in TMDB scraper
    - "ANIDB" library organized after AniDB metadata, but contains only files that could not be mapped to TVDB or MOVIEDB libraries, contains sub-libraries "Movies" and "Series" use with the [AniDB-plugin](https://github.com/jellyfin/jellyfin-plugin-anidb)
  - Import new files to the libraries.

Some important notes:

  - jellyfin_anime_sync has an additional dependency on [jellyfin-apiclient-python](https://github.com/jellyfin/jellyfin-apiclient-python)
  - The tool is only tested on Linux
  - The tool is designed to eventually get the job done. For my ~3000 series it takes about 7 hours to do a complete sync, and that's when the cache is well maintained. The tool will also do exponential backoff if there is a timeout communicating with the API or if the [API tells it to](https://wiki.anidb.net/UDP_API_Definition#General) (starting with 30 minutes).
  - When you first sync/import your library, and don't have a populated cache, the tool will need to do lots of API requests and if you have a large library you may get IP banned from the AniDB UDP API and the tool will crash or hang (all according to the [API specification](https://wiki.anidb.net/UDP_API_Definition#General)). If this happens you should wait at least 24 hours before retrying the import. If you're still banned after that you should probably ask the staff in the [AniDB forum](https://anidb.net/forum/19/thread) and let me know if there is any need for changes in this library. To avoid bans you should probably use the `--sleep-delay` option during inital import. This parameter introduces a sleep between each imported series, reducing the load on the API but makes the sync slower. Setting this parameter to 300 (5 minutes) will give you about 280 series synced per 24 hours. I don't know if that's enough to avoid bans, but I think that's a good starting point.
  - The naming schemas for files is hardcoded. There are many pitfalls in file naming, but I'm open for PRs for templating this as long as you manage to avoid those :slightly_smiling_face:
  - Only one file per episode/movie is supported, variants/versions of the same episode/movie will not appear in the TVDB or MOVIEDB libraries and adbb has trouble with multiple file (mylist-)entries for the same episode.
  - The path to the main library must be given as the "real" absolute path (ie. no symlinks along the way)
  - On first run, you probably should use the `--no-watched` flag unless you want to mark everything in your anidb mylist as unwatched (since the files are not added to the jellyfin library yet during the first run it will treat everything as unwatched).
  - This tool will rename, and possibly move around, the files in the original library, to get a preview of what would be done you can run the included `arrange_anime` tool on the path with the `--dry-run` flag. Run `arrange_anime --help` for more help.

### Example usage
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
  --sleep-delay 300 \
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
When files are detected in the `staging-path` they will be identified by adbb, imported to the main library and linked to from the proper jellyfin library. This path is polled after every other directory is traversed (so roughly every `sleep-delay` + a few seconds). I would recommend to add files atomicaly to this path (first place them somewhere else on the same filesystem and then use `mv` to move them to the staging directory). To help adbb to identify the file it should preferably be added to a sub-directory named after the anidb title.

### Authentication

Although the tool support command line parameters for anidb/jellyfin/database credentials, it's highly recommended to use a [`.netrc`](https://everything.curl.dev/usingcurl/netrc) file instead.

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
