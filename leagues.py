#!/usr/bin/env python3
"""
Builds a single .ics calendar from free, keyless sports data feeds.

Sources
-------
fixturedownload.com   fixtures, venues and results for most leagues
api.jolpi.ca          Formula 1 session times (Ergast-compatible)

Run:  python3 generate.py
Out:  docs/calendar.ics  and  docs/diagnostics.txt

Nothing here needs an API key, an account or a payment method.
"""

import csv
import datetime as dt
import io
import json
import os
import sys
import urllib.error
import urllib.request

import leagues as cfg

# Tried in order until one returns something we can read.
FD_URLS = [
    "https://fixturedownload.com/download/json/{slug}",
    "https://fixturedownload.com/feed/json/{slug}",
    "https://fixturedownload.com/view/json/{slug}",
    "https://fixturedownload.com/download/csv/{slug}",
]
JOLPICA_URL = "https://api.jolpi.ca/ergast/f1/{year}.json?limit=100"
USER_AGENT = "sports-calendar/1.1 (personal calendar generator)"
TIMEOUT = 45

diagnostics = []


def note(line):
    """Record something worth a human look after the run."""
    diagnostics.append(line)
    print(line)


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8-sig", errors="replace")


def fetch_json(url):
    return json.loads(fetch_text(url))


# ---------------------------------------------------------------------------
# Reading the fixture feeds
# ---------------------------------------------------------------------------

def tidy_key(name):
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def rows_from_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for raw in reader:
        rows.append({tidy_key(k): (v or "").strip()
                     for k, v in raw.items() if k})
    return rows


def rows_from_json(text):
    payload = json.loads(text)
    if isinstance(payload, dict):
        for key in ("matches", "fixtures", "data", "results"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise ValueError("JSON was not a list of matches")
    return [{tidy_key(k): v for k, v in row.items()} for row in payload]


def load_feed(slug):
    """Return (rows, url_that_worked) or (None, None)."""
    last_error = None
    for template in FD_URLS:
        url = template.format(slug=slug)
        try:
            text = fetch_text(url)
        except urllib.error.HTTPError as err:
            last_error = "HTTP %s" % err.code
            continue
        except Exception as err:  # noqa: BLE001
            last_error = str(err)
            continue

        for parser in (rows_from_json, rows_from_csv):
            try:
                rows = parser(text)
            except Exception as err:  # noqa: BLE001
                last_error = str(err)
                continue
            if rows and any(rows[0].get(k) for k in ("hometeam", "home")):
                return rows, url
            last_error = "parsed but found no team columns"

    note("SKIPPED %s: no readable feed (%s)" % (slug, last_error))
    return None, None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
)


def parse_utc(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "").replace("T", " ").strip()
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    note("WARNING could not read date: %r" % value)
    return None


def parse_iso_date_time(date_text, time_text):
    """Jolpica writes date '2026-08-23' and time '13:00:00Z' separately."""
    if not date_text or not time_text:
        return None
    text = "%s %s" % (date_text.strip(), time_text.strip().replace("Z", ""))
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=dt.timezone.utc)
    except ValueError:
        note("WARNING could not read F1 time: %r %r" % (date_text, time_text))
        return None


def parse_scores(row):
    """Scores arrive either as two columns or as one 'Result' string."""
    home = row.get("hometeamscore", row.get("homescore"))
    away = row.get("awayteamscore", row.get("awayscore"))
    if home not in (None, "") and away not in (None, ""):
        return home, away
    result = str(row.get("result", "") or "").strip()
    if "-" in result:
        left, _, right = result.partition("-")
        left, right = left.strip(), right.strip()
        if left.isdigit() and right.isdigit():
            return left, right
    return None, None


def stamp(moment):
    return moment.strftime("%Y%m%dT%H%M%SZ")


def escape(text):
    if text is None:
        return ""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def _joins_to_previous(char):
    """True if breaking a line before this character would break an emoji."""
    code = ord(char)
    return (code == 0x200D or code == 0xFE0F
            or 0x1F3FB <= code <= 0x1F3FF
            or 0x1F1E6 <= code <= 0x1F1FF
            or 0xE0000 <= code <= 0xE007F
            or 0x0300 <= code <= 0x036F)


def fold(line):
    """iCalendar lines must not exceed 75 octets. Continuations start a space."""
    if len(line.encode("utf-8")) <= 73:
        return line

    clusters, cluster = [], ""
    for char in line:
        if cluster and _joins_to_previous(char):
            cluster += char
        else:
            if cluster:
                clusters.append(cluster)
            cluster = char
    if cluster:
        clusters.append(cluster)

    pieces, current, size = [], [], 0
    for item in clusters:
        width = len(item.encode("utf-8"))
        if current and size + width > 73:
            pieces.append("".join(current))
            current, size = [], 1
        current.append(item)
        size += width
    if current:
        pieces.append("".join(current))

    return "\r\n".join(p if i == 0 else " " + p for i, p in enumerate(pieces))


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def display_name(league, feed_name, seen):
    if not feed_name:
        return None
    seen.add(feed_name)
    return league.get("names", {}).get(feed_name, feed_name)


def team_label(league, name):
    gender = league.get("gender", "M")
    if not league.get("international"):
        return "%s (%s)" % (name, gender)

    flag = cfg.COUNTRY_FLAGS.get(name, "")
    if not flag:
        note("MISSING FLAG for country: %s" % name)
    if name == "Australia":
        nickname = cfg.AUS_NICKNAMES.get((league["sport"], gender))
        if nickname:
            return "%s%s (%s)" % (flag, name, nickname)
    return "%s%s (%s)" % (flag, name, gender)


def round_label(row):
    group = row.get("group")
    if group and str(group).strip():
        return str(group).strip()
    number = row.get("roundnumber", row.get("round"))
    if number in (None, ""):
        return ""
    text = str(number).strip()
    return text if not text.isdigit() else "Round %s" % text


def extra_time_suffix(league, row):
    """Extra time markers are not in the fixture feed yet. When a phase two
    source supplies one, it arrives as row['extratime'] holding a key such as
    'aet', 'ot', '2ot', 'gp', 'so'."""
    key = str(row.get("extratime", "") or "").strip().lower()
    if not key:
        return ""
    table = cfg.EXTRA_TIME.get(league["sport"], {})
    return table.get(key, key.upper())


# ---------------------------------------------------------------------------
# Event assembly
# ---------------------------------------------------------------------------

def make_event(uid, title, start, minutes, location, notes):
    end = start + dt.timedelta(minutes=minutes)
    lines = [
        "BEGIN:VEVENT",
        "UID:%s" % uid,
        "DTSTAMP:%s" % stamp(dt.datetime.now(dt.timezone.utc)),
        "DTSTART:%s" % stamp(start),
        "DTEND:%s" % stamp(end),
        "SUMMARY:%s" % escape(title),
    ]
    if location:
        lines.append("LOCATION:%s" % escape(location))
    if notes:
        lines.append("DESCRIPTION:%s" % escape(notes))
    lines.append("TRANSP:TRANSPARENT")
    lines.append("END:VEVENT")
    return lines


def build_league_events(league):
    sport = cfg.SPORTS[league["sport"]]
    slugs = league["slug"] if isinstance(league["slug"], list) else [league["slug"]]

    rows, used_url = None, None
    for slug in slugs:
        rows, used_url = load_feed(slug)
        if rows:
            break
    if not rows:
        return []

    note("FEED OK %s via %s (%d rows, columns: %s)"
         % (slugs[0], used_url, len(rows), ", ".join(sorted(rows[0].keys()))))

    follow = set(league.get("follow") or [])
    seen_names = set()
    events = []
    count = 0

    for index, row in enumerate(rows):
        home_feed = row.get("hometeam") or row.get("home")
        away_feed = row.get("awayteam") or row.get("away")
        for value in (home_feed, away_feed):
            if value:
                seen_names.add(value)
        if follow and home_feed not in follow and away_feed not in follow:
            continue

        home = display_name(league, home_feed, seen_names)
        away = display_name(league, away_feed, seen_names)
        if not home or not away:
            continue

        start = parse_utc(row.get("dateutc") or row.get("date"))
        if start is None:
            continue

        home_label = team_label(league, home)
        away_label = team_label(league, away)

        if cfg.INCLUDE_SCORES:
            home_score, away_score = parse_scores(row)
            if home_score is not None:
                home_label += " [%s]" % home_score
                away_label += " [%s]" % away_score

        if sport["format"] == "@":
            title = "%s %s @ %s" % (sport["emoji"], away_label, home_label)
        else:
            title = "%s %s vs. %s" % (sport["emoji"], home_label, away_label)

        suffix = extra_time_suffix(league, row) if cfg.INCLUDE_SCORES else ""
        if suffix:
            title += " " + suffix

        stage = league.get("stage", "").replace("{round}", round_label(row))
        stage = stage.rstrip(", ").strip()
        notes = "\n".join(x for x in (league.get("competition"), stage) if x)

        number = row.get("matchnumber", index)
        uid = "%s-%s@sports-calendar" % (slugs[0], number)

        events.extend(make_event(uid, title, start, sport["minutes"],
                                 row.get("location"), notes))
        count += 1

    unmapped = sorted(n for n in seen_names if n not in league.get("names", {}))
    if unmapped:
        diagnostics.append("FEED NAMES %s: %s" % (slugs[0], " | ".join(unmapped)))
    print("  %s: %d events kept" % (slugs[0], count))
    return events


def build_f1_events():
    sport = cfg.SPORTS["f1"]
    try:
        payload = fetch_json(JOLPICA_URL.format(year=cfg.F1_SEASON))
    except Exception as err:  # noqa: BLE001
        note("SKIPPED Formula 1: %s" % err)
        return []

    races = payload["MRData"]["RaceTable"]["Races"]
    events = []
    count = 0

    for race in races:
        grand_prix = race.get("raceName", "Grand Prix")
        circuit = race.get("Circuit", {}).get("circuitName", "")
        round_no = race.get("round", "")

        for key, (label, minutes) in cfg.F1_SESSIONS.items():
            if key == "Race":
                block = {"date": race.get("date"), "time": race.get("time")}
            else:
                block = race.get(key)
            if not block:
                continue
            if label.startswith("Practice") and not cfg.F1_INCLUDE_PRACTICE:
                continue
            start = parse_iso_date_time(block.get("date"), block.get("time"))
            if not start:
                continue
            title = "%s F1: %s (%s)" % (sport["emoji"], label, grand_prix)
            notes = "%s\nRound %s" % (cfg.F1_COMPETITION, round_no)
            uid = "f1-%s-%s-%s@sports-calendar" % (cfg.F1_SEASON, round_no, key)
            events.extend(make_event(uid, title, start, minutes, circuit, notes))
            count += 1

    print("  formula-1: %d sessions" % count)
    return events


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Building calendar...")
    body = []
    for league in cfg.LEAGUES:
        body.extend(build_league_events(league))
    body.extend(build_f1_events())

    count = len([1 for line in body if line == "BEGIN:VEVENT"])
    note("TOTAL events written: %d" % count)
    if count == 0:
        note("ERROR no events produced, refusing to overwrite the calendar")
        write_diagnostics()
        return 1

    header = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//sports-calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:%s" % escape(cfg.CALENDAR_NAME),
        "X-WR-TIMEZONE:UTC",
    ]
    lines = header + body + ["END:VCALENDAR"]

    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(cfg.OUTPUT_DIR, cfg.OUTPUT_FILE)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("\r\n".join(fold(line) for line in lines) + "\r\n")

    print("Wrote %s with %d events" % (path, count))
    write_diagnostics()
    return 0


def write_diagnostics():
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(cfg.OUTPUT_DIR, "diagnostics.txt")
    header = "Run at %s UTC" % dt.datetime.now(
        dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(header + "\n\n")
        handle.write("\n".join(diagnostics) if diagnostics else "Nothing to report.")
        handle.write("\n")
    print("Wrote %s" % path)


if __name__ == "__main__":
    sys.exit(main())
