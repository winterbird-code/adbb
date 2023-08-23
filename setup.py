#!/usr/bin/env python
from os import environ

try:
    from setuptools import setup
except ImportError:
    from distutils import setup

import adbb

version = float(adbb.anidb_client_version)/10

setup(
        name='adbb',
        version=str(version),
        description="Object Oriented AniDB UDP Client Library",
        author="Winterbird",
        author_email="adbb<_at_>winterbird.org",
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
                'jellyfin_anime_sync=adbb.jellyfin:jellyfin_anime_sync'
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
