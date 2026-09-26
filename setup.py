#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 2012 Matt Martz
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import os
import re
from setuptools import setup

here = os.path.abspath(os.path.dirname(__file__))


def find_version(*file_paths):
    try:
        with open(os.path.join(here, *file_paths), 'r', encoding='latin1') as f:
            version_file = f.read()
    except Exception:
        raise RuntimeError("Unable to find version string.")

    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]",
                              version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")


try:
    with open('README.rst', 'r', encoding='utf-8') as f:
        long_description = f.read()
except Exception:
    long_description = ''


setup(
    name='dz-speedtest-cli',
    version=find_version('speedtest.py'),
    description=('Command line interface for testing internet bandwidth using '
                 'speedtest.net'),
    long_description=long_description,
    long_description_content_type='text/x-rst',
    keywords='speedtest speedtest.net ookla',
    author='DownloaderZone',
    url='https://github.com/DownloaderZone/Speedtest-Cli',
    project_urls={
        'Source': 'https://github.com/DownloaderZone/Speedtest-Cli',
        'Tracker': 'https://github.com/DownloaderZone/Speedtest-Cli/issues',
    },
    license='Apache-2.0',
    py_modules=['speedtest'],
    python_requires='>=3.8',
    entry_points={
        'console_scripts': [
            'speedtest=speedtest:main',
            'speedtest-cli=speedtest:main',
            'dz-speedtest=speedtest:main',
            'dz-speedtest-cli=speedtest:main'
        ]
    },
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Console',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3 :: Only',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Programming Language :: Python :: 3.14',
    ]
)
