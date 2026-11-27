#!/usr/bin/env python3
"""Снимок публичной прематч-линии Pinnacle (футбол, sport 29): 1X2, форы, тоталы.

Только гостевой API, без ключей, без Fonbet и без кода калькулятора.
Пауза 1–2 с между запросами. Результат — компактный JSON (latest.json)
и копилка closing за последние 4 дня (closings.json).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PIN = "https://guest.api.arcadia.pinnacle.com/0.1"
HEADERS = {
    "Accept": "application/json",
    "Referer": "https://www.pinnacle.com/",
    "Origin": "https://www.pinnacle.com",
    "User-Agent": "gem-pinnacle-feed/1 (public low-rate snapshot; no login)",
}
PAUSE_SEC = 1.5
REQ_TIMEOUT = 40
CLOSE_WINDOW_MIN = (0, 30)      # сохраняем снимки за 0–30 мин до старта
CLOSE_PREF_MIN = 15             # предпочитаем окно 0–15
CLOSING_KEEP_DAYS = 4
PUNCTUALITY_KEEP = 250
MAX_FEED_AGE_HOURS = 6          # события, которые уже давно начались, в latest не держим

# cron: 3,18,33,48 * * * * (UTC)
CRON_MINUTES = (3, 18, 33, 48)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def american_to_decimal(a: float) -> float:
    a = float(a)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def to_decimal(x) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if abs(v) >= 100:
        return american_to_decimal(v)
    return v if v > 1.0 else None


def _ok_pair(a, b) -> bool:
    return bool(a and b and a > 1.0 and b > 1.0)


def _ok_three(a, b, c) -> bool:
    return bool(a and b and c and a > 1.0 and b > 1.0 and c > 1.0)


def http_get(url: str) -> tuple:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=REQ_TIMEOUT) as r:
            raw = r.read()
            return r.status, raw, None
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        return e.code, raw, str(e)
    except Exception as e:
        return 0, b"", type(e).__name__ + ": " + str(e)


def scheduled_slot(ts: datetime) -> datetime:
    """Ближайший прошедший (или текущий) слот сетки :03/:18/:33/:48 UTC."""
    ts = ts.astimezone(timezone.utc).replace(second=0, microsecond=0)
    candidates = []
    base = ts.replace(minute=0)
    for d in (-1, 0):
        hour = base + timedelta(hours=d)
        for m in CRON_MINUTES:
            slot = hour.replace(minute=m)
            if slot <= ts:
                candidates.append(slot)
    return max(candidates) if candidates else ts


def compact_event(m: dict, markets: List[dict]) -> Optional[dict]:
    if m.get("type") != "matchup" or m.get("parentId") or m.get("isLive"):
        return None
    if m.get("units") not in (None, "Regular"):
        return None
    parts = {p.get("alignment"): p.get("name") for p in m.get("participants") or []}
    home, away = parts.get("home"), parts.get("away")
    if not home or not away:
        return None
    start = parse_utc(m.get("startTime"))
    ev = {
        "id": m["id"],
        "lg": (m.get("league") or {}).get("name") or "?",
        "h": home,
        "a": away,
        "start": iso(start) if start else None,
    }
    x12 = None
    ah: List[list] = []
    tot: List[list] = []
    lim = None
    for x in markets:
        if x.get("status") != "open" or x.get("period") != 0:
            continue
        pr = {(p.get("designation") or ""): p for p in x.get("prices") or []}
        typ = x.get("type")
        amounts = [l.get("amount") or 0 for l in x.get("limits") or []]
        if typ == "moneyline" and "draw" in pr:
            h, d, a = (to_decimal((pr.get(k) or {}).get("price")) for k in ("home", "draw", "away"))
            if _ok_three(h, d, a):
                x12 = [round(h, 3), round(d, 3), round(a, 3)]
                if amounts:
                    lim = max(amounts)
        elif typ == "spread" and "home" in pr and "away" in pr:
            h, a = to_decimal(pr["home"].get("price")), to_decimal(pr["away"].get("price"))
            pts = pr["home"].get("points")
            if pts is not None and _ok_pair(h, a):
                ah.append([float(pts), round(h, 3), round(a, 3)])
        elif typ == "total" and "over" in pr and "under" in pr:
            o, u = to_decimal(pr["over"].get("price")), to_decimal(pr["under"].get("price"))
            pts = pr["over"].get("points")
            if pts is not None and _ok_pair(o, u):
                tot.append([float(pts), round(o, 3), round(u, 3)])
    if x12:
        ev["x12"] = x12
    if ah:
        ev["ah"] = ah
    if tot:
        ev["tot"] = tot
    if lim:
        ev["lim"] = lim
    if not x12 and not ah and not tot:
        return None
    return ev


def parse_snapshot(matchups: List[dict], markets: List[dict], ts: Optional[datetime] = None) -> dict:
    ts = ts or now_utc()
    by_mid: Dict[Any, List[dict]] = {}
    for x in markets or []:
        if x.get("status") == "open" and x.get("period") == 0 and x.get("type") in ("moneyline", "spread", "total"):
            by_mid.setdefault(x.get("matchupId"), []).append(x)
    events = []
    for m in matchups or []:
        ev = compact_event(m, by_mid.get(m.get("id"), []))
        if not ev:
            continue
        start = parse_utc(ev.get("start"))
        # в latest — ещё не начавшиеся и только что стартовавшие (на всякий случай 30 мин)
        if start and (ts - start).total_seconds() > 30 * 60:
            continue
        events.append(ev)
    events.sort(key=lambda e: (e.get("start") or "", e.get("lg") or "", e.get("h") or ""))
    return {"ts": iso(ts), "source": "pinnacle_guest", "sport": 29, "n": len(events), "events": events}


def minutes_to_start(start: datetime, now: datetime) -> float:
    return (start - now).total_seconds() / 60.0


def closing_candidate(ev: dict, snap_ts: datetime) -> Optional[dict]:
    start = parse_utc(ev.get("start"))
    if not start:
        return None
    m = minutes_to_start(start, snap_ts)
    lo, hi = CLOSE_WINDOW_MIN
    if not (lo < m <= hi):
        return None
    rec = {
        "id": ev["id"],
        "lg": ev.get("lg"),
        "h": ev.get("h"),
        "a": ev.get("a"),
        "start": ev.get("start"),
        "close_ts": iso(snap_ts),
        "close_min_before": round(m, 2),
        "window": "0-15" if m <= CLOSE_PREF_MIN else "15-30",
    }
    for k in ("x12", "ah", "tot", "lim"):
        if ev.get(k) is not None:
            rec[k] = ev[k]
    return rec


def better_closing(new: dict, old: Optional[dict]) -> dict:
    """Ближе к старту — лучше. При равенстве предпочитаем 0–15."""
    if not old:
        return new
    if new["close_min_before"] < old["close_min_before"] - 1e-9:
        return new
    return old


def merge_closings(prev: List[dict], events: List[dict], snap_ts: datetime,
                   keep_days: int = CLOSING_KEEP_DAYS) -> List[dict]:
    by_id = {int(r["id"]): r for r in prev if r.get("id") is not None}
    for ev in events:
        cand = closing_candidate(ev, snap_ts)
        if not cand:
            continue
        pid = int(cand["id"])
        by_id[pid] = better_closing(cand, by_id.get(pid))
    cut = snap_ts - timedelta(days=keep_days)
    out = []
    for r in by_id.values():
        st = parse_utc(r.get("start"))
        if st and st < cut:
            continue
        out.append(r)
    out.sort(key=lambda r: r.get("start") or "")
    return out


def build_punctuality(prev: List[dict], started: datetime, ok: bool, n: int,
                      delay_sec: Optional[int], scheduled: Optional[str],
                      note: str = "") -> List[dict]:
    row = {
        "scheduled": scheduled,
        "started": iso(started),
        "delay_sec": delay_sec,
        "ok": ok,
        "n": n,
    }
    if note:
        row["note"] = note
    out = list(prev) + [row]
    return out[-PUNCTUALITY_KEEP:]


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def fetch_pinnacle() -> tuple:
    st1, raw1, err1 = http_get(f"{PIN}/sports/29/matchups")
    time.sleep(PAUSE_SEC)
    st2, raw2, err2 = http_get(f"{PIN}/sports/29/markets/straight")
    info = {
        "matchups_http": st1, "matchups_bytes": len(raw1), "matchups_err": err1,
        "markets_http": st2, "markets_bytes": len(raw2), "markets_err": err2,
    }
    mu = mk = None
    if st1 == 200:
        try:
            mu = json.loads(raw1.decode("utf-8"))
        except Exception as e:
            info["matchups_err"] = f"json: {e}"
    if st2 == 200:
        try:
            mk = json.loads(raw2.decode("utf-8"))
        except Exception as e:
            info["markets_err"] = f"json: {e}"
    return mu, mk, info


def run(out_dir: Path, started: Optional[datetime] = None, is_schedule: bool = False,
        prev_closings=None, prev_runs=None, fetched=None) -> dict:
    started = started or now_utc()
    out_dir.mkdir(parents=True, exist_ok=True)
    scheduled = None
    delay_sec = None
    if is_schedule:
        slot = scheduled_slot(started)
        scheduled = iso(slot)
        delay_sec = int((started - slot).total_seconds())

    if fetched is None:
        mu, mk, info = fetch_pinnacle()
    else:
        mu, mk, info = fetched

    ok = isinstance(mu, list) and isinstance(mk, list)
    snap = parse_snapshot(mu or [], mk or [], started) if ok else {
        "ts": iso(started), "source": "pinnacle_guest", "sport": 29, "n": 0, "events": [],
        "error": info,
    }
    if not ok:
        snap["error"] = info

    prev_c = prev_closings
    if prev_c is None:
        prev_raw = load_json(out_dir / "closings.json", {})
        prev_c = prev_raw.get("events") if isinstance(prev_raw, dict) else (prev_raw or [])
    closings = merge_closings(prev_c or [], snap.get("events") or [], started)
    closing_doc = {
        "ts": iso(started),
        "keep_days": CLOSING_KEEP_DAYS,
        "window_min": list(CLOSE_WINDOW_MIN),
        "prefer_max_min": CLOSE_PREF_MIN,
        "n": len(closings),
        "events": closings,
    }

    prev_p = prev_runs
    if prev_p is None:
        prev_raw = load_json(out_dir / "punctuality.json", {})
        prev_p = prev_raw.get("runs") if isinstance(prev_raw, dict) else []
    runs = build_punctuality(prev_p or [], started, ok, snap.get("n") or 0, delay_sec, scheduled,
                             note="" if ok else json.dumps(info)[:300])
    delays = [r["delay_sec"] for r in runs if r.get("delay_sec") is not None]
    punct = {
        "cron": "3,18,33,48 * * * *",
        "tz": "UTC",
        "n_runs": len(runs),
        "n_scheduled": len(delays),
        "mean_delay_sec": (sum(delays) / len(delays)) if delays else None,
        "max_delay_sec": max(delays) if delays else None,
        "runs": runs,
    }
    status = {
        "ts": iso(started),
        "ok": ok,
        "n": snap.get("n") or 0,
        "n_closing": len(closings),
        "scheduled": scheduled,
        "delay_sec": delay_sec,
        "source": "pinnacle_guest",
    }

    def dump(path: Path, obj):
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    dump(out_dir / "latest.json", snap)
    dump(out_dir / "closings.json", closing_doc)
    dump(out_dir / "punctuality.json", punct)
    dump(out_dir / "status.json", status)
    (out_dir / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>gem-pinnacle-feed</title>"
        "<p>Public pre-match Pinnacle football odds (1X2 / Asian handicap / totals).</p>"
        "<ul><li><a href='latest.json'>latest.json</a></li>"
        "<li><a href='closings.json'>closings.json</a></li>"
        "<li><a href='status.json'>status.json</a></li>"
        "<li><a href='punctuality.json'>punctuality.json</a></li></ul>\n",
        encoding="utf-8",
    )
    result = {"ok": ok, "n": snap.get("n") or 0, "n_closing": len(closings),
              "delay_sec": delay_sec, "scheduled": scheduled, "info": info,
              "ts": iso(started)}
    print(json.dumps(result, ensure_ascii=False))
    return result


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=".", help="каталог для JSON")
    p.add_argument("--schedule", action="store_true", help="запуск по cron (считать опоздание)")
    args = p.parse_args(argv)
    res = run(Path(args.out), is_schedule=args.schedule)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
