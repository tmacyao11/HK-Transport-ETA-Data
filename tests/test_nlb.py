import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'crawling'))
import nlb


class NlbTests(unittest.IsolatedAsyncioTestCase):
  async def test_numeric_and_string_ids_remain_usable_after_json_round_trip(self):
    for id_type in (int, str):
      with self.subTest(id_type=id_type.__name__):
        requested_urls = []

        def respond(request):
          requested_urls.append(str(request.url))
          if request.url.path.endswith('/route.php'):
            routes = [{
                'routeId': id_type(route_id), 'routeNo': '1',
                'routeName_e': 'Tai O > Mui Wo Ferry Pier',
                'routeName_c': '大澳 > 梅窩碼頭',
                'overnightRoute': 0, 'specialRoute': 0,
            } for route_id in (2, 3)]
            return httpx.Response(200, json={'routes': routes})
          self.assertEqual(request.url.path, '/v2/transport/nlb/stop.php')
          self.assertIn(request.url.params['routeId'], ('2', '3'))
          stops = [{
              'stopId': id_type(stop_id), 'stopName_e': 'Test Stop',
              'stopName_c': '測試站', 'latitude': 22.25, 'longitude': 113.86,
              'fare': '11.2', 'fareHoliday': '17.5',
          } for stop_id in (1, 2)]
          return httpx.Response(200, json={'stops': stops})

        original_dir = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
          try:
            os.chdir(directory)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
              with patch('nlb.httpx.AsyncClient', return_value=client):
                await nlb.getRouteStop('nlb')
            routes = json.loads(Path('routeList.nlb.json').read_text())
            stops = json.loads(Path('stopList.nlb.json').read_text())
          finally:
            os.chdir(original_dir)

        self.assertEqual(len(requested_urls), 3)
        self.assertEqual([route['id'] for route in routes], ['2', '3'])
        self.assertEqual(set(stops), {'1', '2'})
        for route in routes:
          self.assertEqual(route['stops'], ['1', '2'])
          self.assertEqual(route['service_type'], '1')
          self.assertEqual(route['fares'], ['11.2'])
          self.assertEqual(route['faresHoliday'], ['17.5'])
          for stop_id in route['stops']:
            self.assertEqual(stops[stop_id]['stop'], stop_id)


if __name__ == '__main__':
  unittest.main()
