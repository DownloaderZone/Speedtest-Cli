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

import csv
import datetime
import errno
import math
import os
import platform
import re
import signal
import socket
import statistics
import subprocess
import sys
import threading
import timeit
import xml.parsers.expat

try:
    import gzip
    GZIP_BASE = gzip.GzipFile
except ImportError:
    gzip = None
    GZIP_BASE = object

__version__ = '2.2.0'


class FakeShutdownEvent(object):
    """Class to fake a threading.Event.isSet so that users of this module
    are not required to register their own threading.Event()
    """

    @staticmethod
    def isSet():
        "Dummy method to always return false"""
        return False

    is_set = isSet


# Some global variables we use
DEBUG = False
_GLOBAL_DEFAULT_TIMEOUT = object()
PY310PLUS = sys.version_info[:2] >= (3, 10)
PY311PLUS = sys.version_info[:2] >= (3, 11)
PY312PLUS = sys.version_info[:2] >= (3, 12)
PY313PLUS = sys.version_info[:2] >= (3, 13)
PY314PLUS = sys.version_info[:2] >= (3, 14)

import json
import ssl
import xml.etree.ElementTree as ET
from urllib.request import (
    urlopen, Request, HTTPError, URLError,
    AbstractHTTPHandler, ProxyHandler,
    HTTPDefaultErrorHandler, HTTPRedirectHandler,
    HTTPErrorProcessor, OpenerDirector
)
from http.client import HTTPConnection, HTTPSConnection, BadStatusLine
from queue import Queue
from urllib.parse import urlparse, parse_qs
from hashlib import md5
from argparse import ArgumentParser as ArgParser, SUPPRESS as ARG_SUPPRESS
from io import StringIO, BytesIO

PARSER_TYPE_INT = int
PARSER_TYPE_STR = str
PARSER_TYPE_FLOAT = float

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass


def to_utf8(v):
    return v


def print_(*args, **kwargs):
    print(*args, **kwargs)


CERT_ERROR = (ssl.CertificateError,)
HTTP_ERRORS = (
    HTTPError, URLError, socket.error, ssl.SSLError, BadStatusLine, ssl.CertificateError
)

etree_iter = ET.Element.iter


def event_is_set(event):
    return event.is_set()


thread_is_alive = threading.Thread.is_alive


class SpeedtestException(Exception):
    """Base exception for this module"""


class SpeedtestCLIError(SpeedtestException):
    """Generic exception for raising errors during CLI operation"""


class SpeedtestHTTPError(SpeedtestException):
    """Base HTTP exception for this module"""


class SpeedtestConfigError(SpeedtestException):
    """Configuration XML is invalid"""


class SpeedtestServersError(SpeedtestException):
    """Servers XML is invalid"""


class ConfigRetrievalError(SpeedtestHTTPError):
    """Could not retrieve config.php"""


class ServersRetrievalError(SpeedtestHTTPError):
    """Could not retrieve speedtest-servers.php"""


class InvalidServerIDType(SpeedtestException):
    """Server ID used for filtering was not an integer"""


class NoMatchedServers(SpeedtestException):
    """No servers matched when filtering"""


class SpeedtestMiniConnectFailure(SpeedtestException):
    """Could not connect to the provided speedtest mini server"""


class InvalidSpeedtestMiniServer(SpeedtestException):
    """Server provided as a speedtest mini server does not actually appear
    to be a speedtest mini server
    """


class ShareResultsConnectFailure(SpeedtestException):
    """Could not connect to speedtest.net API to POST results"""


class ShareResultsSubmitFailure(SpeedtestException):
    """Unable to successfully POST results to speedtest.net API after
    connection
    """


class SpeedtestUploadTimeout(SpeedtestException):
    """testlength configuration reached during upload
    Used to ensure the upload halts when no additional data should be sent
    """


class SpeedtestBestServerFailure(SpeedtestException):
    """Unable to determine best server"""


class SpeedtestMissingBestServer(SpeedtestException):
    """get_best_server not called or not able to determine best server"""


class SpeedtestHTTPConnection(HTTPConnection):
    """Custom HTTPConnection to support source_address"""
    def __init__(self, *args, **kwargs):
        source_address = kwargs.pop('source_address', None)
        timeout = kwargs.pop('timeout', 10)

        self._tunnel_host = None

        super().__init__(*args, **kwargs)

        self.source_address = source_address
        self.timeout = timeout

    def connect(self):
        """Connect to the host and port specified in __init__."""
        self.sock = socket.create_connection(
            (self.host, self.port),
            self.timeout,
            self.source_address
        )
        if self._tunnel_host:
            self._tunnel()


class SpeedtestHTTPSConnection(HTTPSConnection):
    """Custom HTTPSConnection to support source_address and SSL contexts"""
    default_port = 443

    def __init__(self, *args, **kwargs):
        source_address = kwargs.pop('source_address', None)
        timeout = kwargs.pop('timeout', 10)

        self._tunnel_host = None

        super().__init__(*args, **kwargs)

        self.timeout = timeout
        self.source_address = source_address

    def connect(self):
        "Connect to a host on a given (SSL) port."
        self.sock = socket.create_connection(
            (self.host, self.port),
            self.timeout,
            self.source_address
        )

        if self._tunnel_host:
            self._tunnel()

        kwargs = {}
        if self._tunnel_host:
            kwargs['server_hostname'] = self._tunnel_host
        else:
            kwargs['server_hostname'] = self.host

        if getattr(self, '_context', None):
            self.sock = self._context.wrap_socket(self.sock, **kwargs)
        else:
            ctx = ssl.create_default_context()
            self.sock = ctx.wrap_socket(self.sock, **kwargs)


def _build_connection(connection, source_address, timeout, context=None):
    """Callable to build an ``HTTPConnection`` or
    ``HTTPSConnection`` with the args we need

    Called from ``http(s)_open`` methods of ``SpeedtestHTTPHandler`` or
    ``SpeedtestHTTPSHandler``
    """
    def inner(host, **kwargs):
        kwargs.update({
            'source_address': source_address,
            'timeout': timeout
        })
        if context:
            kwargs['context'] = context
        return connection(host, **kwargs)
    return inner


class SpeedtestHTTPHandler(AbstractHTTPHandler):
    """Custom ``HTTPHandler`` that can build a ``HTTPConnection`` with the
    args we need for ``source_address`` and ``timeout``
    """
    def __init__(self, debuglevel=0, source_address=None, timeout=10):
        AbstractHTTPHandler.__init__(self, debuglevel)
        self.source_address = source_address
        self.timeout = timeout

    def http_open(self, req):
        return self.do_open(
            _build_connection(
                SpeedtestHTTPConnection,
                self.source_address,
                self.timeout
            ),
            req
        )

    http_request = AbstractHTTPHandler.do_request_


class SpeedtestHTTPSHandler(AbstractHTTPHandler):
    """Custom ``HTTPSHandler`` that can build a ``HTTPSConnection`` with the
    args we need for ``source_address`` and ``timeout``
    """
    def __init__(self, debuglevel=0, context=None, source_address=None,
                 timeout=10):
        AbstractHTTPHandler.__init__(self, debuglevel)
        self._context = context
        self.source_address = source_address
        self.timeout = timeout

    def https_open(self, req):
        return self.do_open(
            _build_connection(
                SpeedtestHTTPSConnection,
                self.source_address,
                self.timeout,
                context=self._context,
            ),
            req
        )

    https_request = AbstractHTTPHandler.do_request_


def build_opener(source_address=None, timeout=10):
    """Function similar to ``urllib2.build_opener`` that will build
    an ``OpenerDirector`` with the explicit handlers we want,
    ``source_address`` for binding, ``timeout`` and our custom
    `User-Agent`
    """

    printer('Timeout set to %d' % timeout, debug=True)

    if source_address:
        source_address_tuple = (source_address, 0)
        printer('Binding to source address: %r' % (source_address_tuple,),
                debug=True)
    else:
        source_address_tuple = None

    handlers = [
        ProxyHandler(),
        SpeedtestHTTPHandler(source_address=source_address_tuple,
                             timeout=timeout),
        SpeedtestHTTPSHandler(source_address=source_address_tuple,
                              timeout=timeout),
        HTTPDefaultErrorHandler(),
        HTTPRedirectHandler(),
        HTTPErrorProcessor()
    ]

    opener = OpenerDirector()
    opener.addheaders = [('User-agent', build_user_agent())]

    for handler in handlers:
        opener.add_handler(handler)

    return opener


class GzipDecodedResponse(GZIP_BASE):
    """A file-like object to decode a response encoded with the gzip
    method, as described in RFC 1952.
    """
    def __init__(self, response):
        if not gzip:
            raise SpeedtestHTTPError('HTTP response body is gzip encoded, '
                                     'but gzip support is not available')
        self.io = BytesIO()
        while 1:
            chunk = response.read(1024)
            if len(chunk) == 0:
                break
            self.io.write(chunk)
        self.io.seek(0)
        gzip.GzipFile.__init__(self, mode='rb', fileobj=self.io)

    def close(self):
        try:
            gzip.GzipFile.close(self)
        finally:
            self.io.close()


def get_exception():
    """Helper function for getting the current exception in a try/except block"""
    return sys.exc_info()[1]


def distance(origin, destination):
    """Determine distance between 2 sets of [lat,lon] in km"""

    lat1, lon1 = origin
    lat2, lon2 = destination
    radius = 6371  # km

    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) * math.sin(dlat / 2) +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) * math.sin(dlon / 2) *
         math.sin(dlon / 2))
    a = min(1.0, max(0.0, a))
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    d = radius * c

    return d


def build_user_agent():
    """Build a Mozilla/5.0 compatible User-Agent string"""

    ua_tuple = (
        'Mozilla/5.0',
        '(%s; U; %s; en-us)' % (platform.platform(),
                                platform.architecture()[0]),
        'Python/%s' % platform.python_version(),
        '(KHTML, like Gecko)',
        'speedtest-cli/%s' % __version__
    )
    user_agent = ' '.join(ua_tuple)
    printer('User-Agent: %s' % user_agent, debug=True)
    return user_agent


def build_request(url, data=None, headers=None, bump='0', secure=False):
    """Build a urllib2 request object

    This function automatically adds a User-Agent header to all requests

    """

    if not headers:
        headers = {}

    if url[0] == ':':
        scheme = ('http', 'https')[bool(secure)]
        schemed_url = '%s%s' % (scheme, url)
    else:
        schemed_url = url

    if '?' in url:
        delim = '&'
    else:
        delim = '?'

    # WHO YOU GONNA CALL? CACHE BUSTERS!
    final_url = '%s%sx=%s.%s' % (schemed_url, delim,
                                 int(timeit.time.time() * 1000),
                                 bump)

    headers.update({
        'Cache-Control': 'no-cache',
        'User-Agent': build_user_agent(),
    })

    printer('%s %s' % (('GET', 'POST')[bool(data)], final_url),
            debug=True)

    return Request(final_url, data=data, headers=headers)


def catch_request(request, opener=None):
    """Helper function to catch common exceptions encountered when
    establishing a connection with a HTTP/HTTPS request

    """

    if opener:
        _open = opener.open
    else:
        _open = urlopen

    try:
        uh = _open(request)
        if request.get_full_url() != uh.geturl():
            printer('Redirected to %s' % uh.geturl(), debug=True)
        return uh, False
    except HTTP_ERRORS:
        e = get_exception()
        return None, e


def get_response_stream(response):
    """Helper function to return either a Gzip reader if
    ``Content-Encoding`` is ``gzip`` otherwise the response itself

    """

    try:
        getheader = response.headers.getheader
    except AttributeError:
        getheader = response.getheader

    if getheader('content-encoding') == 'gzip':
        return GzipDecodedResponse(response)

    return response


def get_attributes_by_tag_name(dom, tag_name):
    """Retrieve an attribute from an XML document and return it in a
    consistent format

    Only used with xml.dom.minidom, which is likely only to be used
    with python versions older than 2.5
    """
    elem = dom.getElementsByTagName(tag_name)[0]
    return dict(list(elem.attributes.items()))


def print_dots(shutdown_event):
    """Built in callback function used by Thread classes for printing
    status
    """
    def inner(current, total, start=False, end=False):
        if event_is_set(shutdown_event):
            return

        sys.stdout.write('.')
        if current + 1 == total and end is True:
            sys.stdout.write('\n')
        sys.stdout.flush()
    return inner


def do_nothing(*args, **kwargs):
    pass


class HTTPDownloader(threading.Thread):
    """Thread class for retrieving a URL"""

    def __init__(self, i, request, start, timeout, opener=None,
                 shutdown_event=None):
        threading.Thread.__init__(self)
        self.request = request
        self.result = [0]
        self.starttime = start
        self.timeout = timeout
        self.i = i
        if opener:
            self._opener = opener.open
        else:
            self._opener = urlopen

        if shutdown_event:
            self._shutdown_event = shutdown_event
        else:
            self._shutdown_event = FakeShutdownEvent()

    def run(self):
        f = None
        try:
            if (not event_is_set(self._shutdown_event) and
                    (timeit.default_timer() - self.starttime) <= self.timeout):
                f = self._opener(self.request)
                while (not event_is_set(self._shutdown_event) and
                        (timeit.default_timer() - self.starttime) <=
                        self.timeout):
                    self.result.append(len(f.read(65536)))
                    if self.result[-1] == 0:
                        break
        except IOError:
            pass
        except HTTP_ERRORS:
            pass
        finally:
            if f is not None:
                f.close()


class HTTPUploaderData(object):
    """File like object to improve cutting off the upload once the timeout
    has been reached
    """

    def __init__(self, length, start, timeout, shutdown_event=None):
        self.length = length
        self.start = start
        self.timeout = timeout

        if shutdown_event:
            self._shutdown_event = shutdown_event
        else:
            self._shutdown_event = FakeShutdownEvent()

        self._data = None

        self.total = [0]

    def pre_allocate(self):
        chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        multiplier = (int(self.length) + 35) // 36
        IO = BytesIO or StringIO
        try:
            self._data = IO(
                ('content1=%s' %
                 (chars * multiplier)[0:int(self.length) - 9]
                 ).encode()
            )
        except MemoryError:
            raise SpeedtestCLIError(
                'Insufficient memory to pre-allocate upload data. Please '
                'use --no-pre-allocate'
            )

    @property
    def data(self):
        if not self._data:
            self.pre_allocate()
        return self._data

    def read(self, n=10240):
        if ((timeit.default_timer() - self.start) <= self.timeout and
                not event_is_set(self._shutdown_event)):
            chunk = self.data.read(n)
            self.total.append(len(chunk))
            return chunk
        else:
            raise SpeedtestUploadTimeout()

    def __len__(self):
        return self.length


class HTTPUploader(threading.Thread):
    """Thread class for putting a URL"""

    def __init__(self, i, request, start, size, timeout, opener=None,
                 shutdown_event=None):
        threading.Thread.__init__(self)
        self.request = request
        self.request.data.start = self.starttime = start
        self.size = size
        self.result = 0
        self.timeout = timeout
        self.i = i

        if opener:
            self._opener = opener.open
        else:
            self._opener = urlopen

        if shutdown_event:
            self._shutdown_event = shutdown_event
        else:
            self._shutdown_event = FakeShutdownEvent()

    def run(self):
        request = self.request
        try:
            if ((timeit.default_timer() - self.starttime) <= self.timeout and
                    not event_is_set(self._shutdown_event)):
                f = self._opener(request)
                f.read(11)
                f.close()
                self.result = sum(self.request.data.total)
            else:
                self.result = 0
        except (IOError, SpeedtestUploadTimeout):
            self.result = sum(self.request.data.total)
        except HTTP_ERRORS:
            self.result = 0


class SpeedtestResults(object):
    """Class for holding the results of a speedtest, including:

    Download speed
    Upload speed
    Ping/Latency to test server
    Data about server that the test was run against

    Additionally this class can return a result data as a dictionary or CSV,
    as well as submit a POST of the result data to the speedtest.net API
    to get a share results image link.
    """

    def __init__(self, download=0, upload=0, ping=0, server=None, client=None,
                 opener=None, secure=False):
        self.download = download
        self.upload = upload
        self.ping = ping
        if server is None:
            self.server = {}
        else:
            self.server = server
        self.client = client or {}

        self._share = None
        try:
            from datetime import timezone
            _utc = timezone.utc
        except ImportError:
            _utc = None

        if _utc is not None:
            self.timestamp = '%sZ' % datetime.datetime.now(_utc).replace(tzinfo=None).isoformat()
        else:
            self.timestamp = '%sZ' % datetime.datetime.utcnow().isoformat()
        self.bytes_received = 0
        self.bytes_sent = 0

        if opener:
            self._opener = opener
        else:
            self._opener = build_opener()

        self._secure = secure

    def __repr__(self):
        return repr(self.dict())

    def share(self):
        """POST data to the speedtest.net API to obtain a share results
        link
        """

        if self._share:
            return self._share

        download = int(round(self.download / 1000.0, 0))
        ping = int(round(self.ping, 0))
        upload = int(round(self.upload / 1000.0, 0))

        # Build the request to send results back to speedtest.net
        # We use a list instead of a dict because the API expects parameters
        # in a certain order
        api_data = [
            'recommendedserverid=%s' % self.server['id'],
            'ping=%s' % ping,
            'screenresolution=',
            'promo=',
            'download=%s' % download,
            'screendpi=',
            'upload=%s' % upload,
            'testmethod=http',
            'hash=%s' % md5(('%s-%s-%s-%s' %
                             (ping, upload, download, '297aae72'))
                            .encode()).hexdigest(),
            'touchscreen=none',
            'startmode=pingselect',
            'accuracy=1',
            'bytesreceived=%s' % self.bytes_received,
            'bytessent=%s' % self.bytes_sent,
            'serverid=%s' % self.server['id'],
        ]

        headers = {'Referer': 'http://c.speedtest.net/flash/speedtest.swf'}
        request = build_request('://www.speedtest.net/api/api.php',
                                data='&'.join(api_data).encode(),
                                headers=headers, secure=self._secure)
        f, e = catch_request(request, opener=self._opener)
        if e:
            raise ShareResultsConnectFailure(e)

        response = f.read()
        code = f.code
        f.close()

        if int(code) != 200:
            raise ShareResultsSubmitFailure('Could not submit results to '
                                            'speedtest.net')

        qsargs = parse_qs(response.decode())
        resultid = qsargs.get('resultid')
        if not resultid or len(resultid) != 1:
            raise ShareResultsSubmitFailure('Could not submit results to '
                                            'speedtest.net')

        self._share = 'http://www.speedtest.net/result/%s.png' % resultid[0]

        return self._share

    def dict(self):
        """Return dictionary of result data"""

        return {
            'download': self.download,
            'upload': self.upload,
            'ping': self.ping,
            'server': self.server,
            'timestamp': self.timestamp,
            'bytes_sent': self.bytes_sent,
            'bytes_received': self.bytes_received,
            'share': self._share,
            'client': self.client,
        }

    @staticmethod
    def csv_header(delimiter=','):
        """Return CSV Headers"""

        row = ['Server ID', 'Sponsor', 'Server Name', 'Timestamp', 'Distance',
               'Ping', 'Download', 'Upload', 'Share', 'IP Address']
        out = StringIO()
        writer = csv.writer(out, delimiter=delimiter, lineterminator='')
        writer.writerow([to_utf8(v) for v in row])
        return out.getvalue()

    def csv(self, delimiter=','):
        """Return data in CSV format"""

        data = self.dict()
        out = StringIO()
        writer = csv.writer(out, delimiter=delimiter, lineterminator='')
        server = data.get('server') or {}
        client = data.get('client') or {}
        row = [server.get('id', ''), server.get('sponsor', ''),
               server.get('name', ''), data.get('timestamp', ''),
               server.get('d', ''), data.get('ping', ''), data.get('download', ''),
               data.get('upload', ''), self._share or '', client.get('ip', '')]
        writer.writerow([to_utf8(v) for v in row])
        return out.getvalue()

    def tsv(self):
        """Return data in TSV format"""
        return self.csv(delimiter='\t')


    def json(self, pretty=False):
        """Return data in JSON format"""

        kwargs = {}
        if pretty:
            kwargs.update({
                'indent': 4,
                'sort_keys': True
            })
        return json.dumps(self.dict(), **kwargs)


class Speedtest(object):
    """Class for performing standard speedtest.net testing operations"""

    def __init__(self, config=None, source_address=None, timeout=10,
                 secure=False, shutdown_event=None, location=None):
        if not math.isfinite(timeout) or timeout <= 0:
            raise SpeedtestConfigError('Timeout must be a positive finite number')
        self.config = {}

        self._source_address = source_address
        self._timeout = timeout
        self._opener = build_opener(source_address, timeout)

        self._secure = secure

        if shutdown_event:
            self._shutdown_event = shutdown_event
        else:
            self._shutdown_event = FakeShutdownEvent()

        self.get_config()
        if config is not None:
            self.config.update(config)

        if location is not None:
            try:
                lat, lon = map(float, location)
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError()
            except (TypeError, ValueError):
                raise SpeedtestConfigError('Location must be valid latitude and longitude')
            self.lat_lon = (lat, lon)
            self.config['client'].update({'lat': str(lat), 'lon': str(lon)})

        self.servers = {}
        self.closest = []
        self._best = {}

        self.results = SpeedtestResults(
            client=self.config['client'],
            opener=self._opener,
            secure=secure,
        )

    @property
    def best(self):
        if not self._best:
            self.get_best_server()
        return self._best

    def get_config(self):
        """Download the speedtest.net configuration and return only the data
        we are interested in
        """

        headers = {}
        if gzip:
            headers['Accept-Encoding'] = 'gzip'
        request = build_request('https://www.speedtest.net/speedtest-config.php',
                                headers=headers, secure=self._secure)
        uh, e = catch_request(request, opener=self._opener)
        if e:
            raise ConfigRetrievalError(e)
        configxml_list = []

        stream = get_response_stream(uh)

        while 1:
            try:
                configxml_list.append(stream.read(1024))
            except (OSError, EOFError):
                raise ConfigRetrievalError(get_exception())
            if len(configxml_list[-1]) == 0:
                break
        stream.close()
        uh.close()

        if int(uh.code) != 200:
            return None

        configxml = ''.encode().join(configxml_list)

        printer('Config XML:\n%s' % configxml, debug=True)

        try:
            try:
                root = ET.fromstring(configxml)
            except ET.ParseError:
                e = get_exception()
                raise SpeedtestConfigError(
                    'Malformed speedtest.net configuration: %s' % e
                )
            server_config = root.find('server-config').attrib
            download = root.find('download').attrib
            upload = root.find('upload').attrib
            # times = root.find('times').attrib
            client = root.find('client').attrib

        except AttributeError:
            try:
                root = DOM.parseString(configxml)
            except ExpatError:
                e = get_exception()
                raise SpeedtestConfigError(
                    'Malformed speedtest.net configuration: %s' % e
                )
            server_config = get_attributes_by_tag_name(root, 'server-config')
            download = get_attributes_by_tag_name(root, 'download')
            upload = get_attributes_by_tag_name(root, 'upload')
            # times = get_attributes_by_tag_name(root, 'times')
            client = get_attributes_by_tag_name(root, 'client')

        ignore_servers = [
            int(i) for i in server_config['ignoreids'].split(',') if i
        ]

        ratio = int(upload['ratio'])
        upload_max = int(upload['maxchunkcount'])
        up_sizes = [32768, 65536, 131072, 262144, 524288, 1048576, 7340032]
        sizes = {
            'upload': up_sizes[ratio - 1:],
            'download': [350, 500, 750, 1000, 1500, 2000, 2500,
                         3000, 3500, 4000]
        }

        size_count = len(sizes['upload'])

        upload_count = int(math.ceil(upload_max / size_count))

        counts = {
            'upload': upload_count,
            'download': int(download['threadsperurl'])
        }

        threads = {
            'upload': int(upload['threads']),
            'download': int(server_config['threadcount']) * 2
        }

        length = {
            'upload': int(upload['testlength']),
            'download': int(download['testlength'])
        }

        self.config.update({
            'client': client,
            'ignore_servers': ignore_servers,
            'sizes': sizes,
            'counts': counts,
            'threads': threads,
            'length': length,
            'upload_max': upload_count * size_count
        })

        try:
            self.lat_lon = (float(client['lat']), float(client['lon']))
        except ValueError:
            raise SpeedtestConfigError(
                'Unknown location: lat=%r lon=%r' %
                (client.get('lat'), client.get('lon'))
            )

        printer('Config:\n%r' % self.config, debug=True)

        return self.config

    def get_servers(self, servers=None, exclude=None, host=None, full=False):
        """Retrieve a the list of speedtest.net servers, optionally filtered
        to servers matching those specified in the ``servers`` argument,
        excluding those in ``exclude``, or matching ``host``
        """
        if servers is None:
            servers = []

        if exclude is None:
            exclude = []

        servers = list(servers)
        exclude = list(exclude)
        self.servers.clear()
        self.closest = []
        self._best.clear()
        self.results.server = {}
        self.results.ping = 0

        for server_list in (servers, exclude):
            for i, s in enumerate(server_list):
                try:
                    server_list[i] = int(s)
                except (TypeError, ValueError):
                    raise InvalidServerIDType(
                        '%s is an invalid server type, must be int' % s
                    )

        urls = [
            'https://www.speedtest.net/speedtest-servers-static.php',
            'https://c.speedtest.net/speedtest-servers-static.php',
            'https://www.speedtest.net/speedtest-servers.php',
            'https://c.speedtest.net/speedtest-servers.php',
        ]
        if not full and not servers and not host:
            urls.insert(0, 'https://www.speedtest.net/api/js/servers'
                        '?engine=js&limit=100&lat=%s&lon=%s' % self.lat_lon)

        headers = {}
        if gzip:
            headers['Accept-Encoding'] = 'gzip'

        errors = []
        for url in urls:
            try:
                request = build_request(
                    '%s%sthreads=%s' % (url, '&' if '?' in url else '?',
                                       self.config['threads']['download']),
                    headers=headers,
                    secure=self._secure
                )
                uh, e = catch_request(request, opener=self._opener)
                if e:
                    errors.append('%s' % e)
                    raise ServersRetrievalError()

                stream = get_response_stream(uh)

                serversxml_list = []
                while 1:
                    try:
                        serversxml_list.append(stream.read(1024))
                    except (OSError, EOFError):
                        raise ServersRetrievalError(get_exception())
                    if len(serversxml_list[-1]) == 0:
                        break

                stream.close()
                uh.close()

                if int(uh.code) != 200:
                    raise ServersRetrievalError()

                serversxml = ''.encode().join(serversxml_list)

                printer('Servers XML:\n%s' % serversxml, debug=True)

                if '/api/js/servers' in url:
                    try:
                        elements = json.loads(serversxml.decode('utf-8'))
                        if not isinstance(elements, list):
                            raise ValueError('Expected a server list')
                    except (ValueError, UnicodeError) as e:
                        errors.append(str(e))
                        continue
                else:
                    try:
                        root = ET.fromstring(serversxml)
                        elements = etree_iter(root, 'server')
                    except ET.ParseError as e:
                        errors.append(str(e))
                        continue

                for server in elements:
                    try:
                        attrib = dict(server if isinstance(server, dict) else server.attrib)
                        server_id = int(attrib['id'])
                        lat, lon = float(attrib['lat']), float(attrib['lon'])
                        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                            continue
                        parts = urlparse(attrib['url'])
                        if parts.scheme not in ('http', 'https') or not parts.hostname:
                            continue
                    except (KeyError, TypeError, ValueError):
                        continue

                    if servers and server_id not in servers:
                        continue

                    if (server_id in self.config['ignore_servers']
                            or server_id in exclude):
                        continue

                    if host:
                        server_host = attrib.get('host', parts.netloc)
                        if host.lower() not in (server_host.lower(),
                                                server_host.rsplit(':', 1)[0].lower(),
                                                parts.netloc.lower(), parts.hostname.lower()):
                            continue

                    d = distance(self.lat_lon, (lat, lon))

                    attrib['d'] = d

                    try:
                        self.servers[d].append(attrib)
                    except KeyError:
                        self.servers[d] = [attrib]

                if self.servers:
                    break

            except ServersRetrievalError:
                continue

        if (servers or exclude or host) and not self.servers:
            raise NoMatchedServers()

        if not self.servers:
            raise ServersRetrievalError('No usable servers retrieved. %s' % '; '.join(errors))

        return self.servers

    def set_mini_server(self, server):
        """Instead of querying for a list of servers, set a link to a
        speedtest mini server
        """

        urlparts = urlparse(server)

        name, ext = os.path.splitext(urlparts[2])
        if ext:
            url = os.path.dirname(server)
        else:
            url = server

        request = build_request(url)
        uh, e = catch_request(request, opener=self._opener)
        if e:
            raise SpeedtestMiniConnectFailure('Failed to connect to %s' %
                                              server)
        else:
            text = uh.read()
            uh.close()

        extension = re.findall('upload_?[Ee]xtension: "([^"]+)"',
                               text.decode())
        if not extension:
            for ext in ['php', 'asp', 'aspx', 'jsp']:
                try:
                    f = self._opener.open(
                        '%s/speedtest/upload.%s' % (url, ext)
                    )
                except Exception:
                    pass
                else:
                    data = f.read().strip().decode()
                    if (f.code == 200 and
                            len(data.splitlines()) == 1 and
                            re.match('size=[0-9]', data)):
                        extension = [ext]
                        break
        if not urlparts or not extension:
            raise InvalidSpeedtestMiniServer('Invalid Speedtest Mini Server: '
                                             '%s' % server)

        self.servers = [{
            'sponsor': 'Speedtest Mini',
            'name': urlparts[1],
            'd': 0,
            'url': '%s/speedtest/upload.%s' % (url.rstrip('/'), extension[0]),
            'latency': 0,
            'id': 0
        }]

        return self.servers

    def get_closest_servers(self, limit=5):
        """Limit servers to the closest speedtest.net servers based on
        geographic distance
        """

        if not isinstance(limit, int) or limit < 1:
            raise ValueError('Server limit must be a positive integer')
        if not self.servers:
            self.get_servers()

        self.closest = []
        for d in sorted(self.servers.keys()):
            for s in sorted(self.servers[d], key=lambda s: int(s['id'])):
                self.closest.append(s)
                if len(self.closest) == limit:
                    break
            else:
                continue
            break

        printer('Closest Servers:\n%r' % self.closest, debug=True)
        return self.closest

    def get_best_server(self, servers=None, selection='nearest'):
        """Select the nearest reachable server, or lowest-latency nearby server.

        Latency is the median HTTP round-trip time, including the response body.
        """
        if selection not in ('nearest', 'latency'):
            raise ValueError('Selection must be nearest or latency')
        if servers is None:
            if not self.servers:
                self.get_servers()
            servers = [s for d in sorted(self.servers) for s in self.servers[d]]
        servers = sorted(servers, key=lambda s: (s.get('d', 0), int(s['id'])))
        self._best.clear()
        self.results.server = {}
        self.results.ping = 0

        if self._source_address:
            source_address_tuple = (self._source_address, 0)
        else:
            source_address_tuple = None

        user_agent = build_user_agent()

        results = []
        for server in servers:
            if event_is_set(self._shutdown_event):
                break
            if selection == 'nearest' and results and server.get('d', 0) > results[0][1]:
                break
            cum = []
            url = os.path.dirname(server['url'])
            stamp = int(timeit.time.time() * 1000)
            latency_url = '%s/latency.txt?x=%s' % (url, stamp)
            for i in range(0, 3):
                this_latency_url = '%s.%s' % (latency_url, i)
                printer('%s %s' % ('GET', this_latency_url),
                        debug=True)
                urlparts = urlparse(this_latency_url)
                h = None
                try:
                    if urlparts[0] == 'https':
                        h = SpeedtestHTTPSConnection(
                            urlparts[1],
                            source_address=source_address_tuple,
                            timeout=min(self._timeout, 3)
                        )
                    else:
                        h = SpeedtestHTTPConnection(
                            urlparts[1],
                            source_address=source_address_tuple,
                            timeout=min(self._timeout, 3)
                        )
                    headers = {'User-Agent': user_agent}
                    path = '%s?%s' % (urlparts[2], urlparts[4])
                    start = timeit.default_timer()
                    h.request("GET", path, headers=headers)
                    r = h.getresponse()
                    text = r.read(9)
                    total = (timeit.default_timer() - start)
                    if int(r.status) == 200 and text == b'test=test':
                        cum.append(total * 1000.0)
                except HTTP_ERRORS:
                    e = get_exception()
                    printer('ERROR: %r' % e, debug=True)
                finally:
                    if h is not None:
                        h.close()

            if len(cum) >= 2:
                latency = statistics.median(cum)
                results.append((latency, server.get('d', 0), int(server['id']), server))
            if selection == 'latency' and len(results) >= 20:
                break

        if not results:
            raise SpeedtestBestServerFailure('Unable to connect to servers to '
                                              'test latency.')
        latency, _, _, best = min(results, key=lambda r: r[:3])
        fastest = round(latency, 3)
        best['latency'] = fastest

        self.results.ping = fastest
        self.results.server = best

        self._best.update(best)
        printer('Best Server:\n%r' % best, debug=True)
        return best

    def download(self, callback=do_nothing, threads=None):
        """Test download speed against speedtest.net

        A ``threads`` value of ``None`` will fall back to those dictated
        by the speedtest.net configuration
        """

        urls = []
        for size in self.config['sizes']['download']:
            for _ in range(0, self.config['counts']['download']):
                urls.append('%s/random%sx%s.jpg' %
                            (os.path.dirname(self.best['url']), size, size))

        request_count = len(urls)
        requests = []
        for i, url in enumerate(urls):
            requests.append(
                build_request(url, bump=i, secure=self._secure)
            )

        max_threads = threads or self.config['threads']['download']
        slots = threading.Semaphore(max_threads)

        def producer(q, requests, request_count):
            for i, request in enumerate(requests):
                thread = HTTPDownloader(
                    i,
                    request,
                    start,
                    self.config['length']['download'],
                    opener=self._opener,
                    shutdown_event=self._shutdown_event
                )
                slots.acquire()
                thread.start()
                q.put(thread, True)
                callback(i, request_count, start=True)

        finished = []

        def consumer(q, request_count):
            _is_alive = thread_is_alive
            while len(finished) < request_count:
                thread = q.get(True)
                while _is_alive(thread):
                    thread.join(timeout=0.001)
                slots.release()
                finished.append(sum(thread.result))
                callback(thread.i, request_count, end=True)

        q = Queue(max_threads)
        prod_thread = threading.Thread(target=producer,
                                       args=(q, requests, request_count))
        cons_thread = threading.Thread(target=consumer,
                                       args=(q, request_count))
        start = timeit.default_timer()
        prod_thread.start()
        cons_thread.start()
        _is_alive = thread_is_alive
        while _is_alive(prod_thread):
            prod_thread.join(timeout=0.001)
        while _is_alive(cons_thread):
            cons_thread.join(timeout=0.001)

        stop = timeit.default_timer()
        self.results.bytes_received = sum(finished)
        if not self.results.bytes_received:
            raise SpeedtestCLIError('Download failed: the selected server returned no test data')
        self.results.download = (
            (self.results.bytes_received / (stop - start)) * 8.0
        )
        if self.results.download > 100000:
            self.config['threads']['upload'] = 8
        return self.results.download

    def upload(self, callback=do_nothing, pre_allocate=True, threads=None):
        """Test upload speed against speedtest.net

        A ``threads`` value of ``None`` will fall back to those dictated
        by the speedtest.net configuration
        """

        sizes = []

        for size in self.config['sizes']['upload']:
            for _ in range(0, self.config['counts']['upload']):
                sizes.append(size)

        # request_count = len(sizes)
        request_count = min(len(sizes), self.config['upload_max'])

        requests = []
        for i, size in enumerate(sizes):
            # We set ``0`` for ``start`` and handle setting the actual
            # ``start`` in ``HTTPUploader`` to get better measurements
            data = HTTPUploaderData(
                size,
                0,
                self.config['length']['upload'],
                shutdown_event=self._shutdown_event
            )
            if pre_allocate:
                data.pre_allocate()

            headers = {'Content-length': size}
            requests.append(
                (
                    build_request(self.best['url'], data, secure=self._secure,
                                  headers=headers),
                    size
                )
            )

        max_threads = threads or self.config['threads']['upload']
        slots = threading.Semaphore(max_threads)

        def producer(q, requests, request_count):
            for i, request in enumerate(requests[:request_count]):
                thread = HTTPUploader(
                    i,
                    request[0],
                    start,
                    request[1],
                    self.config['length']['upload'],
                    opener=self._opener,
                    shutdown_event=self._shutdown_event
                )
                slots.acquire()
                thread.start()
                q.put(thread, True)
                callback(i, request_count, start=True)

        finished = []

        def consumer(q, request_count):
            _is_alive = thread_is_alive
            while len(finished) < request_count:
                thread = q.get(True)
                while _is_alive(thread):
                    thread.join(timeout=0.001)
                slots.release()
                finished.append(thread.result)
                callback(thread.i, request_count, end=True)

        q = Queue(threads or self.config['threads']['upload'])
        prod_thread = threading.Thread(target=producer,
                                       args=(q, requests, request_count))
        cons_thread = threading.Thread(target=consumer,
                                       args=(q, request_count))
        start = timeit.default_timer()
        prod_thread.start()
        cons_thread.start()
        _is_alive = thread_is_alive
        while _is_alive(prod_thread):
            prod_thread.join(timeout=0.1)
        while _is_alive(cons_thread):
            cons_thread.join(timeout=0.1)

        stop = timeit.default_timer()
        self.results.bytes_sent = sum(finished)
        if not self.results.bytes_sent:
            raise SpeedtestCLIError('Upload failed: no test data could be sent to the selected server')
        self.results.upload = (
            (self.results.bytes_sent / (stop - start)) * 8.0
        )
        return self.results.upload


def ctrl_c(shutdown_event):
    """Catch Ctrl-C key sequence and set a SHUTDOWN_EVENT for our threaded
    operations
    """
    def inner(signum, frame):
        shutdown_event.set()
        printer('\nCancelling...', error=True)
        sys.exit(0)
    return inner


OFFICIAL_INSTALL_CMD = """sudo apt-get remove speedtest-cli
sudo apt-get install curl
curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash
sudo apt-get install speedtest"""


def version():
    """Print the version"""

    printer('speedtest-cli %s' % __version__)
    printer('Python %s' % sys.version.replace('\n', ''))
    printer('Repository: https://github.com/DownloaderZone/Speedtest-Cli')
    official_cli = find_official_cli()
    if official_cli:
        printer('Official Ookla Speedtest CLI: %s (active)' % official_cli)
    else:
        printer('Official Ookla Speedtest CLI: not installed')
        printer('To install official CLI:')
        for line in OFFICIAL_INSTALL_CMD.splitlines():
            printer('  %s' % line)
    sys.exit(0)


def find_official_cli():
    """Look for official Ookla speedtest executable.
    Returns path string if found, None otherwise.
    Ensures that the returned path is not this python script itself.
    """
    env_path = os.environ.get('SPEEDTEST_CLI_PATH')
    candidates = []
    if env_path and os.path.isfile(env_path):
        candidates.append(env_path)

    binary_names = ['speedtest.exe', 'speedtest'] if sys.platform.startswith('win') else ['speedtest']
    path_dirs = os.environ.get('PATH', '').split(os.pathsep)
    for p in path_dirs:
        for b in binary_names:
            candidate = os.path.join(p, b)
            if os.path.isfile(candidate):
                if not candidate.lower().endswith(('.py', '.pyc', '.pyo')):
                    candidates.append(candidate)

    this_file = os.path.abspath(__file__)
    for cand in candidates:
        try:
            cand_abs = os.path.abspath(cand)
            if cand_abs == this_file:
                continue
            proc = subprocess.Popen([cand, '--version'],
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            out, _ = proc.communicate()
            out_str = out.decode('utf-8', 'ignore')
            if 'Ookla' in out_str:
                return cand
        except Exception:
            continue
    return None


def run_official_cli(binary_path, argv=None):
    """Delegate execution to official Ookla Speedtest CLI binary"""
    if argv is None:
        argv = sys.argv[1:]
    filtered_argv = [a for a in argv if a not in ('--official', '--pure-python')]
    cmd = [binary_path] + filtered_argv
    try:
        return subprocess.call(cmd)
    except Exception as e:
        raise SpeedtestCLIError(
            'Failed to execute official Speedtest CLI (%s): %s' %
            (binary_path, e)
        )


def format_speed(speed_bps, unit=None, units_tuple=('bit', 1)):
    """Format speed according to requested unit or fallback units tuple"""
    if unit:
        u = unit.lower()
        if u == 'bps':
            return '%0.2f bps' % speed_bps
        elif u == 'kbps':
            return '%0.2f kbps' % (speed_bps / 1000.0)
        elif u == 'mbps':
            return '%0.2f Mbit/s' % (speed_bps / 1000000.0)
        elif u == 'gbps':
            return '%0.2f Gbit/s' % (speed_bps / 1000000000.0)
        elif u in ('b/s', 'bps_bytes'):
            return '%0.2f B/s' % (speed_bps / 8.0)
        elif u in ('kb/s', 'kbyte/s'):
            return '%0.2f kB/s' % (speed_bps / 8000.0)
        elif u in ('mb/s', 'mbyte/s'):
            return '%0.2f MB/s' % (speed_bps / 8000000.0)
        elif u in ('gb/s', 'gbyte/s'):
            return '%0.2f GB/s' % (speed_bps / 8000000000.0)
        elif u == 'auto':
            if units_tuple[0] == 'byte':
                if speed_bps >= 8e9:
                    return '%0.2f GB/s' % (speed_bps / 8e9)
                elif speed_bps >= 8e6:
                    return '%0.2f MB/s' % (speed_bps / 8e6)
                elif speed_bps >= 8e3:
                    return '%0.2f kB/s' % (speed_bps / 8e3)
                else:
                    return '%0.2f B/s' % (speed_bps / 8.0)
            else:
                if speed_bps >= 1e9:
                    return '%0.2f Gbit/s' % (speed_bps / 1e9)
                elif speed_bps >= 1e6:
                    return '%0.2f Mbit/s' % (speed_bps / 1e6)
                elif speed_bps >= 1e3:
                    return '%0.2f kbit/s' % (speed_bps / 1e3)
                else:
                    return '%0.2f bit/s' % speed_bps
    return '%0.2f M%s/s' % (
        (speed_bps / 1000.0 / 1000.0) / units_tuple[1],
        units_tuple[0]
    )


def csv_header(delimiter=','):
    """Print the CSV Headers"""

    printer(SpeedtestResults.csv_header(delimiter=delimiter))
    sys.exit(0)


def parse_args():
    """Function to handle building and parsing of command line arguments"""
    description = (
        'Command line interface for testing internet bandwidth using '
        'speedtest.net.\n'
        '------------------------------------------------------------'
        '--------------\n'
        'https://github.com/DownloaderZone/Speedtest-Cli')

    parser = ArgParser(description=description)
    # Give optparse.OptionParser an `add_argument` method for
    # compatibility with argparse.ArgumentParser
    try:
        parser.add_argument = parser.add_option
    except AttributeError:
        pass
    parser.add_argument('--no-download', dest='download', default=True,
                        action='store_const', const=False,
                        help='Do not perform download test')
    parser.add_argument('--no-upload', dest='upload', default=True,
                        action='store_const', const=False,
                        help='Do not perform upload test')
    parser.add_argument('--single', default=False, action='store_true',
                        help='Only use a single connection instead of '
                             'multiple. This simulates a typical file '
                             'transfer.')
    parser.add_argument('--bytes', dest='units', action='store_const',
                        const=('byte', 8), default=('bit', 1),
                        help='Display values in bytes instead of bits. Does '
                             'not affect the image generated by --share, nor '
                             'output from --json or --csv')
    parser.add_argument('-u', '--unit', default=None,
                        choices=['bps', 'kbps', 'Mbps', 'Gbps',
                                 'B/s', 'kB/s', 'MB/s', 'GB/s', 'auto'],
                        help='Output unit for speed (bps, kbps, Mbps, Gbps, '
                             'B/s, kB/s, MB/s, GB/s, auto)')
    parser.add_argument('-f', '--format', default=None,
                        choices=['human-readable', 'json', 'json-pretty', 'csv', 'tsv'],
                        help='Output format (human-readable, json, json-pretty, csv, tsv)')
    parser.add_argument('-p', '--progress', default=None,
                        help='Display progress meter (yes or no)')
    parser.add_argument('--no-progress', action='store_true', default=False,
                        help='Disable progress meter')
    parser.add_argument('--share', action='store_true',
                        help='Generate and provide a URL to the speedtest.net '
                             'share results image, not displayed with --csv')
    parser.add_argument('--simple', action='store_true', default=False,
                        help='Suppress verbose output, only show basic '
                             'information')
    parser.add_argument('--csv', action='store_true', default=False,
                        help='Suppress verbose output, only show basic '
                             'information in CSV format. Speeds listed in '
                             'bit/s and not affected by --bytes')
    parser.add_argument('--csv-delimiter', default=',', type=PARSER_TYPE_STR,
                        help='Single character delimiter to use in CSV '
                             'output. Default ","')
    parser.add_argument('--csv-header', action='store_true', default=False,
                        help='Print CSV headers')
    parser.add_argument('--json', action='store_true', default=False,
                        help='Suppress verbose output, only show basic '
                             'information in JSON format. Speeds listed in '
                             'bit/s and not affected by --bytes')
    parser.add_argument('-L', '--list', '--servers', dest='list', action='store_true',
                        help='Display a list of speedtest.net servers '
                             'sorted by distance')
    parser.add_argument('-s', '--server', '--server-id', dest='server',
                        type=PARSER_TYPE_INT, action='append',
                        help='Specify a server ID to test against. Can be '
                             'supplied multiple times')
    parser.add_argument('-o', '--host', default=None,
                        help='Specify a server host (e.g. host:port) to test against')
    parser.add_argument('--exclude', type=PARSER_TYPE_INT, action='append',
                        help='Exclude a server from selection. Can be '
                             'supplied multiple times')
    parser.add_argument('--mini', help='URL of the Speedtest Mini server')
    parser.add_argument('-i', '--source', '--ip', dest='source',
                        help='Source IP address to bind to')
    parser.add_argument('-I', '--interface', dest='interface',
                        help='Source network interface to bind to')
    parser.add_argument('--timeout', default=10, type=PARSER_TYPE_FLOAT,
                        help='HTTP timeout in seconds. Default 10')
    parser.add_argument('--secure', action='store_true',
                        help='Use HTTPS instead of HTTP when communicating '
                             'with speedtest.net operated servers')
    parser.add_argument('--no-pre-allocate', dest='pre_allocate',
                        action='store_const', default=True, const=False,
                        help='Do not pre allocate upload data. Pre allocation '
                             'is enabled by default to improve upload '
                             'performance. To support systems with '
                             'insufficient memory, use this option to avoid a '
                             'MemoryError')
    parser.add_argument('-V', '--version', action='store_true',
                        help='Show the version number and exit')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='Logging verbosity (can specify multiple times: -v, -vv)')
    parser.add_argument('--debug', action='store_true',
                        help=ARG_SUPPRESS, default=ARG_SUPPRESS)
    parser.add_argument('--selection-details', action='store_true', default=False,
                        help='Show details of the server selection')
    parser.add_argument('--selection', choices=['nearest', 'latency'], default='nearest',
                        help='Select nearest reachable server (default) or lowest-latency nearby server')
    parser.add_argument('--location', nargs=2, type=float, metavar=('LAT', 'LON'),
                        help='Override IP geolocation with your latitude and longitude')
    parser.add_argument('--accept-license', action='store_true', default=False,
                        help='Acknowledge license (for compatibility with Ookla CLI)')
    parser.add_argument('--accept-gdpr', action='store_true', default=False,
                        help='Acknowledge GDPR notice (for compatibility with Ookla CLI)')
    parser.add_argument('--official', action='store_true', default=False,
                        help='Force delegation to official Ookla speedtest CLI binary')
    parser.add_argument('--pure-python', action='store_true', default=False,
                        help='Force using the pure-Python speedtest engine')

    options = parser.parse_args()
    if isinstance(options, tuple):
        args = options[0]
    else:
        args = options
    return args


def validate_optional_args(args):
    """Check if an argument was provided that depends on a module that may
    not be part of the Python standard library.

    If such an argument is supplied, and the module does not exist, exit
    with an error stating which module is missing.
    """
    optional_args = {
        'json': ('json/simplejson python module', json),
        'secure': ('SSL support', HTTPSConnection),
    }

    for arg, info in optional_args.items():
        if getattr(args, arg, False) and info[1] is None:
            raise SystemExit('%s is not installed. --%s is '
                             'unavailable' % (info[0], arg))


def printer(string, quiet=False, debug=False, error=False, **kwargs):
    """Helper function print a string with various features"""

    if debug and not DEBUG:
        return

    if debug:
        if sys.stdout.isatty():
            out = '\033[1;30mDEBUG: %s\033[0m' % string
        else:
            out = 'DEBUG: %s' % string
    else:
        out = string

    if error:
        kwargs['file'] = sys.stderr

    if not quiet:
        print_(out, **kwargs)


def shell():
    """Run the full speedtest.net test"""

    global DEBUG
    shutdown_event = threading.Event()

    signal.signal(signal.SIGINT, ctrl_c(shutdown_event))

    args = parse_args()

    # Print the version and exit
    if args.version:
        version()

    # Official Ookla CLI delegation
    # By default, always use official Ookla CLI if available!
    official_cli = find_official_cli()
    custom_selection = args.location is not None or '--selection' in sys.argv or any(
        a.startswith('--selection=') for a in sys.argv)
    if args.official and custom_selection:
        raise SpeedtestCLIError('--selection and --location require the Python engine')
    if official_cli and not custom_selection and not args.pure_python and not os.environ.get('SPEEDTEST_PURE_PYTHON'):
        sys.exit(run_official_cli(official_cli))

    if args.official and not official_cli:
        raise SpeedtestCLIError(
            'Official Ookla speedtest CLI not found.\n'
            'To install the official Speedtest CLI:\n%s\n\n'
            'Or run with --pure-python to use the Python engine.' %
            OFFICIAL_INSTALL_CMD
        )

    if not args.download and not args.upload:
        raise SpeedtestCLIError('Cannot supply both --no-download and '
                                '--no-upload')

    if len(args.csv_delimiter) != 1:
        raise SpeedtestCLIError('--csv-delimiter must be a single character')

    if args.csv_header:
        csv_header(args.csv_delimiter)

    # Format handling
    if args.format:
        fmt = args.format.lower()
        if fmt == 'json':
            args.json = True
        elif fmt == 'json-pretty':
            args.json = True
            args.json_pretty = True
        elif fmt == 'csv':
            args.csv = True
        elif fmt == 'tsv':
            args.csv = True
            args.csv_delimiter = '\t'
        elif fmt == 'human-readable':
            args.json = False
            args.csv = False
            args.simple = False

    validate_optional_args(args)

    debug = getattr(args, 'debug', False)
    if debug == 'SUPPRESSHELP':
        debug = False
    if debug or getattr(args, 'verbose', 0) > 0:
        DEBUG = True

    if args.simple or args.csv or args.json:
        quiet = True
    else:
        quiet = False

    if args.csv or args.json:
        machine_format = True
    else:
        machine_format = False

    if not official_cli and not args.pure_python and not quiet and not machine_format:
        printer(
            'Notice: Official Ookla Speedtest CLI not found on system.\n'
            'To install the official Speedtest CLI:\n%s\n'
            'Running with pure-Python engine fallback (use --pure-python to suppress this notice)...\n' %
            OFFICIAL_INSTALL_CMD
        )

    show_progress = True
    if args.progress:
        p_val = str(args.progress).strip().lower()
        if p_val in ('no', 'false', '0', 'off'):
            show_progress = False
    if getattr(args, 'no_progress', False):
        show_progress = False

    # Don't set a callback if we are running quietly or progress is disabled
    if quiet or debug or not show_progress:
        callback = do_nothing
    else:
        callback = print_dots(shutdown_event)

    printer('Retrieving speedtest.net configuration...', quiet)
    try:
        speedtest = Speedtest(
            source_address=args.source,
            timeout=args.timeout,
            secure=args.secure,
            shutdown_event=shutdown_event,
            location=args.location
        )
    except (ConfigRetrievalError,) + HTTP_ERRORS:
        printer('Cannot retrieve speedtest configuration', error=True)
        raise SpeedtestCLIError(get_exception())

    if args.list:
        try:
            speedtest.get_servers(servers=args.server, exclude=args.exclude, host=args.host, full=True)
        except (ServersRetrievalError,) + HTTP_ERRORS:
            printer('Cannot retrieve speedtest server list', error=True)
            raise SpeedtestCLIError(get_exception())

        for _, servers in sorted(speedtest.servers.items()):
            for server in servers:
                line = ('%(id)5s) %(sponsor)s (%(name)s, %(country)s) '
                        '[%(d)0.2f km]' % server)
                try:
                    printer(line)
                except IOError:
                    e = get_exception()
                    if e.errno != errno.EPIPE:
                        raise
        sys.exit(0)

    printer('Testing from %(isp)s (%(ip)s)...' % speedtest.config['client'],
            quiet)

    if not args.mini:
        printer('Retrieving speedtest.net server list...', quiet)
        try:
            speedtest.get_servers(servers=args.server, exclude=args.exclude, host=args.host)
        except NoMatchedServers:
            if args.host:
                raise SpeedtestCLIError('No matched servers for host: %s' % args.host)
            raise SpeedtestCLIError(
                'No matched servers: %s' %
                ', '.join('%s' % s for s in (args.server or []))
            )
        except (ServersRetrievalError,) + HTTP_ERRORS:
            printer('Cannot retrieve speedtest server list', error=True)
            raise SpeedtestCLIError(get_exception())
        except InvalidServerIDType:
            raise SpeedtestCLIError(
                '%s is an invalid server type, must '
                'be an int' % ', '.join('%s' % s for s in args.server)
            )

        if args.server and len(args.server) == 1:
            printer('Retrieving information for the selected server...', quiet)
        else:
            printer('Selecting %s reachable server...' %
                    ('nearest' if args.selection == 'nearest' else 'lowest-latency'), quiet)
        speedtest.get_best_server(selection=args.selection)
    elif args.mini:
        speedtest.get_best_server(speedtest.set_mini_server(args.mini))

    results = speedtest.results

    if args.selection_details:
        printer('Server Selection Details:\n'
                '  Mode: ' + args.selection + '\n'
                '  Client coordinates: %s, %s\n' % speedtest.lat_lon +
                '  ID: %(id)s\n'
                '  Host: %(host)s\n'
                '  Sponsor: %(sponsor)s\n'
                '  Location: %(name)s, %(country)s\n'
                '  Latency: %(latency)s ms\n'
                '  Distance: %(d)0.2f km' % results.server, quiet)

    printer('Hosted by %(sponsor)s (%(name)s) [%(d)0.2f km]: '
            '%(latency)s ms' % results.server, quiet)

    if args.download:
        printer('Testing download speed', quiet,
                end=('', '\n')[bool(debug)])
        speedtest.download(
            callback=callback,
            threads=(None, 1)[args.single]
        )
        printer('Download: %s' %
                format_speed(results.download, unit=args.unit, units_tuple=args.units),
                quiet)
    else:
        printer('Skipping download test', quiet)

    if args.upload:
        printer('Testing upload speed', quiet,
                end=('', '\n')[bool(debug)])
        speedtest.upload(
            callback=callback,
            pre_allocate=args.pre_allocate,
            threads=(None, 1)[args.single]
        )
        printer('Upload: %s' %
                format_speed(results.upload, unit=args.unit, units_tuple=args.units),
                quiet)
    else:
        printer('Skipping upload test', quiet)

    printer('Results:\n%r' % results.dict(), debug=True)

    if not args.simple and args.share:
        results.share()

    if args.simple:
        dl_str = format_speed(results.download, unit=args.unit, units_tuple=args.units)
        ul_str = format_speed(results.upload, unit=args.unit, units_tuple=args.units)
        printer('Ping: %s ms\nDownload: %s\nUpload: %s' %
                (results.ping, dl_str, ul_str))
    elif args.csv:
        printer(results.csv(delimiter=args.csv_delimiter))
    elif args.json:
        printer(results.json(pretty=getattr(args, 'json_pretty', False)))

    if args.share and not machine_format:
        printer('Share results: %s' % results.share())


def main():
    try:
        shell()
    except KeyboardInterrupt:
        printer('\nCancelling...', error=True)
    except (SpeedtestException, SystemExit):
        e = get_exception()
        # Ignore a successful exit, or argparse exit
        if getattr(e, 'code', 1) not in (0, 2):
            msg = '%s' % e
            if not msg:
                msg = '%r' % e
            raise SystemExit('ERROR: %s' % msg)


if __name__ == '__main__':
    main()
