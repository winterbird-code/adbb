#!/usr/bin/env python
from os import environ, path

try:
    from setuptools import setup
except ImportError:
    from distutils import setup

import adbb

version = float(adbb.anidb_client_version)/10
readme = path.join(path.split(__file__)[0], 'README.md')
with open(readme, 'r', encoding='utf-8') as f:
    long_description = f.read()

setup(
        name='adbb',
        version=f'{version:.1f}.1',
        description="Object Oriented AniDB UDP Client Library",
        long_description_content_type="text/markdown",
        long_description=long_description,
        author="Winterbird",
        author_email="adbb@winterbird.org",
        url='https://github.com/winterbird-code/adbb',
        platforms=['any'],
        license= "GPLv3",
        packages=['adbb'],
        package_data = {
            'adbb': ['*.txt']
            },
        entry_points={
            'console_scripts': [
                'arrange_anime=adbb.utils:arrange_anime',
                'jellyfin_anime_sync=adbb.jellyfin:jellyfin_anime_sync',
                'adbb_cache=adbb.utils:cache_cleaner'
                ]
            },
        install_requires=[
            'pycryptodome',
            'sqlalchemy'
            ],
        extras_requires={
            'jellyfin': ['jellyfin_apiclient_python']
            }
        )
