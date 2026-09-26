#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 2018 Matt Martz
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

import sys
import subprocess

cmd = [sys.executable, 'speedtest.py', '--source', '127.0.0.1']

p = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)

stdout, stderr = p.communicate()

if p.returncode != 1:
    raise SystemExit('%s did not fail with exit code 1' % ' '.join(cmd))

expected_errors = [
    b'Invalid argument',
    b'WinError 10051',
    b'WinError 10049',
    b'unreachable network',
    b'Cannot assign requested address',
    b"Can't assign requested address",
    b'Network is unreachable'
]

if not any(err in stderr for err in expected_errors):
    raise SystemExit(
        'Expected source binding error not found in stderr:\n%s' % stderr.decode()
    )
