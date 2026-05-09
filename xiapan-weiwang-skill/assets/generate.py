#!/usr/bin/env python3
"""
每日 5:00 BJT 自动更新赛程
包含：5大联赛 + 欧冠 + 欧联杯 + 世界杯
时间规则：北京时间 0:00-4:59 的比赛归前一日
"""
import json, subprocess, re, os, sys, time
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))
BJT_LEAGUES = {"premier-league": "英超", "la-liga": "西甲", "bundesliga": "德甲",
               "serie-a": "意甲", "ligue-1": "法甲"}
OTHER_LEAGUES = {"champions-league": "欧冠", "europa-league": "欧联杯", "world-cup": "世界杯"}
VALID_IDS = set(list(BJT_LEAGUES.keys()) + list(OTHER_LEAGUES.keys()))

def run_cli(*args, retries=2):
    """Run sports-skills CLI, return parsed JSON."""
    cmd = "sports-skills football " + " ".join(args)
    for attempt in range(retries + 1):
        if attempt > 0:
            time.sleep(3 * attempt)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                             shell=True, executable="/bin/bash")
            if r.returncode != 0:
                if attempt < retries: continue
                return None
            data = json.loads(r.stdout)
            if not data.get("status"):
                if attempt < retries: continue
                return None
            return data
        except:
            if attempt >= retries: return None
    return None

def us_to_dec(us):
    if not us: return ""
    try:
        v = int(us)
        return f"{1 + v/100:.2f}" if v > 0 else f"{1 + 100/abs(v):.2f}"
    except: return ""

def fetch_events(utc_dates):
    """Fetch all events from multiple UTC dates, return raw events."""
    all_events = []
    for d in set(utc_dates):
        if not d: continue
        data = run_cli("get_daily_schedule", f'--date="{d}"')
        if data and data.get("data",{}).get("events"):
            all_events.extend(data["data"]["events"])
    return all_events

def process_events(all_events, first_bjt_date):
    """
    Process raw events, apply BJT grouping rules.
    Only include matches whose adjusted BJT date >= first_bjt_date.
    Returns list of match dicts with additional 'day_idx' field.
    """
    DAY_SEC = 86400
    first_bjt_ts = first_bjt_date.timestamp()
    
    seen = set()
    results = []
    
    for e in all_events:
        eid = e.get("id")
        if not eid or eid in seen: continue
        seen.add(eid)
        
        comp = e.get("competition", {}).get("id", "")
        if comp not in VALID_IDS: continue
        
        utc_str = e.get("start_time", "")
        if not utc_str: continue
        
        try:
            dt_utc = datetime.strptime(utc_str, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
        except: continue
        
        dt_bjt = dt_utc.astimezone(BJT)
        bj_hour, bj_min = dt_bjt.hour, dt_bjt.minute
        
        # 0:00-4:59 → previous day
        adjusted = dt_bjt - timedelta(days=1) if bj_hour < 5 else dt_bjt
        adjusted_ts = adjusted.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        
        # Skip matches before our start date
        if adjusted_ts < first_bjt_ts:
            continue
        
        # Skip matches more than 2 days after start (we only show 2 days)
        if adjusted_ts >= first_bjt_ts + 2 * DAY_SEC:
            continue
        
        competitors = e.get("competitors", [])
        if len(competitors) < 2: continue
        home = competitors[0].get("team", {}).get("name", "?")
        away = competitors[1].get("team", {}).get("name", "?")
        
        odds = e.get("odds") or {}
        ml = odds.get("moneyline") or {} if isinstance(odds, dict) else {}
        
        day_idx = int((adjusted_ts - first_bjt_ts) / DAY_SEC)
        
        results.append({
            "comp": BJT_LEAGUES.get(comp, OTHER_LEAGUES.get(comp, comp)),
            "t": f"{bj_hour:02d}:{bj_min:02d}",
            "h": home.replace("&amp;", "&"),
            "a": away.replace("&amp;", "&"),
            "hD": us_to_dec(ml.get("home","")),
            "dD": us_to_dec(ml.get("draw","")),
            "aD": us_to_dec(ml.get("away","")),
            "day_idx": day_idx,
        })
    
    return results

def generate_matches_js(matches):
    """Generate the MATCHES array JS code."""
    matches.sort(key=lambda m: (m["day_idx"], m["t"]))
    lines = ["const MATCHES=["]
    for i, m in enumerate(matches):
        if i > 0: lines[-1] = lines[-1] + ","
        parts = [f'comp:"{m["comp"]}"', f't:"{m["t"]}"',
                 f'h:"{m["h"]}"', f'a:"{m["a"]}"',
                 f'hD:"{m["hD"]}"', f'dD:"{m["dD"]}"', f'aD:"{m["aD"]}"']
        if m["day_idx"] > 0:
            parts.append('gr:"sun"')
        lines.append("  {" + ",".join(parts) + "}")
    lines.append("];")
    return "\n".join(lines)

def generate_html(matches_js):
    """Insert data into template and write output."""
    template = os.path.join(os.path.dirname(__file__), "template.html")
    output = os.path.join(os.path.dirname(__file__), "index.html")
    
    if not os.path.exists(template):
        print(f"ERROR: template not found at {template}")
        return False
    
    with open(template, "r", encoding="utf-8") as f:
        html = f.read()
    
    html = re.sub(r'const MATCHES=\[.*?\];', matches_js, html, flags=re.DOTALL)
    
    now = datetime.now(BJT)
    html = re.sub(
        r'<div class="sub" id="hd">.*?</div>',
        f'<div class="sub" id="hd">📅 {now.strftime("%Y年%m月%d日 %H:%M")} 更新</div>',
        html
    )
    
    with open(output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Written: {output}")
    return True

def main():
    now = datetime.now(BJT)
    # If before 5:00, we're showing yesterday's day (already previous day's schedule)
    # If after 5:00, today is the start day
    if now.hour < 5:
        first_day = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        first_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    print(f"First display day: {first_day.strftime('%Y-%m-%d')} (BJT)")
    
    # Fetch events from UTC dates that could cover our range
    # BJT day 0:00 = UTC prev day 16:00; BJT day+2 4:59 = UTC day+1 20:59
    # We need UTC dates: first_day-1, first_day, first_day+1, first_day+2
    utc_dates = []
    for offset in [-1, 0, 1, 2]:
        d = (first_day + timedelta(days=offset)).strftime("%Y-%m-%d")
        utc_dates.append(d)
    
    all_events = fetch_events(utc_dates)
    print(f"Fetched {len(all_events)} raw events from {len(set(utc_dates))} UTC dates")
    
    matches = process_events(all_events, first_day)
    print(f"Found {len(matches)} matches for display")
    
    for m in matches:
        day_label = "周六" if m["day_idx"] == 0 else "周日"
        print(f"  {day_label} {m['t']} {m['comp']} {m['h']} vs {m['a']}")
    
    matches_js = generate_matches_js(matches) if matches else "const MATCHES=[];"
    
    if generate_html(matches_js):
        print("Update successful")
    else:
        print("Update FAILED")

if __name__ == "__main__":
    main()
