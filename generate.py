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

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

import leagues as cfg

FD_URL = "https://fixturedownload.com/view/json/{slug}"
JOLPICA_URL = "https://api.jolpi.ca/ergast/f1/{year}.json?limit=100"
USER_AGENT = "sports-calendar/1.0 (personal calendar generator)"
TIMEOUT = 45

diagnostics = []


def note(line):
    """Record something worth a human look after the run."""
    diagnostics.append(line)
    print(line)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def parse_utc(value):
    """fixturedownload writes '2026-03-01 02:15:00Z'. Return an aware datetime."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
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


def stamp(moment):
    return moment.strftime("%Y%m%dT%H%M%SZ")


def escape(text):
    """iCalendar escaping: backslash, semicolon, comma, newline."""
    if text is None:
        return ""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n")
            .replace("\n", "\\n"))


def _joins_to_previous(char):
    """True if breaking a line immediately before this character would break
    an emoji sequence such as a flag, a skin tone or a joined pictograph."""
    code = ord(char)
    return (code == 0x200D                       # zero width joiner
            or code == 0xFE0F                    # variation selector
            or 0x1F3FB <= code <= 0x1F3FF        # skin tone modifiers
            or 0x1F1E6 <= code <= 0x1F1FF        # regional indicators (flags)
            or 0xE0000 <= code <= 0xE007F        # tag characters (sub-flags)
            or 0x0300 <= code <= 0x036F)         # combining marks


def fold(line):
    """iCalendar lines must not exceed 75 octets. Continuations start a space."""
    if len(line.encode("utf-8")) <= 73:
        return line

    # Group the text into clusters that must never be split apart.
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
            current, size = [], 1  # the continuation space costs one octet
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
    """Chelsea (M)   or   flagAustralia (Matildas) for internationals."""
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


def round_label(match):
    """Prefer a named stage from the feed, fall back to 'Round N'."""
    group = match.get("Group") or match.get("group")
    if group and str(group).strip():
        return str(group).strip()
    number = match.get("RoundNumber", match.get("roundNumber"))
    if number in (None, ""):
        return ""
    text = str(number).strip()
    return text if not text.isdigit() else "Round %s" % text


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
    url = FD_URL.format(slug=league["slug"])
    try:
        matches = fetch_json(url)
    except urllib.error.HTTPError as err:
        note("SKIPPED %s: feed returned HTTP %s (season may not be published yet)"
             % (league["slug"], err.code))
        return []
    except Exception as err:  # noqa: BLE001 - a bad feed must not stop the run
        note("SKIPPED %s: %s" % (league["slug"], err))
        return []

    follow = set(league.get("follow") or [])
    seen_names = set()
    events = []

    for match in matches:
        home_feed = match.get("HomeTeam") or match.get("homeTeam")
        away_feed = match.get("AwayTeam") or match.get("awayTeam")
        if follow and home_feed not in follow and away_feed not in follow:
            # still record the name so diagnostics can show the real spellings
            seen_names.update(x for x in (home_feed, away_feed) if x)
            continue

        home = display_name(league, home_feed, seen_names)
        away = display_name(league, away_feed, seen_names)
        if not home or not away:
            continue

        start = parse_utc(match.get("DateUtc") or match.get("dateUtc"))
        if start is None:
            continue

        home_label = team_label(league, home)
        away_label = team_label(league, away)

        if cfg.INCLUDE_SCORES:
            home_score = match.get("HomeTeamScore")
            away_score = match.get("AwayTeamScore")
            if home_score is not None and away_score is not None:
                home_label += " [%s]" % home_score
                away_label += " [%s]" % away_score

        if sport["format"] == "@":
            title = "%s %s @ %s" % (sport["emoji"], away_label, home_label)
        else:
            title = "%s %s vs. %s" % (sport["emoji"], home_label, away_label)

        stage = league.get("stage", "").replace("{round}", round_label(match))
        stage = stage.rstrip(", ").strip()
        notes = "\n".join(x for x in (league.get("competition"), stage) if x)

        number = match.get("MatchNumber", match.get("matchNumber", len(events)))
        uid = "%s-%s@sports-calendar" % (league["slug"], number)

        events.extend(make_event(
            uid, title, start, sport["minutes"],
            match.get("Location") or match.get("location"), notes))

    if seen_names:
        unmapped = sorted(n for n in seen_names
                          if n not in league.get("names", {}))
        diagnostics.append("FEED NAMES %s: %s" % (league["slug"],
                                                  ", ".join(unmapped)))
    print("  %s: %d events" % (league["slug"], len([1 for l in events
                                                    if l == "BEGIN:VEVENT"])))
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

    for race in races:
        grand_prix = race.get("raceName", "Grand Prix")
        circuit = race.get("Circuit", {}).get("circuitName", "")
        round_no = race.get("round", "")

        sessions = []
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
            if start:
                sessions.append((key, label, minutes, start))

        for key, label, minutes, start in sessions:
            title = "%s F1: %s (%s)" % (sport["emoji"], label, grand_prix)
            notes = "%s\nRound %s" % (cfg.F1_COMPETITION, round_no)
            uid = "f1-%s-%s-%s@sports-calendar" % (cfg.F1_SEASON, round_no, key)
            events.extend(make_event(uid, title, start, minutes, circuit, notes))

    print("  formula-1: %d sessions" % len([1 for l in events
                                            if l == "BEGIN:VEVENT"]))
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
    if count == 0:
        note("ERROR no events were produced, refusing to overwrite the calendar")
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
    stamp_line = "Run at %s UTC" % dt.datetime.now(
        dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(stamp_line + "\n\n")
        handle.write("\n".join(diagnostics) if diagnostics else "Nothing to report.")
        handle.write("\n")
    print("Wrote %s" % path)


if __name__ == "__main__":
    sys.exit(main())
