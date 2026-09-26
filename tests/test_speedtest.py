#!/usr/bin/env python
# -*- coding: utf-8 -*-
import datetime
import io
import json
import re
import subprocess
import sys
import unittest

import speedtest


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
