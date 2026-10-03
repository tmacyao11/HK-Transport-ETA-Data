import json
import httpx
import asyncio
import logging
import os
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)


class RequestLimiter:
  """Limit every HTTP attempt, including nested requests and retries."""

  def __init__(self, limit=2, interval=0.5):
    if limit < 1 or interval < 0:
      raise ValueError("Request limit must be positive and interval non-negative")
    self._slots = asyncio.Semaphore(limit)
    self._lock = asyncio.Lock()
    self._interval = interval
    self._next_request = 0

  def defer(self, seconds):
    self._next_request = max(
        self._next_request, asyncio.get_running_loop().time() + seconds)

  async def get(self, client, url, headers):
    async with self._slots:
      async with self._lock:
        loop = asyncio.get_running_loop()
        # Another in-flight request can extend the shared cooldown while waiting.
        while (delay := self._next_request - loop.time()) > 0:
          await asyncio.sleep(delay)
        self._next_request = loop.time() + self._interval
      return await client.get(url, headers=headers)


def retry_after_seconds(value):
  if not value:
    return 0
  try:
    return max(0, float(value))
  except ValueError:
    try:
      date = parsedate_to_datetime(value)
      if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
      return max(0, (date - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
      return 0


async def emitRequest(url: str, client: httpx.AsyncClient, headers=None,
                      *, limiter=None, max_attempts=10):
  if max_attempts < 1:
    raise ValueError("max_attempts must be positive")
  for attempt in range(1, max_attempts + 1):
    retry_after = 0
    throttled = False
    try:
      if limiter is None:
        r = await client.get(url, headers=headers)
      else:
        r = await limiter.get(client, url, headers)
      if r.status_code == 200:
        return r
      if r.status_code not in (403, 408, 429, 500, 502, 503, 504):
        r.raise_for_status()
        raise RuntimeError(f"Unexpected status_code={r.status_code}. URL={url}")
      error = httpx.HTTPStatusError(
          f"status_code={r.status_code}", request=r.request, response=r)
      retry_after = retry_after_seconds(r.headers.get('Retry-After'))
      throttled = r.status_code in (403, 429)
    except (httpx.TimeoutException, httpx.NetworkError,
            httpx.RemoteProtocolError) as exc:
      error = exc
    if attempt == max_attempts:
      raise RuntimeError(
          f"Request failed after {max_attempts} attempts. URL={url}") from error
    backoff = min(2 ** (attempt - 1), 120)
    delay = max(backoff + random.uniform(0, backoff * 0.25), retry_after)
    if limiter is not None and throttled:
      limiter.defer(delay)
    logger.warning(
        f"{error!r}, attempt {attempt}/{max_attempts}, wait {delay:.1f}s and retry. URL={url}")
    await asyncio.sleep(delay)


def get_request_limit():
  default_limit = "10"
  return int(os.environ.get('REQUEST_LIMIT', default_limit))


def store_version(key: str, version: str):
  logger.info(f"{key} version: {version}")
  # "0" is prepended in filename so that this file appears first in Github directory listing
  try:
    with open('0versions.json', 'r') as f:
      version_dict = json.load(f)
  except BaseException:
    version_dict = {}
  version_dict[key] = version
  version_dict = dict(sorted(version_dict.items()))
  with open('0versions.json', 'w', encoding='UTF-8') as f:
    json.dump(version_dict, f, indent=4)
