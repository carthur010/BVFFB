#!/usr/bin/env python3
"""Pull draft, trade and player-level history from ESPN and write data/history.json.

  D  drafts, every season:        {year: [[overall, round, teamId, playerId, seasonPoints, keeper, auctionPrice], ...]}
  T  trades (ESPN keeps these from 2018 on): [[year, week, "YYYY-MM-DD", [[playerId, fromTeam, toTeam, startedPts, totalPts, starts], ...]], ...]
     startedPts / totalPts / starts are what the player did for his new team for the rest of that season.
  W  top single-week starter performances (2018 on): [[year, week, teamId, playerId, points, slot], ...]
  R  starts by team and player (2018 on): [[year, teamId, playerId, starts, points], ...]
  A  waiver and free-agent adds (2018 on), kept when the player started or cost budget:
       [[year, week, teamId, playerId, bid, startedPts, totalPts, starts], ...]
  AC add counts: [[year, teamId, adds, budgetSpent], ...]
  G  ones that got away: drops who then started for someone else [[year, week, fromTeam, playerId, toTeam, startedPts, starts], ...]
  P  player names: {playerId: [name, position]}

Finished seasons are kept from the previous file, so only the current season is pulled each week.
Needs ESPN_S2 and SWID, like update_data.py.
"""
import datetime, json, os, sys, urllib.request, urllib.error
from pathlib import Path

LEAGUE_ID = int(os.environ.get("LEAGUE_ID", "1068706"))
OUT = Path(__file__).resolve().parent.parent / "data" / "history.json"
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
BENCH = {20, 21}
FIRST_BOX_YEAR = 2018


def get(url, filt=None):
    cookie = f"espn_s2={os.environ['ESPN_S2']}; SWID={os.environ['SWID']}"
    h = {"Cookie": cookie, "User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if filt:
        h["x-fantasy-filter"] = json.dumps(filt)
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=90) as r:
        j = json.loads(r.read().decode())
    return j[0] if isinstance(j, list) else j


def league(year, query, filt=None):
    try:
        return get(f"{BASE}/seasons/{year}/segments/0/leagues/{LEAGUE_ID}?{query}", filt)
    except urllib.error.HTTPError:
        return get(f"{BASE}/leagueHistory/{LEAGUE_ID}?seasonId={year}&{query}", filt)


def main():
    for k in ("ESPN_S2", "SWID"):
        if not os.environ.get(k):
            sys.exit(f"Missing {k}.")
    today = datetime.date.today()
    cur = int(os.environ.get("SEASON") or (today.year if today.month >= 8 else today.year - 1))
    st = league(cur, "view=mSettings")
    years = sorted(set(st["status"]["previousSeasons"]) | {cur})
    cur_week = st.get("scoringPeriodId") or 1

    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    P = old.get("P", {})
    # finished drafts are kept; rows from before bids were stored (6 fields) are pulled again
    D = {k: v for k, v in old.get("D", {}).items() if int(k) < cur and v and len(v[0]) >= 7}
    T = [t for t in old.get("T", []) if t[0] < cur]
    Wk = [w for w in old.get("W", []) if w[0] < cur]
    R = [r for r in old.get("R", []) if r[0] < cur]
    A = [a for a in old.get("A", []) if a[0] < cur]
    AC = [a for a in old.get("AC", []) if a[0] < cur]
    G = [g for g in old.get("G", []) if g[0] < cur]
    # seasons pulled before waivers were tracked get pulled again
    done_box = {int(y) for y in old.get("boxYears", []) if int(y) < cur} if "A" in old else set()

    def remember(pl):
        P[str(pl["id"])] = [pl.get("fullName", "?"), POS.get(pl.get("defaultPositionId"), "?")]

    # ---- drafts + season totals
    for y in years:
        if str(y) in D:
            continue
        dd = (league(y, "view=mDraftDetail").get("draftDetail") or {}).get("picks") or []
        if not dd:
            continue
        ids = [p["playerId"] for p in dd]
        kona = league(y, "view=kona_player_info", {"players": {"filterIds": {"value": ids},
                      "filterStatsForSourceIds": {"value": [0]}, "filterStatsForSplitTypeIds": {"value": [0]}}})
        pts = {}
        for p in kona.get("players", []):
            pl = p.get("player", {})
            remember(pl)
            pts[pl.get("id")] = next((s.get("appliedTotal", 0) for s in pl.get("stats", [])
                                      if s.get("seasonId") == y and s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 0), 0)
        D[str(y)] = [[p["overallPickNumber"], p["roundId"], p["teamId"], p["playerId"], round(pts.get(p["playerId"], 0) or 0, 1),
                      1 if p.get("keeper") else 0, p.get("bidAmount") or 0] for p in sorted(dd, key=lambda p: p["overallPickNumber"])]
        print(f"draft {y}: {len(dd)} picks")

    # ---- box scores (who started, who scored) and trades, 2018 on
    for y in years:
        if y < FIRST_BOX_YEAR or y in done_box:
            continue
        for lst in (T, Wk, R, A, AC, G):
            lst[:] = [r for r in lst if r[0] != y]
        last = (cur_week - 1) if y == cur else 18
        weeks = {}
        for w in range(1, last + 1):
            b = league(y, f"view=mBoxscore&scoringPeriodId={w}")
            rows = []
            for m in b.get("schedule", []):
                for side in (m.get("home"), m.get("away")):
                    if not side:
                        continue
                    for e in (side.get("rosterForCurrentScoringPeriod") or {}).get("entries", []):
                        pl = e["playerPoolEntry"]["player"]
                        remember(pl)
                        rows.append((side["teamId"], e["playerId"], e["lineupSlotId"], round(e["playerPoolEntry"].get("appliedStatTotal") or 0, 2)))
            if rows:
                weeks[w] = rows
        # starts and top weeks
        agg = {}
        for w, rows in weeks.items():
            for (tid, pid, slot, pts) in rows:
                if slot in BENCH:
                    continue
                a = agg.setdefault((tid, pid), [0, 0.0])
                a[0] += 1
                a[1] += pts
                Wk.append([y, w, tid, pid, pts, slot])
        R.extend([[y, tid, pid, a[0], round(a[1], 2)] for (tid, pid), a in agg.items()])

        # trades, adds and drops
        seen, trades, seen_tx, moves = set(), [], set(), []
        for sp in range(0, (cur_week if y == cur else 18) + 1):
            try:
                tx = league(y, f"view=mTransactions2&scoringPeriodId={sp}").get("transactions", [])
            except urllib.error.HTTPError:
                continue
            for t in tx:
                if t.get("type") in ("WAIVER", "FREEAGENT") and t.get("status") == "EXECUTED" and t.get("id") not in seen_tx:
                    seen_tx.add(t.get("id"))
                    moves.append((t.get("scoringPeriodId") or sp, t.get("teamId"), t.get("bidAmount") or 0,
                                  [(i["type"], i["playerId"], i.get("fromTeamId"), i.get("toTeamId")) for i in t.get("items", [])]))
                if t.get("type") not in ("TRADE_UPHOLD", "TRADE_ACCEPT") or t.get("status") != "EXECUTED":
                    continue
                items = [(i["playerId"], i["fromTeamId"], i["toTeamId"]) for i in t.get("items", []) if i.get("type") == "TRADE"]
                key = (y, tuple(sorted(items)))
                if not items or key in seen:
                    continue
                seen.add(key)
                trades.append((t.get("scoringPeriodId") or sp, t.get("processDate") or t.get("proposedDate"), items))
        for (wk, ts, items) in sorted(trades, key=lambda x: x[1] or 0):
            out = []
            for (pid, frm, to) in items:
                started = total = 0.0
                n = 0
                for w, rows in weeks.items():
                    if w < wk:
                        continue
                    for (tid, p2, slot, pts) in rows:
                        if p2 == pid and tid == to:
                            total += pts
                            if slot not in BENCH:
                                started += pts
                                n += 1
                out.append([pid, frm, to, round(started, 2), round(total, 2), n])
            date = datetime.datetime.fromtimestamp((ts or 0) / 1000, datetime.timezone.utc).strftime("%Y-%m-%d") if ts else ""
            T.append([y, wk, date, out])
        def after(pid, team, wk, other=False):
            started = total = 0.0
            n, best = 0, {}
            for w, rows in weeks.items():
                if w < wk:
                    continue
                for (tid, p2, slot, pts) in rows:
                    if p2 != pid or (tid == team) == other:
                        continue
                    total += pts
                    if slot not in BENCH:
                        started += pts
                        n += 1
                        b = best.setdefault(tid, [0.0, 0])
                        b[0] += pts
                        b[1] += 1
            return started, total, n, best
        counts = {}
        for (wk, team, bid, items) in moves:
            for (typ, pid, frm, to) in items:
                if typ == "ADD" and to and to > 0:
                    c = counts.setdefault(to, [0, 0])
                    c[0] += 1
                    c[1] += bid
                    st_, tot, n, _ = after(pid, to, wk)
                    if n > 0 or bid > 0:
                        A.append([y, wk, to, pid, bid, round(st_, 2), round(tot, 2), n])
                elif typ == "DROP" and frm and frm > 0:
                    _, _, _, best = after(pid, frm, wk, other=True)
                    if best:
                        tid, (pts, n) = max(best.items(), key=lambda kv: kv[1][0])
                        if pts >= 40:
                            G.append([y, wk, frm, pid, tid, round(pts, 2), n])
        AC.extend([[y, tid, c[0], c[1]] for tid, c in counts.items()])
        if y < cur:
            done_box.add(y)
        print(f"box {y}: {len(weeks)} weeks, {len(trades)} trades, {len(moves)} adds/drops")

    # keep only the top 100 single-week performances overall plus each season's top 10, to stay small
    Wk.sort(key=lambda r: -r[4])
    keep, per = [], {}
    for r in Wk:
        per[r[0]] = per.get(r[0], 0) + 1
        if len(keep) < 100 or per[r[0]] <= 10:
            keep.append(r)
    used = {str(r[3]) for r in keep} | {str(r[2]) for r in R} | {str(p[3]) for v in D.values() for p in v} | \
           {str(i[0]) for t in T for i in t[3]} | {str(a[3]) for a in A} | {str(g[3]) for g in G}
    P = {k: v for k, v in P.items() if k in used}
    T.sort(key=lambda t: (t[0], t[2]))
    OUT.write_text(json.dumps({"P": P, "D": D, "T": T, "W": keep, "R": R, "A": A, "AC": AC, "G": G, "boxYears": sorted(done_box)},
                              separators=(",", ":"), ensure_ascii=False))
    print(f"history: {len(D)} drafts, {len(T)} trades, {len(keep)} top weeks, {len(R)} start rows, {len(A)} adds, {len(G)} got away")


if __name__ == "__main__":
    main()
