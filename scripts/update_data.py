#!/usr/bin/env python3
"""Pull every BVFFB season from ESPN and write data/league.json.

Needs two cookies from a logged-in ESPN session (the league is private):
  ESPN_S2  and  SWID   (set as environment variables / GitHub secrets)

Usage:  ESPN_S2=... SWID=... python3 scripts/update_data.py
"""
import datetime, json, os, sys, urllib.request, urllib.error
from pathlib import Path

LEAGUE_ID = int(os.environ.get("LEAGUE_ID", "1068706"))
OUT = Path(__file__).resolve().parent.parent / "data" / "league.json"
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
VIEWS = "view=mTeam&view=mMatchupScore&view=mSettings&view=mStandings"

# Same person, different ESPN accounts or spellings
NAME_FIX = {
    "Alexandros Sargetis": "Alex Sargettis",
    "lefteris sargetis": "Lefteris Sargetis",
    "will gonzales": "Will Gonzales",
    "Hibbs Sam": "Sam Hibbs",
}
# Accounts with no first/last name on file
ID_FIX = {"{B1CC5652-EA20-431A-BC8E-E5BD2EC5D585}": "Nick Mosbaugh"}


def get(url):
    cookie = f"espn_s2={os.environ['ESPN_S2']}; SWID={os.environ['SWID']}"
    req = urllib.request.Request(url, headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def season(year):
    try:
        return get(f"{BASE}/seasons/{year}/segments/0/leagues/{LEAGUE_ID}?{VIEWS}")
    except urllib.error.HTTPError:
        j = get(f"{BASE}/leagueHistory/{LEAGUE_ID}?seasonId={year}&{VIEWS}")
        return j[0] if isinstance(j, list) else j


def r2(x):
    return None if x is None else round(x * 100) / 100


def num(x):
    # match JS JSON output: 129.0 -> 129
    if x is None:
        return None
    return int(x) if float(x).is_integer() else x


def main():
    for k in ("ESPN_S2", "SWID"):
        if not os.environ.get(k):
            sys.exit(f"Missing {k}. Copy it from your ESPN cookies.")
    today = datetime.date.today()
    season_year = int(os.environ.get("SEASON") or (today.year if today.month >= 8 else today.year - 1))
    cur = get(f"{BASE}/seasons/{season_year}/segments/0/leagues/{LEAGUE_ID}?view=mSettings")
    years = sorted(set(cur["status"]["previousSeasons"]) | {cur["seasonId"]})
    raw = {y: season(y) for y in years}

    names = {}
    for j in raw.values():
        for m in j.get("members", []):
            n = f"{m.get('firstName') or ''} {m.get('lastName') or ''}".strip()
            if n:
                names[m["id"]] = n
    names.update(ID_FIX)

    managers, out = [], {}

    def mi(n):
        if n not in managers:
            managers.append(n)
        return managers.index(n)

    for y in years:
        j = raw[y]
        s = j.get("settings", {}).get("scheduleSettings", {})
        teams = []
        for t in j.get("teams", []):
            owners = t.get("owners") or ([t["primaryOwner"]] if t.get("primaryOwner") else [])
            ns = sorted({NAME_FIX.get(names.get(o, "Unknown"), names.get(o, "Unknown")) for o in owners})
            name = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}"
            rec = t.get("record", {}).get("overall", {})
            teams.append([t["id"], mi(" & ".join(ns)), " ".join(name.split()),
                          t.get("rankCalculatedFinal") or t.get("finalStandingsPosition") or None,
                          t.get("playoffSeed"), rec.get("wins"), rec.get("losses"), rec.get("ties"),
                          num(r2(rec.get("pointsFor"))), num(r2(rec.get("pointsAgainst")))])
        games = []
        for m in j.get("schedule", []):
            h, a = m.get("home") or {}, m.get("away") or {}
            tier = m.get("playoffTierType", "NONE")
            if not a.get("teamId") or not h.get("teamId") or tier not in ("NONE", "WINNERS_BRACKET"):
                continue
            if m.get("winner") == "UNDECIDED":
                # upcoming or in-progress regular season game: keep the pairing, not the partial score
                if tier == "NONE":
                    games.append(",".join(str(v) for v in [m["matchupPeriodId"], h["teamId"], 0, a["teamId"], 0, "U"]))
                continue
            games.append(",".join(str(v) for v in [m["matchupPeriodId"], h["teamId"], num(r2(h.get("totalPoints"))),
                                                    a["teamId"], num(r2(a.get("totalPoints"))), "R" if tier == "NONE" else "P"]))
        out[str(y)] = {"reg": s.get("matchupPeriodCount"), "po": s.get("playoffTeamCount"), "T": teams, "G": ";".join(games)}

    OUT.write_text(json.dumps({"M": managers, "S": out}, separators=(",", ":"), ensure_ascii=False))
    print(f"Wrote {OUT} · {len(years)} seasons · {len(managers)} managers")


if __name__ == "__main__":
    main()
