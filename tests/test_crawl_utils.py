import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'crawling'))
from crawl_utils import RequestLimiter, emitRequest, retry_after_seconds
import gmb

URL = 'https://data.etagmb.gov.hk/route/NT/89P'


def response(status, headers=None):
  return httpx.Response(status, headers=headers, request=httpx.Request('GET', URL))


class RequestTests(unittest.IsolatedAsyncioTestCase):
  async def test_transient_connection_timeout_and_protocol_errors_recover(self):
    for error_type in (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                       httpx.PoolTimeout, httpx.ReadError, httpx.RemoteProtocolError):
      with self.subTest(error=error_type.__name__):
        client = AsyncMock()
        client.get.side_effect = [error_type('temporary', request=httpx.Request('GET', URL)),
                                  response(200)]
        with patch('crawl_utils.asyncio.sleep', new_callable=AsyncMock) as sleep:
          self.assertEqual((await emitRequest(URL, client)).status_code, 200)
        self.assertEqual(client.get.await_count, 2)
        sleep.assert_awaited_once()

  async def test_rate_limit_defers_other_requests_and_honors_retry_after(self):
    for status in (403, 429):
      with self.subTest(status=status):
        limiter = AsyncMock()
        limiter.defer = Mock()
        limiter.get.side_effect = [response(status, {'Retry-After': '20'}), response(200)]
        with patch('crawl_utils.asyncio.sleep', new_callable=AsyncMock) as sleep:
          await emitRequest(URL, AsyncMock(), limiter=limiter)
        sleep.assert_awaited_once_with(20)
        limiter.defer.assert_called_once_with(20)
        self.assertEqual(limiter.get.await_count, 2)

  async def test_service_unavailable_retries(self):
    client = AsyncMock()
    client.get.side_effect = [response(503), response(200)]
    with patch('crawl_utils.asyncio.sleep', new_callable=AsyncMock):
      self.assertEqual((await emitRequest(URL, client)).status_code, 200)

  async def test_permanent_http_error_fails_immediately(self):
    client = AsyncMock()
    client.get.return_value = response(404)
    with patch('crawl_utils.asyncio.sleep', new_callable=AsyncMock) as sleep:
      with self.assertRaises(httpx.HTTPStatusError):
        await emitRequest(URL, client)
    self.assertEqual(client.get.await_count, 1)
    sleep.assert_not_awaited()

  async def test_exhaustion_preserves_error_and_url_without_final_sleep(self):
    for failure in (httpx.ConnectError('offline'), response(429)):
      client = AsyncMock()
      client.get.side_effect = [failure, failure, failure]
      with patch('crawl_utils.asyncio.sleep', new_callable=AsyncMock) as sleep:
        with self.assertRaisesRegex(RuntimeError, 'after 3 attempts.*89P') as caught:
          await emitRequest(URL, client, max_attempts=3)
      self.assertEqual(client.get.await_count, 3)
      self.assertEqual(sleep.await_count, 2)
      self.assertIsInstance(caught.exception.__cause__, httpx.HTTPError)

  async def test_cancellation_is_not_retried(self):
    client = AsyncMock()
    client.get.side_effect = asyncio.CancelledError()
    with self.assertRaises(asyncio.CancelledError):
      await emitRequest(URL, client)
    self.assertEqual(client.get.await_count, 1)

  async def test_all_parallel_requests_share_concurrency_limit(self):
    active = peak = 0

    async def get(*args, **kwargs):
      nonlocal active, peak
      active += 1
      peak = max(peak, active)
      await asyncio.sleep(0.01)
      active -= 1
      return response(200)

    client = AsyncMock()
    client.get.side_effect = get
    limiter = RequestLimiter(limit=2, interval=0)
    await asyncio.gather(*[emitRequest(URL, client, limiter=limiter) for _ in range(12)])
    self.assertEqual(peak, 2)

  async def test_request_starts_are_spaced(self):
    starts = []

    async def get(*args, **kwargs):
      starts.append(asyncio.get_running_loop().time())
      return response(200)

    client = AsyncMock()
    client.get.side_effect = get
    limiter = RequestLimiter(limit=2, interval=0.02)
    await asyncio.gather(*[emitRequest(URL, client, limiter=limiter) for _ in range(4)])
    self.assertTrue(all(b - a >= 0.019 for a, b in zip(starts, starts[1:])))

  async def test_cooldown_can_extend_while_request_waits(self):
    limiter = RequestLimiter(limit=2, interval=0)
    client = AsyncMock()
    client.get.return_value = response(200)
    limiter.defer(0.02)
    pending = asyncio.create_task(emitRequest(URL, client, limiter=limiter))
    await asyncio.sleep(0.005)
    extended_at = asyncio.get_running_loop().time()
    limiter.defer(0.04)
    await pending
    self.assertGreaterEqual(asyncio.get_running_loop().time() - extended_at, 0.039)

  async def test_minibus_nested_routes_and_stops_use_one_limiter(self):
    observed_limiters = []
    paths = []

    async def fetch(url, client, *, limiter):
      observed_limiters.append(limiter)
      path = httpx.URL(url).path
      paths.append(path)
      if path.startswith('/route-stop/'):
        data = {'route_stops': [{'stop_id': 1, 'name_en': 'Test Stop', 'name_tc': '測試站'}]}
      elif path.startswith('/stop/'):
        data = {'coordinates': {'wgs84': {'latitude': 22.3, 'longitude': 114.2}}}
      elif path.count('/') == 2:
        data = {'routes': ['1']}
      else:
        direction = {'route_seq': 1, 'orig_tc': '起點', 'orig_en': 'Origin',
                     'dest_tc': '終點', 'dest_en': 'Destination', 'headways': []}
        data = [{'route_id': route_id, 'description_tc': '正常班次',
                 'directions': [direction]} for route_id in (100, 101)]
      return httpx.Response(200, json={'data': data}, request=httpx.Request('GET', url))

    original_dir = Path.cwd()
    with tempfile.TemporaryDirectory() as directory:
      try:
        os.chdir(directory)
        Path('gtfs').mkdir()
        Path('gtfs/calendar.txt').write_text(
            'service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday\n'
            '1,1,1,1,1,1,1,1\n')
        Path('gtfs.json').write_text(json.dumps({'stopList': {}}))
        with patch('gmb.emitRequest', side_effect=fetch):
          await gmb.getRouteStop('gmb', AsyncMock())
        routes = json.loads(Path('routeList.gmb.json').read_text())
        stops = json.loads(Path('stopList.gmb.json').read_text())
      finally:
        os.chdir(original_dir)
    self.assertEqual(len(routes), 6)
    self.assertEqual(stops['1']['lat'], 22.3)
    self.assertEqual(len(observed_limiters), 13)
    self.assertTrue(all(limiter is observed_limiters[0] for limiter in observed_limiters))
    self.assertIn('/stop/1', paths)


class RetryAfterTests(unittest.TestCase):
  def test_seconds_and_invalid_header(self):
    self.assertEqual(retry_after_seconds('25'), 25)
    self.assertEqual(retry_after_seconds('-2'), 0)
    self.assertEqual(retry_after_seconds('invalid'), 0)
    self.assertEqual(retry_after_seconds(None), 0)

  def test_http_date(self):
    header = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    self.assertGreater(retry_after_seconds(header), 28)
    self.assertLessEqual(retry_after_seconds(header), 30)


if __name__ == '__main__':
  unittest.main()
