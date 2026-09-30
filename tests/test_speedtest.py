#!/usr/bin/env python
# -*- coding: utf-8 -*-
import datetime
import io
import json
import re
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import speedtest


class TestServerSelection(unittest.TestCase):
    def setUp(self):
        with patch.object(speedtest.Speedtest, 'get_config'):
            self.test = speedtest.Speedtest(config={
                'client': {'lat': '0', 'lon': '0'},
                'ignore_servers': [], 'threads': {'download': 2}
            })
        self.test.lat_lon = (0, 0)

    def server(self, server_id, distance):
        return {'id': str(server_id), 'd': distance,
                'url': 'http://server%s.example/speedtest/upload.php' % server_id}

    def connection(self, host, **kwargs):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b'test=test'
        return connection

    def test_closest_list_is_rebuilt_and_ties_are_stable(self):
        near, far = self.server(1, 1), self.server(2, 2)
        self.test.servers = {1: [near], 2: [far]}
        self.assertEqual(self.test.get_closest_servers(1), [near])
        self.assertEqual(self.test.get_closest_servers(2), [near, far])
        self.assertEqual(self.test.get_closest_servers(1), [near])
        with self.assertRaises(ValueError):
            self.test.get_closest_servers(0)

    def test_nearest_server_wins_over_faster_distant_server(self):
        near, far = self.server(1, 1), self.server(2, 50)
        with patch.object(speedtest, 'SpeedtestHTTPConnection', side_effect=self.connection) as factory:
            with patch.object(speedtest.timeit, 'default_timer', side_effect=[0, .04, 1, 1.04, 2, 2.04]):
                best = self.test.get_best_server([far, near])
        self.assertEqual(best['id'], '1')
        self.assertEqual(best['latency'], 40)
        self.assertEqual(factory.call_count, 3)
        self.assertEqual(self.test.results.ping, 40)

    def test_dead_nearest_server_falls_back_and_connections_close(self):
        connections = []

        def connect(host, **kwargs):
            connection = self.connection(host, **kwargs)
            connections.append(connection)
            if host.startswith('server1.'):
                connection.getresponse.return_value.read.side_effect = OSError('offline')
            self.assertEqual(kwargs['timeout'], 3)
            return connection

        with patch.object(speedtest, 'SpeedtestHTTPConnection', side_effect=connect):
            best = self.test.get_best_server([self.server(1, 1), self.server(2, 2)])
        self.assertEqual(best['id'], '2')
        for connection in connections:
            connection.close.assert_called_once()
        paths = [c.request.call_args.args[1] for c in connections[:3]]
        self.assertEqual(len(set(paths)), 3)

    def test_all_failed_probes_raise_instead_of_selecting_dead_server(self):
        self.test._best = self.server(99, 99)
        with patch.object(speedtest, 'SpeedtestHTTPConnection', side_effect=OSError('offline')):
            with self.assertRaises(speedtest.SpeedtestBestServerFailure):
                self.test.get_best_server([self.server(1, 1)])
        self.assertEqual(self.test._best, {})
        self.assertEqual(self.test.results.server, {})

    def test_latency_mode_and_equal_latency_tie(self):
        near, far = self.server(1, 1), self.server(2, 2)
        with patch.object(speedtest, 'SpeedtestHTTPConnection', side_effect=self.connection):
            with patch.object(speedtest.timeit, 'default_timer', side_effect=[0, .04, 0, .04, 0, .04, 0, .01, 0, .01, 0, .01]):
                self.assertEqual(self.test.get_best_server([near, far], selection='latency')['id'], '2')
            with patch.object(speedtest.timeit, 'default_timer', side_effect=[0, .02] * 6):
                self.assertEqual(self.test.get_best_server([near, far], selection='latency')['id'], '1')

    def response(self, payload):
        response = io.BytesIO(payload)
        response.code = 200
        response.getheader = lambda key: None
        return response

    def test_modern_discovery_recalculates_distance_and_clears_cache(self):
        self.test.closest = [self.server(99, 0)]
        self.test._best = self.server(99, 0)
        payload = json.dumps([
            {'id': '2', 'lat': '0', 'lon': '1', 'url': 'http://far.example/upload.php', 'distance': 0},
            {'id': '1', 'lat': '0', 'lon': '.1', 'url': 'http://near.example/upload.php', 'distance': 999},
            {'id': 'bad', 'lat': '0', 'lon': '0'}
        ]).encode()
        with patch.object(speedtest, 'catch_request', return_value=(self.response(payload), False)) as fetch:
            self.test.get_servers()
        self.assertIn('/api/js/servers?', fetch.call_args.args[0].full_url)
        self.assertEqual(self.test.get_closest_servers(1)[0]['id'], '1')
        self.assertEqual(self.test._best, {})

    def test_empty_modern_discovery_falls_back_to_xml(self):
        xml = b'<settings><servers><server id="1" lat="0" lon="0" url="http://near.example/upload.php" /></servers></settings>'
        with patch.object(speedtest, 'catch_request', side_effect=[
                (self.response(b'[]'), False), (self.response(xml), False)]) as fetch:
            self.test.get_servers()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(self.test.get_closest_servers()[0]['id'], '1')

    def test_location_override_and_invalid_coordinates(self):
        with patch.object(speedtest.Speedtest, 'get_config'):
            test = speedtest.Speedtest(config={'client': {}}, location=(30.9, 75.85))
            self.assertEqual(test.lat_lon, (30.9, 75.85))
            with self.assertRaises(speedtest.SpeedtestConfigError):
                speedtest.Speedtest(config={'client': {}}, location=(91, 0))

    def test_upload_body_matches_content_length(self):
        for length in (32768, 65536, 131072, 262144, 7340032):
            data = speedtest.HTTPUploaderData(length, 0, 10)
            data.pre_allocate()
            self.assertEqual(len(data.data.getvalue()), length)

    def test_transfer_workers_complete_with_bounded_concurrency(self):
        self.test._best = self.server(1, 0)
        self.test.config.update({
            'sizes': {'download': [350], 'upload': [32768]},
            'counts': {'download': 4, 'upload': 4},
            'threads': {'download': 2, 'upload': 2},
            'length': {'download': 1, 'upload': 1},
            # More configured uploads than requests must not hang the consumer.
            'upload_max': 8
        })

        def download(worker):
            worker.result = [65536]

        def upload(worker):
            worker.result = worker.size

        with patch.object(speedtest.HTTPDownloader, 'run', download):
            self.assertGreater(self.test.download(threads=2), 0)
        with patch.object(speedtest.HTTPUploader, 'run', upload):
            self.assertGreater(self.test.upload(threads=2), 0)
        self.assertEqual(self.test.results.bytes_received, 4 * 65536)
        self.assertEqual(self.test.results.bytes_sent, 4 * 32768)
        with patch.object(speedtest.HTTPDownloader, 'run', lambda worker: None):
            with self.assertRaises(speedtest.SpeedtestCLIError):
                self.test.download(threads=2)

    def test_invalid_timeout_is_rejected_before_network_access(self):
        for timeout in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(speedtest.SpeedtestConfigError):
                speedtest.Speedtest(timeout=timeout)


class TestSpeedtestCLI(unittest.TestCase):
    def test_version_constants(self):
        self.assertTrue(hasattr(speedtest, '__version__'))
        self.assertEqual(speedtest.__version__, '2.2.0')
        self.assertTrue(hasattr(speedtest, 'PY311PLUS'))
        self.assertTrue(hasattr(speedtest, 'PY312PLUS'))
        self.assertTrue(hasattr(speedtest, 'PY313PLUS'))
        self.assertTrue(hasattr(speedtest, 'PY314PLUS'))

    def test_official_install_instructions(self):
        self.assertIn('sudo apt-get remove speedtest-cli', speedtest.OFFICIAL_INSTALL_CMD)
        self.assertIn('sudo apt-get install curl', speedtest.OFFICIAL_INSTALL_CMD)
        self.assertIn('curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | sudo bash', speedtest.OFFICIAL_INSTALL_CMD)
        self.assertIn('sudo apt-get install speedtest', speedtest.OFFICIAL_INSTALL_CMD)

    def test_timestamp_utc_isoformat(self):
        results = speedtest.SpeedtestResults()
        self.assertTrue(results.timestamp.endswith('Z'))
        # Ensure it parses as ISO datetime
        clean_ts = results.timestamp[:-1]
        dt = datetime.datetime.fromisoformat(clean_ts)
        self.assertIsInstance(dt, datetime.datetime)

    def test_format_speed_defaults(self):
        # 100 Mbps = 100,000,000 bits/sec
        s = speedtest.format_speed(100000000)
        self.assertEqual(s, '100.00 Mbit/s')

    def test_format_speed_units(self):
        speed_bps = 50000000.0  # 50 Mbit/s
        self.assertEqual(speedtest.format_speed(speed_bps, unit='Mbps'), '50.00 Mbit/s')
        self.assertEqual(speedtest.format_speed(speed_bps, unit='kbps'), '50000.00 kbps')
        self.assertEqual(speedtest.format_speed(speed_bps, unit='bps'), '50000000.00 bps')
        self.assertEqual(speedtest.format_speed(speed_bps, unit='Gbps'), '0.05 Gbit/s')
        self.assertEqual(speedtest.format_speed(speed_bps, unit='MB/s'), '6.25 MB/s')
        self.assertEqual(speedtest.format_speed(speed_bps, unit='kB/s'), '6250.00 kB/s')
        self.assertEqual(speedtest.format_speed(speed_bps, unit='B/s'), '6250000.00 B/s')
        self.assertEqual(speedtest.format_speed(speed_bps, unit='auto'), '50.00 Mbit/s')

    def test_format_speed_auto_gigabit(self):
        speed_bps = 2500000000.0  # 2.5 Gbps
        self.assertEqual(speedtest.format_speed(speed_bps, unit='auto'), '2.50 Gbit/s')

    def test_distance_calculation(self):
        # Distance between London (51.5074, -0.1278) and Paris (48.8566, 2.3522) ~ 343 km
        d = speedtest.distance((51.5074, -0.1278), (48.8566, 2.3522))
        self.assertAlmostEqual(d, 343.5, delta=10.0)

    def test_build_user_agent(self):
        ua = speedtest.build_user_agent()
        self.assertIn('speedtest-cli/', ua)
        self.assertIn('Python/', ua)

    def test_results_serialization(self):
        results = speedtest.SpeedtestResults(
            download=50000000,
            upload=20000000,
            ping=15.5,
            server={'id': '1234', 'name': 'Test Server', 'sponsor': 'Tester', 'country': 'Testland', 'host': 'test.com:8080', 'latency': 15.5},
            client={'ip': '1.2.3.4', 'isp': 'Test ISP', 'lat': 0.0, 'lon': 0.0}
        )
        # dict()
        d = results.dict()
        self.assertEqual(d['download'], 50000000)
        self.assertEqual(d['upload'], 20000000)
        self.assertEqual(d['ping'], 15.5)

        # json()
        j = json.loads(results.json())
        self.assertEqual(j['download'], 50000000)

        # csv()
        c = results.csv()
        self.assertIn('1234', c)
        self.assertIn('50000000', c)

        # tsv()
        t = results.tsv()
        self.assertIn('\t', t)
        self.assertIn('1234', t)

    def test_parse_args_server_id_aliases(self):
        sys_argv_orig = sys.argv[:]
        try:
            sys.argv = ['speedtest', '-s', '1234', '--server-id', '5678']
            args = speedtest.parse_args()
            self.assertEqual(args.server, [1234, 5678])
        finally:
            sys.argv = sys_argv_orig

    def test_parse_args_servers_list_aliases(self):
        sys_argv_orig = sys.argv[:]
        try:
            sys.argv = ['speedtest', '-L']
            args = speedtest.parse_args()
            self.assertTrue(args.list)

            sys.argv = ['speedtest', '--servers']
            args = speedtest.parse_args()
            self.assertTrue(args.list)
        finally:
            sys.argv = sys_argv_orig

    def test_parse_args_format_and_units(self):
        sys_argv_orig = sys.argv[:]
        try:
            sys.argv = ['speedtest', '-f', 'json-pretty', '-u', 'MB/s', '-p', 'no']
            args = speedtest.parse_args()
            self.assertEqual(args.format, 'json-pretty')
            self.assertEqual(args.unit, 'MB/s')
            self.assertEqual(args.progress, 'no')
        finally:
            sys.argv = sys_argv_orig

    def test_parse_args_official_cli_compatibility_flags(self):
        sys_argv_orig = sys.argv[:]
        try:
            sys.argv = ['speedtest', '--accept-license', '--accept-gdpr', '-v', '--selection-details', '--pure-python']
            args = speedtest.parse_args()
            self.assertTrue(args.accept_license)
            self.assertTrue(args.accept_gdpr)
            self.assertEqual(args.verbose, 1)
            self.assertTrue(args.selection_details)
            self.assertTrue(args.pure_python)
        finally:
            sys.argv = sys_argv_orig

    def test_find_official_cli_returns_valid_or_none(self):
        path = speedtest.find_official_cli()
        # Should be None if not installed, or non-empty string path
        self.assertTrue(path is None or isinstance(path, str))

    def test_cli_version_flag(self):
        p = subprocess.run(
            [sys.executable, 'speedtest.py', '--version'],
            capture_output=True,
            text=True
        )
        self.assertEqual(p.returncode, 0)
        self.assertIn('speedtest-cli', p.stdout)
        self.assertIn('Python', p.stdout)
        self.assertIn('DownloaderZone', p.stdout)

    def test_cli_help_flag(self):
        p = subprocess.run(
            [sys.executable, 'speedtest.py', '--help'],
            capture_output=True,
            text=True
        )
        self.assertEqual(p.returncode, 0)
        self.assertIn('--format', p.stdout)
        self.assertIn('--unit', p.stdout)
        self.assertIn('--official', p.stdout)
        self.assertIn('--pure-python', p.stdout)


if __name__ == '__main__':
    unittest.main()
