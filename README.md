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

## Data sources and attribution

The crawler reads public transport information from Hong Kong government and
transport-operator endpoints. The crawler source is based on
[HK Bus Crawling](https://github.com/hkbus/hk-bus-crawling), originally created
by its contributors, and is redistributed under the GNU General Public License
version 2. See `LICENSE` for the complete terms.

This repository is a standalone repository, not a GitHub fork. It retains the
upstream attribution required by the licence while publishing the generated
route database under this account.
