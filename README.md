# adbb
Object Oriented UDP Client for AniDB, very loosly based on adba.

I created this mainly to be able to add new files to my "mylist" on anidb when I add them to my local collection.
As I tend to rip my own files and have no intention of spreading them to a wide audience, I needed to add these as 
"generic" files to anidb. And manual work is always less fun then automating said work...

As the anidb UDP API enforces a very slow rate of requests, this implementation caches all information requested from
anidb and uses the cache whenever possible. The cache is stored in mysql (or any other sqlalchemy-compatible
database). For how long does it cache? It depends! Shortest caching period is one day, after that some not very 
inteligent algorithm will add some probability score which is used to decide if the cache should be updated or not. 
It's untuned and will probably be difficult to get right for all use cases... I'm listening to any ideas about how to 
make this better :)

Also, you can always force an update of the cache by using the objects update() method.

The Anime title search is implemented using the animetitles.xml file hosted at anidb. It is automatically downloaded
and stored localy (for now hardcoded to /var/tmp/adbb/animetitles.xml.gz). This animetitles file is also cached for 7 
days (using mtime to calculate age) and then is automatically updated. You can of course "update" it manually by 
simply removing the cached file :)

## Requirements
* recent python
* sqlalchemy
* sqlalchemy-compatible database:
  * mysql is tested
  * sqlite is tested
  * postgresql should probably work


## Usage
```Python
import adbb

user="<anidb-username>"
pwd="<anidb-password>"

sql="sqlite:///adbb.db"

# initialize backend
adbb.init(user, pwd, sql, debug=True)

# anime object can be created either with a name or anime ID
anime = adbb.Anime("Kemono no Souja Erin")
#anime = adbb.Anime(6187)

# this will print "Kemono no Souja Erin has 50 episodes and is a TV Series"
print("{} has {} episodes and is a {}".format(anime.title, anime.nr_of_episodes, anime.type))

# Episode object can be created either using anime+episode number or the anidb eid
# anime can be either aid, title or Anime object
episode = adbb.Episode(anime=anime, epno=5)
#episode = adbb.Episode(eid=96461)

# this will print "'Kemono no Souja Erin' episode 5 has title 'Erin and the Egg Thieves'"
print("'{}' episode {} has title '{}'".format(episode.anime.title, episode.episode_number, episode.title_eng))

# file can either be created with a local file, an anidb file id (fid) or 
# using Anime and Episode
file = File(path="/media/Anime/Series/Kemono no Souja Erin/[winterbird] Kemono no Souja Erin - 05 [8EEAA040].mkv")
#file = File(fid=<some-fid>)
#file = File(anime=anime, episode=episode)

# note that most of the time this will work even if we use a file that is not in the anidb database
# will print "'<path>' contains episode 5 of 'Kemono no Souja Erin'. Mylist state is 'on hdd'"
print("'{}' contains episode {} of '{}'. Mylist state is '{}'".format(file.path, file.episode.episode_number, file.anime.title, file.mylist_state))
```

## Reference 

### Anime object
```python
Anime(init)
```
'init' can be either a title or aid. Titles are searched in the animetitles.xml file using fuzzy text matching
(implemented using difflib). Only a single Anime is created, using the best title match. Note that some titles are 
ambigious. A search for 'Ranma', for example, can return either the series 'Ranma 1/2' (which has "Ranma" as a 
synonym) or 'Ranma 1/2 Nettou Hen' which has "Ranma" as an official title). 

#### Attributes
* aid - AniDB anime ID
* titles - A list of all titles for this Anime
* title - main title of this Anime
* updated - datetime when information about this Anime was last fetched from AniDB

The following attributes as returned from the AniDB API
* year
* type
* nr_of_episodes
* highest_episode_number
* special_ep_count
* air_date
* end_date
* url
* picname
* rating
* vote_count
* temp_rating
* temp_vote_count
* average_review_rating
* review_count
* is_18_restricted
* ann_id
* allcinema_id
* animenfo_id
* anidb_updated
* special_count
* credit_count
* other_count
* trailer_count
* parody_count

### Episode
```python
Episode(anime=None, epno=None, eid=None)
```
Episode object can be created by specifying both anime and epno, or using just eid. anime can be either a title, aid
or an Anime object. epno should be either a string or int representing the episode number. eid should be an int.

#### Attributes
* eid - AniDB episode ID
* anime - Anime object for the anime series that this episode belongs to
* episode_number - The episode number (note that this is a string)
* updated - datetime when information about this episode was last fetched from AniDB

The following attributes as returned from the AniDB API
* length
* rating
* votes
* title_eng
* title_romaji
* title_kanji
* aired
* type

### File
```python
File(path=None, fid=None, anime=None, episode=None)
```
File object requires either path, fid, or anime and episode to be set. When setting anime and episode this file will
either be a generic file, or the file you have in your mylist for this anime and episode. fid is obviously the AniDB
file ID. Path is the fun one.

When a path is specified the library will first check the size and ed2k-hash to the AniDB database. If the file exists
there this will obviously represent that file. If the file *doesn't* exist in the AniDB databse the library will try
to figure out which anime and episode this file represents. The episode number is guessed from the filename by using 
some regex. If no episode number is found, adbb will check if the Anime only has a single episode; and if that is the
case it will assume that the file has episode number '1'. The Anime title is guessed from the parent directory if there 
is a good-enough match in the animetitles- file, otherwise it's guessed from the filename. For details, check
_guess_anime_ep_from_file() and _guess_epno_from_filename() in the File class in animeobjs.py, and get_titles() in 
anames.py.


#### Functions
The File object has some functions for managing the file in mylist.

```python
update_mylist(state=None, watched=None, source=None, other=None)
remove_from_mylist()
```

The update_mylist() function can be used both to add and to edit a file in mylist. state can be one of 'unknown', 
'on hdd', 'on cd' or 'deleted'. watched can be either True, False or an datetime object describing when it was watched.

#### Attributes
* anime - Anime object for which anime this file contains
* episode - Episode object for which episode this file contains
* multiep - List of episode numbers this file contains. The episode number parsing supports multiple episodes, but
  fetching from AniDB does not so this is not reliable and I'm not really sure what to do with it...
* fid - File ID from AniDB
* path - Full Path to this file (if created with a path)
* size - file size in bytes
* ed2khash - ed2k-hash, because AniDB still uses it...
* updated - datetime when information about this file was last fetched from AniDB

The following attributes as returned from the AniDB API
* lid
* gid
* is_deprecated
* is_generic
* crc_ok
* file_version
* censored
* length_in_seconds
* description
* aired_date
* mylist_state
* mylist_filestate
* mylist_viewed
* mylist_viewdate
* mylist_storage
* mylist_source
* mylist_other

### Group
```python
Group(name=None, gid=None)
```
Group object requires either a name (can be either short or long name) or gid.
A group created with a name is always considered valid, and will be saved to the database even if the name does not
represent a group in AniDB. In that case both the name and the short name will be set to the given name, and all
other atributes will be empty.

#### Attributes
* updated - datetime when information about this file was last fetched from AniDB

The following attributes as returned from the AniDB API
* gid
* rating
* votes
* acount
* fcount
* name
* short
* irc_channel
* irc_server
* url
* picname
* founded
* disbanded
* dateflag
* last_release
* last_activity

## TODO:
In no particular order:
* add support for more anidb-commands (anime description would be nice...)
* make cache invalidation smarter
* add better/more error checking, right now it will probably crash at most errors it will encounter :)
* make multiprocess/multithreading-safe
* any other feature request?
