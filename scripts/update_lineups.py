#!/usr/bin/env python3
"""Pull this week's starting lineups from ESPN and write data/lineups.json.

For every starter: ESPN's projection, our own projection, injury status and bye.

Our projection is a weighted average of the player's recent games (recent weeks
count more, last season counts a little), pulled toward the average for his
position, then adjusted for bye weeks and injury status. Tuned on a 2026 Weeks 4-10
backtest (about 6.4 points of error per starter, vs 6.35 for ESPN).

Needs the same ESPN_S2 and SWID cookies as update_data.py.
"""
import datetime, json, os, sys, urllib.request, urllib.error
from pathlib import Path

LEAGUE_ID = int(os.environ.get("LEAGUE_ID", "1068706"))
OUT = Path(__file__).resolve().parent.parent / "data" / "lineups.json"
WEEKS = Path(__file__).resolve().parent.parent / "data" / "weeks.json"
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
SLOT = {0: "QB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR", 6: "TE", 7: "OP", 16: "D/ST", 17: "K", 23: "FLEX"}
SLOT_ORDER = [0, 2, 3, 4, 5, 6, 23, 7, 16, 17]
BENCH = {20, 21}
INJ = {"OUT": 0.0, "INJURY_RESERVE": 0.0, "SUSPENSION": 0.0, "DOUBTFUL": 0.25, "QUESTIONABLE": 0.85}
INJ_SHORT = {"OUT": "O", "INJURY_RESERVE": "IR", "SUSPENSION": "SSPD", "DOUBTFUL": "D", "QUESTIONABLE": "Q"}
DECAY, LAST_SEASON, PRIOR = 0.9, 0.7, 3.0


def cookie_check(e):
    if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403):
        sys.exit("ESPN refused the login cookies (HTTP %d). They have probably expired: log in to ESPN, copy espn_s2 and SWID, "
                 "and update the ESPN_S2 and SWID secrets in GitHub (Settings > Secrets and variables > Actions)." % e.code)


def get(url, filt=None):
    cookie = f"espn_s2={os.environ['ESPN_S2']}; SWID={os.environ['SWID']}"
    h = {"Cookie": cookie, "User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if filt:
        h["x-fantasy-filter"] = json.dumps(filt)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        cookie_check(e)
        raise


def main():
    for k in ("ESPN_S2", "SWID"):
        if not os.environ.get(k):
            sys.exit(f"Missing {k}.")
    today = datetime.date.today()
    year = int(os.environ.get("SEASON") or (today.year if today.month >= 8 else today.year - 1))
    lg = f"{BASE}/seasons/{year}/segments/0/leagues/{LEAGUE_ID}"
    settings = get(f"{lg}?view=mSettings")
    wk = settings.get("scoringPeriodId") or settings["status"]["currentMatchupPeriod"]
    final = settings["settings"]["scheduleSettings"].get("matchupPeriodCount", 14) + 4
    if settings["status"].get("isActive") is False or wk > final:
        print("Season not active; leaving lineups as they are.")
        return

    ros = get(f"{lg}?view=mRoster&view=mMatchup&scoringPeriodId={wk}")
    slot_counts = {int(k): v for k, v in settings["settings"]["rosterSettings"]["lineupSlotCounts"].items() if v and int(k) not in BENCH}
    # box scores for every finished week this season (starters' actual points and ESPN's pregame projection)
    boxes = {}
    for w in range(1, wk):
        b = get(f"{lg}?view=mBoxscore&view=mMatchupScore&scoringPeriodId={w}")
        boxes[w] = [m for m in b.get("schedule", []) if m.get("matchupPeriodId") == w and m.get("away")]
    pro = {p["id"]: p for p in get(f"{BASE}/seasons/{year}?view=proTeamSchedules_wl")["settings"]["proTeams"]}

    ids = {e["playerId"] for t in ros["teams"] for e in t["roster"]["entries"]}
    for ms in boxes.values():
        for m in ms:
            for side in (m["home"], m["away"]):
                ids |= {e["playerId"] for e in (side.get("rosterForCurrentScoringPeriod") or {}).get("entries", [])}
    ids = sorted(ids)
    hist = get(f"{lg}?view=kona_player_info", {"players": {"filterIds": {"value": ids},
               "filterStatsForTopScoringPeriodIds": {"value": 17, "additionalValue": [f"00{year}", f"10{year}"]}}})
    games = {}
    for p in hist["players"]:
        games[p["id"]] = [(s["seasonId"], s["scoringPeriodId"], s["appliedTotal"]) for s in p["player"].get("stats", [])
                          if s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 1]

    # prior = typical starter at the position: average of the top half of rostered players
    by_pos = {}
    for t in ros["teams"]:
        for e in t["roster"]["entries"]:
            pos = e["playerPoolEntry"]["player"]["defaultPositionId"]
            v = [v for (y, w, v) in games.get(e["playerId"], []) if y == year and w < wk]
            if len(v) >= 2:
                by_pos.setdefault(pos, []).append(sum(v) / len(v))
    pos_mean = {}
    for k, v in by_pos.items():
        top = sorted(v, reverse=True)[:max(1, round(len(v) / 2))]
        pos_mean[k] = sum(top) / len(top)

    def ours(pid, pos, target=None):
        target = target or wk
        sw = sv = 0.0
        for (y, w, v) in games.get(pid, []):
            if y == year and w >= target:
                continue
            age = (target - w) if y == year else (target + 18 - w)
            wt = DECAY ** age * (LAST_SEASON if y != year else 1.0)
            sw += wt
            sv += wt * v
        return (sv + PRIOR * pos_mean.get(pos, 10.0)) / (sw + PRIOR)

    uses_te_in_wr = any(e["lineupSlotId"] == 5 and e["playerPoolEntry"]["player"]["defaultPositionId"] == 4
                        for t in ros["teams"] for e in t["roster"]["entries"])
    out_teams = {}
    for t in ros["teams"]:
        rows = []
        for e in t["roster"]["entries"]:
            slot = e["lineupSlotId"]
            if slot in BENCH:
                continue
            pl = e["playerPoolEntry"]["player"]
            pos = pl["defaultPositionId"]
            pt = pro.get(pl.get("proTeamId"), {})
            bye = pt.get("byeWeek") == wk
            inj = pl.get("injuryStatus") or e.get("injuryStatus")
            espn = next((s["appliedTotal"] for s in pl.get("stats", [])
                         if s.get("scoringPeriodId") == wk and s.get("statSourceId") == 1 and s.get("statSplitTypeId") == 1), None)
            mine = 0.0 if bye else ours(e["playerId"], pos) * INJ.get(inj, 1.0)
            last = [round(v, 1) for (y, w, v) in sorted(games.get(e["playerId"], []), key=lambda g: (g[0], g[1]))
                    if y == year and w < wk][-5:]
            rows.append({
                "s": slot,
                "n": pl["fullName"],
                "pos": POS.get(pos, "?"),
                "pro": pt.get("abbrev", "FA"),
                "inj": INJ_SHORT.get(inj),
                "bye": bye or None,
                "espn": None if espn is None else round(espn, 1),
                "ours": round(mine, 1),
                "last": last,
            })
        rows.sort(key=lambda r: (SLOT_ORDER.index(r["s"]) if r["s"] in SLOT_ORDER else 99, -(r["espn"] or 0)))
        out_teams[str(t["id"])] = rows

    slot_names = dict(SLOT)
    if uses_te_in_wr:
        slot_names[5] = "WR/TE"
    new = {"y": year, "wk": wk, "slots": {str(k): v for k, v in slot_names.items()}, "teams": out_teams}
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    new["U"] = old.get("U") if {k: v for k, v in old.items() if k != "U"} == new else \
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # keep last week's final pregame snapshot of our projections before overwriting
    snaps = {}
    old_weeks = json.loads(WEEKS.read_text()) if WEEKS.exists() else {}
    if old_weeks.get("y") == year:
        snaps = old_weeks.get("snap", {})
    if old.get("y") == year and old.get("wk") and old["wk"] < wk:
        snaps[str(old["wk"])] = {tid: {r["n"]: r["ours"] for r in rows} for tid, rows in old.get("teams", {}).items()}
    OUT.write_text(json.dumps(new, separators=(",", ":")))

    # finished weeks: starters with actual, ESPN and our projection; bench and best-possible points
    def proj_of(pl, w, src):
        return next((s["appliedTotal"] for s in pl.get("stats", []) if s.get("scoringPeriodId") == w
                     and s.get("statSourceId") == src and s.get("statSplitTypeId") == 1), None)

    def optimal(entries, w):
        pool = [(proj_of(e["playerPoolEntry"]["player"], w, 0) or 0.0, set(e["playerPoolEntry"]["player"].get("eligibleSlots", [])), i)
                for i, e in enumerate(entries)]
        used, total = set(), 0.0
        for sl in sorted(slot_counts, key=lambda k: (k == 23 or k == 7, k)):  # flex-type slots last
            for _ in range(slot_counts[sl]):
                best = max((p for p in pool if p[2] not in used and sl in p[1]), default=None, key=lambda p: p[0])
                if best:
                    used.add(best[2])
                    total += best[0]
        return round(total, 2)

    weeks = {}
    for w, ms in boxes.items():
        wt = {}
        snap = snaps.get(str(w))
        for m in ms:
            for side in (m["home"], m["away"]):
                entries = (side.get("rosterForCurrentScoringPeriod") or {}).get("entries", [])
                st, bench = [], 0.0
                for e in entries:
                    pl = e["playerPoolEntry"]["player"]
                    act = proj_of(pl, w, 0) or 0.0
                    if e["lineupSlotId"] in BENCH:
                        bench += act
                        continue
                    esp = proj_of(pl, w, 1)
                    pos = pl["defaultPositionId"]
                    bye = pro.get(pl.get("proTeamId"), {}).get("byeWeek") == w
                    if snap and str(side["teamId"]) in snap and pl["fullName"] in snap[str(side["teamId"])]:
                        mine = snap[str(side["teamId"])][pl["fullName"]]
                    else:
                        # rebuilt after the fact: ESPN's 0 stands in for players known to be out
                        mine = 0.0 if bye or esp == 0 else ours(e["playerId"], pos, w)
                    st.append([e["lineupSlotId"], pl["fullName"], POS.get(pos, "?"), pro.get(pl.get("proTeamId"), {}).get("abbrev", "FA"),
                               round(act, 2), None if esp is None else round(esp, 1), round(mine, 1)])
                st.sort(key=lambda r: (SLOT_ORDER.index(r[0]) if r[0] in SLOT_ORDER else 99, -r[4]))
                wt[str(side["teamId"])] = {"st": st, "bench": round(bench, 2), "opt": optimal(entries, w)}
        weeks[str(w)] = wt
    WEEKS.write_text(json.dumps({"y": year, "snap": snaps, "weeks": weeks}, separators=(",", ":")))
    print(f"Week {wk}: {sum(len(v) for v in out_teams.values())} starters written; {len(weeks)} finished weeks.")


if __name__ == "__main__":
    main()
