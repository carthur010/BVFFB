# BVFFB

The history site for Bella Vista Fantasy Football: every season since 2011, pulled from ESPN.

**Live site:** https://carthur010.github.io/BVFFB/

## What's in it

- **League History:** all-time standings, the champions, league ratings, a page for every season, and scoring and parity trends
- **Record Book:** single game, season, playoff, streak and career records
- **Franchises:** a career page for every manager, with season log, rivalries and signature games
- **Head-to-Head:** the full rivalry grid, plus a Versus page for any two managers
- **Achievements:** trophies and follies, earned straight from the results

## How it works

| File | Job |
| --- | --- |
| `index.html` | The whole site. Plain HTML, CSS and JavaScript, no build step. |
| `data/league.json` | Every team, record and matchup, in a compact format. |
| `scripts/update_data.py` | Pulls all seasons from ESPN and rewrites `data/league.json`. |
| `.github/workflows/update-data.yml` | Runs the script every Tuesday and Wednesday morning and commits new results. |

## Turning on the weekly refresh

The league is private on ESPN, so the refresh needs two cookies from a logged-in ESPN session.

1. Log in at fantasy.espn.com in Chrome.
2. Open DevTools, go to **Application > Cookies > https://fantasy.espn.com**, and copy the values of `espn_s2` and `SWID` (keep the curly braces on SWID).
3. In this repo, go to **Settings > Secrets and variables > Actions > New repository secret** and add `ESPN_S2` and `SWID`.
4. Go to **Actions > Refresh league data from ESPN > Run workflow** to test it.

ESPN cookies last about a year. If the refresh starts failing, grab fresh ones.

## Running it locally

```bash
python3 -m http.server   # then open http://localhost:8000
ESPN_S2=... SWID=... python3 scripts/update_data.py   # refresh the data by hand
```

## Data notes

- A few managers have more than one ESPN account over the years. The script merges them by name (see `NAME_FIX` and `ID_FIX` in the script).
- 2012 had no playoff, so the best regular season record is the champion.
- Playoff games in 2011, 2013 and 2014 were two-week totals. They count for wins and losses but are left out of single-game records.
- Consolation games are excluded everywhere.
