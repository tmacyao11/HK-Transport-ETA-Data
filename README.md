# HK Transport ETA Data

This repository independently generates and hosts the route database used by
`tmacyao11/HK-Transport-ETA`.

## Published files

The scheduled GitHub Actions workflow rebuilds the database twice daily. The
app downloads only these files from the `gh-pages` branch:

- `routeFareList.min.json`
- `routeFareList.md5`

Raw base URL:

```text
https://raw.githubusercontent.com/tmacyao11/HK-Transport-ETA-Data/gh-pages
```

## Request handling

Transient network errors and retryable HTTP responses are retried up to ten
attempts with exponential backoff and jitter, honoring `Retry-After`. Permanent
failures stop the build before publication, preserving the last valid database.
All green minibus HTTP attempts share a concurrency limit and request pacing,
including nested route variants and retries. The workflow sets
`GMB_REQUEST_LIMIT=2` and `GMB_REQUEST_INTERVAL=0.5` seconds; 403/429 responses
also pause new minibus requests during the retry cooldown. Minibus connections
use a 30-second timeout.

Run the request regression tests after installing `crawling/requirements.txt`:

```sh
python -m unittest discover -s tests -v
```

## Data sources and attribution

The crawler reads public transport information from Hong Kong government and
transport-operator endpoints. The crawler source is based on
[HK Bus Crawling](https://github.com/hkbus/hk-bus-crawling), originally created
by its contributors, and is redistributed under the GNU General Public License
version 2. See `LICENSE` for the complete terms.

This repository is a standalone repository, not a GitHub fork. It retains the
upstream attribution required by the licence while publishing the generated
route database under this account.
