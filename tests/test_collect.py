import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import collect as C

T0 = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)  # 19:00 MSK typ. :00 kickoff


def test_scheduled_slot():
    assert C.scheduled_slot(datetime(2026, 11, 27, 14, 3, 10, tzinfo=timezone.utc)) == \
        datetime(2026, 11, 27, 14, 3, tzinfo=timezone.utc)
    # 14:17 — предыдущий слот 14:03, не 14:18
    assert C.scheduled_slot(datetime(2026, 11, 27, 14, 17, 0, tzinfo=timezone.utc)).minute == 3
    # ровно 14:18
    assert C.scheduled_slot(datetime(2026, 11, 27, 14, 18, 0, tzinfo=timezone.utc)).minute == 18
    # 15:01 — слот 14:48 предыдущего часа
    slot = C.scheduled_slot(datetime(2026, 11, 27, 15, 1, 0, tzinfo=timezone.utc))
    assert slot == datetime(2026, 11, 27, 14, 48, tzinfo=timezone.utc)


def _mu(pid, home, away, start, league="L"):
    return {"id": pid, "type": "matchup", "parentId": None, "isLive": False, "units": "Regular",
            "startTime": start, "league": {"name": league},
            "participants": [{"alignment": "home", "name": home}, {"alignment": "away", "name": away}]}


def _ml(pid, h, d, a):
    return {"matchupId": pid, "type": "moneyline", "period": 0, "status": "open",
            "prices": [{"designation": "home", "price": h}, {"designation": "draw", "price": d},
                       {"designation": "away", "price": a}]}


def _ah(pid, pts, h, a):
    return {"matchupId": pid, "type": "spread", "period": 0, "status": "open",
            "prices": [{"designation": "home", "price": h, "points": pts},
                       {"designation": "away", "price": a, "points": -pts}]}


def _tot(pid, pts, o, u):
    return {"matchupId": pid, "type": "total", "period": 0, "status": "open",
            "prices": [{"designation": "over", "price": o, "points": pts},
                       {"designation": "under", "price": u, "points": pts}]}


def test_parse_filters_live_and_started():
    start_soon = C.iso(T0 + timedelta(hours=2))
    start_old = C.iso(T0 - timedelta(hours=2))
    mu = [
        _mu(1, "A", "B", start_soon),
        {**_mu(2, "C", "D", start_soon), "isLive": True},
        _mu(3, "E", "F", start_old),
        {**_mu(4, "G", "H", start_soon), "units": "Corners", "parentId": 1},
    ]
    mk = [_ml(1, 2.1, 3.4, 3.5), _ml(2, 1.8, 3.6, 4.2), _ml(3, 2.0, 3.3, 3.8),
          _ah(1, -0.5, 1.95, 1.95), _tot(1, 2.5, 1.90, 2.00)]
    snap = C.parse_snapshot(mu, mk, T0)
    ids = [e["id"] for e in snap["events"]]
    assert ids == [1]
    ev = snap["events"][0]
    assert ev["x12"] == [2.1, 3.4, 3.5]
    assert ev["ah"][0][0] == -0.5
    assert ev["tot"][0] == [2.5, 1.9, 2.0]


def test_american_odds_and_missing_markets():
    start = C.iso(T0 + timedelta(hours=1))
    mu = [_mu(9, "X", "Y", start)]
    mk = [_ml(9, -150, 280, 400)]  # american home, decimal draw/away (>=100 → american)
    snap = C.parse_snapshot(mu, mk, T0)
    ev = snap["events"][0]
    assert abs(ev["x12"][0] - (1 + 100/150)) < 1e-3


def test_closing_prefers_closer_and_keeps_15_30_if_needed():
    ev = {"id": 10, "lg": "L", "h": "A", "a": "B", "start": C.iso(T0),
          "x12": [2.0, 3.4, 3.8], "ah": [[-1.0, 1.9, 2.0]], "tot": [[2.5, 1.95, 1.95]]}
    far = C.closing_candidate(ev, T0 - timedelta(minutes=27))   # 15-30
    mid = C.closing_candidate(ev, T0 - timedelta(minutes=12))   # 0-15
    near = C.closing_candidate(ev, T0 - timedelta(minutes=4))
    none = C.closing_candidate(ev, T0 - timedelta(minutes=42))
    after = C.closing_candidate(ev, T0 + timedelta(minutes=1))
    at = C.closing_candidate(ev, T0)
    assert none is None and after is None and at is None
    assert far["window"] == "15-30" and mid["window"] == "0-15"
    kept = C.better_closing(mid, far)
    assert kept["close_min_before"] == mid["close_min_before"]
    kept2 = C.better_closing(near, mid)
    assert kept2["close_min_before"] == near["close_min_before"]
    # более далёкий не затирает близкий
    assert C.better_closing(far, mid) is mid


def test_merge_purges_old(tmp_path):
    ev_old = {"id": 1, "start": C.iso(T0 - timedelta(days=5)), "x12": [2.0, 3.2, 3.6],
              "h": "O", "a": "P", "lg": "L"}
    ev_new = {"id": 2, "start": C.iso(T0 + timedelta(minutes=10)), "x12": [1.8, 3.6, 4.2],
              "h": "A", "a": "B", "lg": "L"}
    prev = [C.closing_candidate({**ev_old, "start": C.iso(T0 - timedelta(days=5) + timedelta(minutes=10))},
                                T0 - timedelta(days=5)) or {"id": 1, "start": ev_old["start"],
                                                            "close_min_before": 10, "close_ts": C.iso(T0 - timedelta(days=5))}]
    # force old start
    prev = [{"id": 1, "start": ev_old["start"], "close_min_before": 8, "close_ts": ev_old["start"]}]
    out = C.merge_closings(prev, [ev_new], T0)
    ids = [r["id"] for r in out]
    assert 2 in ids and 1 not in ids
    assert out[0]["window"] == "0-15"


def test_run_writes_files(tmp_path):
    start = C.iso(T0 + timedelta(minutes=12))
    mu = [_mu(5, "Home", "Away", start, "England - Premier League")]
    mk = [_ml(5, 2.2, 3.3, 3.4), _tot(5, 2.5, 1.91, 1.99)]
    started = T0
    res = C.run(tmp_path, started=started, is_schedule=True,
                prev_closings=[], prev_runs=[], fetched=(mu, mk, {"matchups_http": 200, "markets_http": 200}))
    assert res["ok"] and res["n"] == 1 and res["n_closing"] == 1
    latest = json.loads((tmp_path / "latest.json").read_text())
    clos = json.loads((tmp_path / "closings.json").read_text())
    st = json.loads((tmp_path / "status.json").read_text())
    pu = json.loads((tmp_path / "punctuality.json").read_text())
    assert latest["events"][0]["h"] == "Home"
    assert clos["events"][0]["window"] == "0-15"
    assert st["ok"] is True and st["delay_sec"] is not None
    # started T0 = 16:00, слот 15:48, delay 12 мин
    assert pu["runs"][0]["delay_sec"] == 12 * 60
    assert (tmp_path / "index.html").exists()
