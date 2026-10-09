#!/usr/bin/env python3
"""
Builds a single .ics calendar from free, keyless sports data feeds.

Sources
-------
fixturedownload.com   fixtures, venues and results for most leagues
api.jolpi.ca          Formula 1 session times (Ergast-compatible)
api.openf1.org        Formula 1 results, grids and race control messages

Run:  python3 generate.py
Out:  docs/calendar.ics  and  docs/diagnostics.txt

Nothing here needs an API key, an account or a payment method.
"""

import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import leagues as cfg

# Tried in order until one returns something we can read.
FD_URLS = [
    "https://fixturedownload.com/download/json/{slug}",
    "https://fixturedownload.com/feed/json/{slug}",
    "https://fixturedownload.com/view/json/{slug}",
    "https://fixturedownload.com/download/csv/{slug}",
]
NBA_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
NHL_URL = "https://api-web.nhle.com/v1/club-schedule-season/{code}/{season}"
NHL_GAME_URLS = ["https://api-web.nhle.com/v1/gamecenter/{game}/landing",
                 "https://api-web.nhle.com/v1/gamecenter/{game}/boxscore"]
JOLPICA_URL = "https://api.jolpi.ca/ergast/f1/{year}.json?limit=100"
USER_AGENT = "sports-calendar/1.1 (personal calendar generator)"
TIMEOUT = 45

NOW = dt.datetime.now(dt.timezone.utc)
diagnostics = []
seen_events = {}
watched = set()
pending = []
# How many events each feed produced this run. Published alongside the
# calendar so the next run can tell a feed that genuinely has no fixtures
# from a feed that timed out. See shrink_guard_trips().
feed_counts = {}

# Assertions about what was published, not about whether the build ran. A
# green build that quietly produces wrong content is the failure this system
# keeps having, and the shrink guard only catches the version of it where the
# event count drops. These catch content faults. See run_checks().
checks = []


def add_check(name, ok, detail, warning=None):
    """Record one assertion about the output.

    name    short label, shown in diagnostics
    ok      True if the assertion held
    detail  what was measured, shown either way
    warning one plain sentence for the 9pm notification and the unlock page,
            used only when ok is False. It must never contain the letters
            "none", because the phone automation fires on
            "if the text does not contain none" and would go silent.
    """
    if not ok and warning and "none" in warning.lower():
        warning = warning.replace("none", "zero").replace("None", "Zero")
    checks.append({"name": name, "ok": bool(ok), "detail": detail,
                   "warning": warning or detail})


def check_warnings():
    """One sentence per failed check, for the notification and the page."""
    return [c["warning"] for c in checks if not c["ok"]]


def load_watched():
    """Matches you have told the unlock page you have already seen."""
    try:
        with open(cfg.WATCHED_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        note("WATCHED %s not found, treating every result as unseen"
             % cfg.WATCHED_FILE)
        return set()
    except Exception as err:  # noqa: BLE001
        note("WATCHED %s could not be read (%s), treating every result as unseen"
             % (cfg.WATCHED_FILE, err))
        return set()
    items = data.get("watched", []) if isinstance(data, dict) else data
    return {str(x).strip() for x in items if str(x).strip()}


def short_id(uid):
    """A short, stable code for a match. Full identifiers are far too long to
    fit in a web address once a couple of hundred are selected."""
    return hashlib.sha1(uid.encode("utf-8")).hexdigest()[:8]


def is_watched(uid):
    return uid in watched or short_id(uid) in watched


def awaiting_score(start):
    """A match that has kicked off but whose score the feed has not published
    yet.  Two reasons this goes by the clock rather than by the score.

    fixturedownload refreshes about once a day, so waiting for the score
    would mean the nightly reminder arrives a day or two after the match.

    And a match that is still being played counts.  If Sean is watching it
    live he already knows the score, so ticking it off is safe; if he is not
    going to see the end of it he simply leaves it unticked.

    Limited to a window, so a fixture that is abandoned, or that sits in a
    competition whose feed never posts scores, stops asking to be ticked off
    instead of nagging for the rest of the season."""
    if NOW <= start:
        return False
    window = getattr(cfg, "PENDING_NO_SCORE_DAYS", 21)
    return NOW < start + dt.timedelta(days=window)


def hold_future(competition, count):
    if count:
        note("HELD %d future fixture(s) in %s until the last result is unlocked"
             % (count, competition))


def add_pending(uid, title, start, competition, scored=True):
    pending.append({"id": short_id(uid), "uid": uid, "title": title,
                    "date": start.strftime("%Y-%m-%d %H:%M") + " UTC",
                    "competition": competition, "scored": scored})


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
    "%Y%m%d %H%M%S",
    "%Y%m%d",
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

DUPLICATE_WINDOW_HOURS = 36


def event_key(home, away):
    return frozenset((home.strip(), away.strip()))


def already_seen(start, home, away):
    """Same two teams within 36 hours is the same match, even if two sources
    disagree about the kick-off time or land either side of midnight UTC."""
    times = seen_events.get(event_key(home, away))
    if not times:
        return False
    limit = dt.timedelta(hours=DUPLICATE_WINDOW_HOURS)
    return any(abs(start - existing) < limit for existing in times)


def remember_event(start, home, away):
    seen_events.setdefault(event_key(home, away), []).append(start)


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

    # Claim the slot now, so a feed that fails records a zero rather than
    # dropping out of the counts and looking like a slug that was renamed.
    feed_counts.setdefault("feed:%s" % slugs[0], 0)

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
    count = held = upcoming = 0
    feed_upcoming = 0

    # In a knockout competition the next fixture gives the last result away,
    # so it is withheld until that result has been unlocked.
    locked_result = False
    if league.get("knockout"):
        for row in rows:
            home_feed = row.get("hometeam") or row.get("home")
            away_feed = row.get("awayteam") or row.get("away")
            if follow and home_feed not in follow and away_feed not in follow:
                continue
            home_score, away_score = parse_scores(row)
            when = parse_utc(row.get("dateutc") or row.get("date"))
            if home_score is None and not (
                    when and awaiting_score(when)):
                continue
            number = row.get("matchnumber")
            if number is None:
                continue
            if not is_watched("%s-%s@sports-calendar" % (slugs[0], number)):
                locked_result = True
                break

    for index, row in enumerate(rows):
        home_feed = row.get("hometeam") or row.get("home")
        away_feed = row.get("awayteam") or row.get("away")
        for value in (home_feed, away_feed):
            if value:
                seen_names.add(value)
        row_start = parse_utc(row.get("dateutc") or row.get("date"))
        if row_start and row_start > NOW:
            feed_upcoming += 1
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

        number = row.get("matchnumber", index)
        uid = "%s-%s@sports-calendar" % (slugs[0], number)
        home_score, away_score = parse_scores(row)
        if locked_result and home_score is None and start > NOW:
            held += 1
            continue
        # Played counts from the clock, not from the feed, so a match shows up
        # in tonight's reminder even if the score is still a day away.
        played = home_score is not None or awaiting_score(start)
        plain_home, plain_away = home_label, away_label
        if cfg.INCLUDE_SCORES and played:
            if is_watched(uid):
                if home_score is not None:
                    home_label += " [%s]" % home_score
                    away_label += " [%s]" % away_score
            else:
                add_pending(uid, "%s v %s" % (plain_home, plain_away), start,
                            league.get("competition", ""),
                            scored=home_score is not None)

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

        remember_event(start, plain_home, plain_away)

        events.extend(make_event(uid, title, start, sport["minutes"],
                                 row.get("location"), notes))
        count += 1
        if start > NOW:
            upcoming += 1

    unmapped = sorted(n for n in seen_names if n not in league.get("names", {}))
    if unmapped:
        diagnostics.append("FEED NAMES %s: %s" % (slugs[0], " | ".join(unmapped)))
    hold_future(league.get("competition", slugs[0]), held)

    # A competition with no fixtures left anywhere is the signal that the feed
    # needs rolling over to next season's address. Your own team running out of
    # fixtures just means they are done for the year, which is not the same.
    if not feed_upcoming:
        note("SEASON OVER %s has no fixtures left in the whole competition, "
             "roll the slug to next season when it is published" % slugs[0])
    elif count and not upcoming:
        note("TEAM DONE %s: the competition continues but your team has no "
             "fixtures left in it" % slugs[0])

    print("  %s: %d events kept" % (slugs[0], count))
    feed_counts["feed:%s" % slugs[0]] = count
    return events



def build_manual_events():
    """Fixtures typed into leagues.py by hand, for competitions with no feed."""
    entries = getattr(cfg, "MANUAL_FIXTURES", [])
    events = []
    for index, entry in enumerate(entries):
        sport = cfg.SPORTS.get(entry.get("sport"))
        if not sport:
            note("MANUAL row %d: unknown sport %r" % (index + 1, entry.get("sport")))
            continue
        start = parse_utc(entry.get("start"))
        if start is None:
            note("MANUAL row %d: unreadable start %r" % (index + 1, entry.get("start")))
            continue

        pseudo = {"gender": entry.get("gender", "M"),
                  "international": entry.get("international", False),
                  "sport": entry["sport"], "names": {}}
        home_label = team_label(pseudo, entry["home"])
        away_label = team_label(pseudo, entry["away"])

        if cfg.INCLUDE_SCORES and entry.get("score"):
            home_score, away_score = entry["score"]
            home_label += " [%s]" % home_score
            away_label += " [%s]" % away_score

        if sport["format"] == "@":
            title = "%s %s @ %s" % (sport["emoji"], away_label, home_label)
        else:
            title = "%s %s vs. %s" % (sport["emoji"], home_label, away_label)

        remember_event(start, home_label, away_label)
        notes = "\n".join(x for x in (entry.get("competition"),
                                      entry.get("stage")) if x)
        uid = "manual-%s-%s-%s@sports-calendar" % (
            entry["start"].replace(" ", "").replace(":", "").replace("-", ""),
            entry["home"].replace(" ", ""), entry["away"].replace(" ", ""))

        events.extend(make_event(uid, title, start,
                                 entry.get("minutes", sport["minutes"]),
                                 entry.get("location"), notes))

    print("  hand-entered: %d events" % len(entries))
    return events



def _name_from(block, *keys):
    """League feeds nest names differently. Try each shape and take the first."""
    parts = []
    for key in keys:
        value = block.get(key)
        if isinstance(value, dict):
            value = value.get("default")
        if value:
            parts.append(str(value).strip())
    return " ".join(parts).strip()


def build_nba_events(entry):
    payload = fetch_json(NBA_URL)
    dates = payload["leagueSchedule"]["gameDates"]
    wanted = entry["team"].lower()
    events, count = [], 0

    for day in dates:
        for game in day.get("games", []):
            home = _name_from(game.get("homeTeam", {}), "teamCity", "teamName")
            away = _name_from(game.get("awayTeam", {}), "teamCity", "teamName")
            if wanted not in home.lower() and wanted not in away.lower():
                continue
            start = parse_utc(str(game.get("gameDateTimeUTC", "")).replace("T", " "))
            if start is None:
                continue

            game_id = str(game.get("gameId", ""))
            stage = cfg.NBA_STAGES.get(game_id[2:3], "2026/27 Season")
            label = (game.get("gameLabel") or "").strip()
            if label:
                stage = "%s, %s" % (stage, label)

            pseudo = {"gender": entry["gender"], "sport": entry["sport"], "names": {}}
            title = "%s %s @ %s" % (cfg.SPORTS[entry["sport"]]["emoji"],
                                    team_label(pseudo, away),
                                    team_label(pseudo, home))
            notes = "%s\n%s" % (entry["competition"], stage)
            events.extend(make_event("nba-%s@sports-calendar" % game_id, title,
                                     start, cfg.SPORTS[entry["sport"]]["minutes"],
                                     game.get("arenaName"), notes))
            count += 1
    return events, count



PERIOD_NAMES = {1: "1st Period", 2: "2nd Period", 3: "3rd Period"}


def _find_by_period(blob):
    """The period breakdown moves around between NHL endpoints, so look for it
    rather than assuming where it lives."""
    if isinstance(blob, dict):
        if isinstance(blob.get("byPeriod"), list):
            return blob["byPeriod"]
        for value in blob.values():
            found = _find_by_period(value)
            if found:
                return found
    elif isinstance(blob, list):
        for value in blob:
            found = _find_by_period(value)
            if found:
                return found
    return None


def _periods_from_scoring(blob):
    """Rebuild the period breakdown out of summary.scoring.

    The NHL removed summary.linescore.byPeriod from the game centre endpoints
    at some point before 8 October 2026, which is why period scores silently
    stopped working. What is left is summary.scoring: one entry per period,
    each holding that period's goals, and a period with no goals is still
    present with an empty list. Every goal carries awayScore and homeScore as
    a running total for the whole game, so a period's own score is the total
    at the end of it minus the total at the end of the period before."""
    scoring = ((blob or {}).get("summary") or {}).get("scoring")
    if not isinstance(scoring, list) or not scoring:
        return None

    rows = []
    away_before = home_before = 0
    for entry in scoring:
        if not isinstance(entry, dict):
            continue
        away = home = None
        for goal in entry.get("goals") or []:
            if not isinstance(goal, dict):
                continue
            # Running totals only ever go up, so the highest in the period is
            # the one at the end of it, whatever order the goals are listed in.
            for key, current in (("awayScore", away), ("homeScore", home)):
                value = goal.get(key)
                if not isinstance(value, int):
                    continue
                best = value if current is None else max(current, value)
                if key == "awayScore":
                    away = best
                else:
                    home = best
        if away is None:
            away = away_before
        if home is None:
            home = home_before
        rows.append({"periodDescriptor": entry.get("periodDescriptor") or {},
                     "away": away - away_before, "home": home - home_before})
        away_before, home_before = away, home
    return rows or None


def nhl_period_lines(game_id):
    """Returns lines like '1st Period: 1 - 0', in the away then home order the
    event title uses. Empty list if the breakdown cannot be read."""
    # Why each endpoint failed, so the diagnostics line can say whether the
    # request failed or the response simply has no breakdown in it. Without
    # this the two look identical and the fault cannot be told apart.
    why = []
    for template in NHL_GAME_URLS:
        # Not "kind": the loop below already uses that name for the period
        # type, and reusing it here silently blanked the endpoint name.
        endpoint = template.rsplit("/", 1)[-1]
        try:
            payload = fetch_json(template.format(game=game_id))
        except Exception as err:  # noqa: BLE001
            why.append("%s: %s" % (endpoint, err))
            continue
        # byPeriod first, in case the NHL puts it back, then rebuild it from
        # the per-period goals, which is where the scores live now.
        rows = _find_by_period(payload) or _periods_from_scoring(payload)
        if not rows:
            keys = (", ".join(sorted(payload))
                    if isinstance(payload, dict) else type(payload).__name__)
            why.append("%s: no byPeriod and no summary.scoring, all top-level "
                       "keys were %s" % (endpoint, keys or "none"))
            continue
        lines = []
        for row in rows:
            descriptor = row.get("periodDescriptor") or {}
            number = descriptor.get("number")
            kind = str(descriptor.get("periodType") or "").upper()
            if kind == "OT":
                label = "Overtime"
            elif kind == "SO":
                label = "Shootout"
            else:
                label = PERIOD_NAMES.get(number, "Period %s" % number)
            away = row.get("away")
            home = row.get("home")
            if away is None or home is None:
                continue
            lines.append("%s: %s - %s" % (label, away, home))
        if lines:
            return lines
        why.append("%s: byPeriod found but no row had both scores" % endpoint)
    note("NHL could not read the period scores for game %s | %s"
         % (game_id, " | ".join(why) or "no reason recorded"))
    return []


def build_nhl_events(entry):
    url = NHL_URL.format(code=entry["team_code"], season=entry["season"])
    payload = fetch_json(url)
    events, count, breakdowns, eligible = [], 0, 0, 0

    for game in payload.get("games", []):
        home = _name_from(game.get("homeTeam", {}), "placeName", "commonName") \
            or game.get("homeTeam", {}).get("abbrev", "")
        away = _name_from(game.get("awayTeam", {}), "placeName", "commonName") \
            or game.get("awayTeam", {}).get("abbrev", "")
        start = parse_utc(str(game.get("startTimeUTC", "")).replace("T", " "))
        if start is None or not home or not away:
            continue

        stage = cfg.NHL_STAGES.get(game.get("gameType"), "2026/27 Season")
        venue = game.get("venue", {})
        if isinstance(venue, dict):
            venue = venue.get("default")

        pseudo = {"gender": entry["gender"], "sport": entry["sport"], "names": {}}
        home_label = team_label(pseudo, home)
        away_label = team_label(pseudo, away)
        remember_event(start, home_label, away_label)

        uid = "nhl-%s@sports-calendar" % game.get("id")
        home_score = game.get("homeTeam", {}).get("score")
        away_score = game.get("awayTeam", {}).get("score")
        finished = home_score is not None and away_score is not None

        suffix = ""
        outcome = game.get("gameOutcome") or {}
        last = str(outcome.get("lastPeriodType") or "").upper()
        if last in ("OT", "SO"):
            suffix = " " + last

        if cfg.INCLUDE_SCORES and finished:
            if is_watched(uid):
                home_label += " [%s]" % home_score
                away_label += " [%s]" % away_score
            else:
                add_pending(uid, "%s v %s" % (home_label, away_label), start,
                            entry["competition"])
                suffix = ""      # OT would give the result away on its own

        title = "%s %s @ %s%s" % (cfg.SPORTS[entry["sport"]]["emoji"],
                                  away_label, home_label, suffix)
        notes = "%s\n%s" % (entry["competition"], stage)

        if (getattr(cfg, "NHL_PERIOD_SCORES", False) and finished
                and is_watched(uid) and breakdowns < cfg.NHL_PERIOD_LIMIT):
            eligible += 1
            lines = nhl_period_lines(game.get("id"))
            if lines:
                notes += "\n\n" + "\n".join(lines)
                breakdowns += 1
        events.extend(make_event(uid, title, start,
                                 cfg.SPORTS[entry["sport"]]["minutes"],
                                 venue, notes))
        count += 1

    if breakdowns:
        note("NHL added period scores to %d unlocked game(s)" % breakdowns)
    missed = eligible - breakdowns
    add_check("nhl period scores", not missed,
              "%d of %d unlocked finished games have a period breakdown"
              % (breakdowns, eligible),
              "Build warning: %d unlocked ice hockey game(s) have a final "
              "score but no period breakdown" % missed)
    return events, count


def build_official_events():
    """NBA and NHL straight from the leagues, so preseason and playoffs appear."""
    builders = {"nba": build_nba_events, "nhl": build_nhl_events}
    events = []

    for entry in getattr(cfg, "OFFICIAL_SOURCES", []):
        name = entry["source"]
        # Claimed before the call, so a source that fails records a zero.
        # A fallback to fixturedownload records its own slug separately.
        feed_counts.setdefault("official:%s" % name, 0)
        try:
            built, count = builders[name](entry)
        except Exception as err:  # noqa: BLE001
            note("OFFICIAL %s failed (%s), falling back to fixturedownload" % (name, err))
            built, count = [], 0

        if count == 0:
            fallback = dict(entry.get("fallback") or {})
            if fallback:
                note("OFFICIAL %s returned nothing, using %s"
                     % (name, fallback.get("slug")))
                fallback.update({"sport": entry["sport"], "gender": entry["gender"],
                                 "names": {}, "competition": entry["competition"]})
                events.extend(build_league_events(fallback))
            continue

        note("OFFICIAL %s: %d events" % (name, count))
        print("  %s (official): %d events" % (name, count))
        feed_counts["official:%s" % name] = count
        events.extend(built)

    return events



def _unfold_ics(text):
    """iCalendar wraps long lines; continuation lines start with a space."""
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _strip_symbols(text):
    """Remove flags, sport emoji and stray spaces the feeds put in titles."""
    kept = []
    for char in text:
        code = ord(char)
        if (0x1F1E6 <= code <= 0x1F1FF or 0x1F300 <= code <= 0x1FAFF
                or 0xE0000 <= code <= 0xE007F or code in (0x200D, 0xFE0F)
                or 0x2600 <= code <= 0x27BF):
            continue
        kept.append(char)
    return " ".join("".join(kept).split())


SEPARATORS = (" - ", " vs. ", " vs ", " Vs ", " v ", " V ", " @ ", " at ")

GENDER_MARKERS = (" (W)", " (M)", " (w)", " (m)", " Women", " Men",
                  " women", " men", " WFC", " Ladies", " W", " M")


def _clean_team(name):
    """Feeds often append their own gender marker. Ours is added later, so
    'Chelsea (W)' must become 'Chelsea' or the title reads 'Chelsea (W) (W)'."""
    text = name.strip()
    changed = True
    while changed:
        changed = False
        for marker in GENDER_MARKERS:
            if text.endswith(marker) and len(text) > len(marker):
                text = text[: -len(marker)].strip()
                changed = True
    return text


def _split_summary(summary, source=None):
    """Turn a feed title into (first, second, tag, score).

    Handles 'Home - Away [LC] (1-2)', 'Away @ Home', 'Away at Home',
    'Home v Away' and leading emoji or flags."""
    source = source or {}
    text = summary.strip()

    for prefix in ("\u26a0\ufe0f Postponed:", "Postponed:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    for suffix in source.get("strip_suffix", []):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()

    score = None
    if text.endswith(")") and "(" in text:
        head, _, tail = text.rpartition("(")
        tail = tail[:-1]
        if "-" in tail and all(p.strip().isdigit() for p in tail.split("-", 1)):
            score = tuple(p.strip() for p in tail.split("-", 1))
            text = head.strip()

    tag = None
    if text.endswith("]") and "[" in text:
        head, _, tail = text.rpartition("[")
        tag = tail[:-1].strip()
        text = head.strip()

    for splitter in SEPARATORS:
        if splitter in text:
            first, _, second = text.partition(splitter)
            first = _clean_team(_strip_symbols(first))
            second = _clean_team(_strip_symbols(second))
            if first and second:
                return first, second, tag, score
    return None, None, tag, score


def probe_ics(source, text):
    """Describe a feed in diagnostics without adding anything to the calendar."""
    titles, tags, dates = [], set(), []
    summary = start = None
    location = ""
    sample_locations = []

    for line in _unfold_ics(text):
        if line.startswith("BEGIN:VEVENT"):
            summary = start = None
            location = ""
        elif line.startswith("SUMMARY:"):
            summary = line[8:]
        elif line.startswith("LOCATION:"):
            location = line[9:]
        elif line.startswith("DTSTART"):
            start = parse_utc(line.split(":", 1)[-1])
        elif line.startswith("END:VEVENT"):
            if not summary:
                continue
            titles.append(summary)
            if start:
                dates.append(start)
            _, _, tag, _ = _split_summary(summary)
            tags.add(tag or "(none)")
            if location and len(sample_locations) < 2:
                sample_locations.append(location)

    future = [d for d in dates if d >= dt.datetime.now(dt.timezone.utc)]
    span = ""
    if dates:
        span = " | %s to %s" % (min(dates).strftime("%Y-%m-%d"),
                                max(dates).strftime("%Y-%m-%d"))

    note("PROBE %s: %d events, %d upcoming%s | tags: %s"
         % (source.get("name", "?"), len(titles), len(future), span,
            ", ".join(sorted(tags))))
    for title in titles[-6:]:
        note("PROBE %s sample: %s" % (source.get("name", "?"), title))
    if sample_locations:
        note("PROBE %s venue: %s" % (source.get("name", "?"),
                                     " / ".join(sample_locations)))
    else:
        note("PROBE %s venue: none in feed" % source.get("name", "?"))
    print("  probe %s: %d events" % (source.get("name", "?"), len(titles)))


_ICS_NEWLINE = re.compile(r"\\n|\\N|\n")


def _prop(line, name):
    """The value of an iCalendar property, or "" if this is another property.

    Handles parameters, so both DESCRIPTION: and DESCRIPTION;LANGUAGE=en: are
    read. Returns "" rather than None so an empty value is not mistaken for a
    missing one."""
    if not line.startswith(name):
        return ""
    rest = line[len(name):]
    if rest.startswith(":"):
        return rest[1:]
    if rest.startswith(";") and ":" in rest:
        return rest.split(":", 1)[1]
    return ""


def _ics_desc_lines(description):
    """A feed's DESCRIPTION split into its real lines, unescaped and trimmed.

    Feeds write their line breaks as the two characters backslash-n inside a
    single folded DESCRIPTION, so splitting on a real newline is not enough."""
    if not description:
        return []
    out = []
    for part in _ICS_NEWLINE.split(description):
        part = (part.replace("\\,", ",").replace("\\;", ";")
                    .replace("\\\\", "\\").strip())
        if part:
            out.append(part)
    return out


def _stage_window(name, start):
    """The stage for a date, from the month-day windows in leagues.py."""
    windows = getattr(cfg, "ICS_STAGE_WINDOWS", {}).get(name, [])
    when = "%02d-%02d" % (start.month, start.day)
    for window in windows:
        first, last = window["from"], window["to"]
        if first <= last:
            inside = first <= when <= last
        else:                         # a window that wraps over new year
            inside = when >= first or when <= last
        if inside:
            return window.get("stage", "")
    return ""


def _ics_stage(source, description, categories, start):
    """Line 2 of the notes for an .ics fixture, or "" if the feed has none.

    Works through source["stage_from"] in order and takes the first rule that
    produces text. See the ICS_SOURCES comments in leagues.py for the rules."""
    ignore = [word.lower() for word
              in getattr(cfg, "ICS_STAGE_IGNORE", []) + source.get("stage_ignore", [])]
    cut = source.get("stage_cut", "|")
    lines = _ics_desc_lines(description)

    for rule in source.get("stage_from") or []:
        text = ""
        if rule == "description 1":
            text = lines[0] if len(lines) > 0 else ""
        elif rule == "description 2":
            text = lines[1] if len(lines) > 1 else ""
        elif rule == "categories":
            text = (categories or "").replace("\\,", ",").strip()
        elif isinstance(rule, dict) and "fixed" in rule:
            text = rule["fixed"]
        elif isinstance(rule, dict) and "windows" in rule:
            text = _stage_window(rule["windows"], start)
        else:
            note("ICS %s: stage_from rule %r not understood, skipped"
                 % (source.get("name", "?"), rule))
            continue

        for char in cut:
            if char and char in text:
                text = text.split(char, 1)[0]
        for find, replace in source.get("stage_replace", []):
            text = text.replace(find, replace)
        text = text.strip(" .,-–—")
        low = text.lower()
        if text and not any(low.startswith(word) for word in ignore):
            return text
    return ""


def build_ics_events():
    """Fixtures pulled from published team calendars."""
    events = []
    earliest = parse_utc(getattr(cfg, "ICS_EARLIEST", "2000-01-01") + " 00:00")

    missing_stage = []
    for source in getattr(cfg, "ICS_SOURCES", []):
        # Claimed before the fetch, so a feed that fails records a zero.
        if not source.get("probe"):
            feed_counts.setdefault(
                "ics:%s" % source.get("name", source["url"]), 0)
        try:
            text = fetch_text(source["url"])
        except Exception as err:  # noqa: BLE001
            label = "PROBE" if source.get("probe") else "ICS"
            note("%s %s failed: %s" % (label, source.get("name", source["url"]), err))
            continue

        if source.get("probe"):
            probe_ics(source, text)
            continue

        sport = cfg.SPORTS[source["sport"]]
        only = source.get("only_tags")
        locked_result = False
        held = 0
        if source.get("knockout"):
            u = st = when = None
            for line in _unfold_ics(text):
                if line.startswith("BEGIN:VEVENT"):
                    u = st = when = None
                elif line.startswith("UID:"):
                    u = line[4:].strip()
                elif line.startswith("DTSTART"):
                    when = parse_utc(line.split(":", 1)[-1])
                elif line.startswith("SUMMARY:"):
                    _, _, tg, sc = _split_summary(line[8:], source)
                    st = (tg, sc)
                elif line.startswith("END:VEVENT") and u and st and when:
                    if earliest and when < earliest:
                        continue          # old seasons are not spoilers
                    tg, sc = st
                    ready = sc or awaiting_score(when)
                    if ready and (only is None or tg in only):
                        if not is_watched("ics-%s@sports-calendar" % u):
                            locked_result = True
                            break
        wanted = source.get("team", "").lower()
        names = source.get("names", {})
        skip_words = source.get("skip_if_contains", [])
        kept = duplicates = skipped = unparsed = staged = 0
        seen_tags = set()
        summary = start = uid = location = None
        description = categories = None

        # A VEVENT usually contains a VALARM, and a VALARM has a DESCRIPTION
        # of its own, normally the single word "Reminder". Reading the last
        # DESCRIPTION in the event therefore reads the alarm's, not the
        # event's, so anything inside a nested BEGIN block is skipped and the
        # first DESCRIPTION at event level is the one kept.
        inner = 0
        for line in _unfold_ics(text):
            if line.startswith("BEGIN:VEVENT"):
                summary = start = uid = location = None
                description = categories = None
                inner = 0
                continue
            if line.startswith("END:VEVENT"):
                inner = 0
            elif line.startswith("BEGIN:"):
                inner += 1
                continue
            elif line.startswith("END:"):
                inner = max(0, inner - 1)
                continue
            elif inner:
                continue

            if _prop(line, "DESCRIPTION"):
                if description is None:
                    description = _prop(line, "DESCRIPTION")
            elif _prop(line, "CATEGORIES"):
                categories = _prop(line, "CATEGORIES")
            elif line.startswith("SUMMARY:"):
                summary = line[8:]
            elif line.startswith("LOCATION:"):
                location = line[9:].replace("\\,", ",").strip()
            elif line.startswith("DTSTART"):
                start = parse_utc(line.split(":", 1)[-1])
            elif line.startswith("UID:"):
                uid = line[4:].strip()
            elif line.startswith("END:VEVENT"):
                if not summary or start is None:
                    continue
                if earliest and start < earliest:
                    continue
                if any(word in summary for word in skip_words):
                    skipped += 1
                    continue

                first, second, tag, score = _split_summary(summary, source)
                if not first or not second:
                    unparsed += 1
                    continue
                seen_tags.add(tag or "(none)")
                if only is not None and tag not in only:
                    continue

                first = names.get(first, first)
                second = names.get(second, second)
                if wanted and wanted not in first.lower() \
                        and wanted not in second.lower():
                    continue

                if source.get("away_first"):
                    home, away = second, first
                else:
                    home, away = first, second

                pseudo = {"gender": source["gender"], "sport": source["sport"],
                          "international": source.get("international", False),
                          "names": {}}
                home_label = team_label(pseudo, home)
                away_label = team_label(pseudo, away)

                if already_seen(start, home_label, away_label):
                    duplicates += 1
                    continue
                remember_event(start, home_label, away_label)

                if locked_result and not score and start > NOW:
                    held += 1
                    continue
                played = bool(score) or awaiting_score(start)
                uid = "ics-%s@sports-calendar" % (uid or start.isoformat())
                if cfg.INCLUDE_SCORES and played:
                    if is_watched(uid):
                        if score and source.get("away_first"):
                            away_label += " [%s]" % score[0]
                            home_label += " [%s]" % score[1]
                        elif score:
                            home_label += " [%s]" % score[0]
                            away_label += " [%s]" % score[1]
                    else:
                        add_pending(uid, "%s v %s" % (home_label, away_label),
                                    start, source.get("name", ""),
                                    scored=bool(score))

                if sport["format"] == "@":
                    title = "%s %s @ %s" % (sport["emoji"], away_label, home_label)
                else:
                    title = "%s %s vs. %s" % (sport["emoji"], home_label, away_label)

                competition = source.get("tag_names", {}).get(
                    tag, source.get("competition", tag or ""))
                domestic = source.get("domestic_clubs")
                if domestic and not tag:
                    opponent = second if wanted in first.lower() else first
                    if opponent in domestic:
                        competition = source.get("domestic_competition",
                                                 competition)
                    else:
                        competition = source.get("european_competition",
                                                 competition)
                stage = _ics_stage(source, description, categories, start)
                if stage:
                    staged += 1
                notes = "\n".join(x for x in (competition, stage) if x)
                events.extend(make_event(uid, title, start, sport["minutes"],
                                         location, notes))
                kept += 1
                if kept <= 3:
                    note("ICS %s sample: %s | %s"
                         % (source.get("name", "?"), title,
                            notes.replace("\n", " / ")))

        note("ICS %s: kept %d, %d duplicates dropped, %d filtered out, "
             "%d unreadable, %d with a stage line | tags: %s"
             % (source.get("name", "?"), kept, duplicates, skipped, unparsed,
                staged, ", ".join(sorted(seen_tags)) or "none"))
        hold_future(source.get("name", "?"), held)
        feed_counts["ics:%s" % source.get("name", source["url"])] = kept
        if source.get("stage_from") and kept and staged < kept:
            missing_stage.append("%s %d of %d"
                                 % (source.get("name", "?"), kept - staged, kept))
        print("  ics %s: %d events" % (source.get("name", "?"), kept))

    add_check("ics stage lines", not missing_stage,
              "every .ics feed put a stage on every event it kept"
              if not missing_stage else
              "events with no stage line: " + "; ".join(missing_stage),
              "Build warning: .ics fixtures are missing their stage line (%s)"
              % "; ".join(missing_stage))
    return events


# ---------------------------------------------------------------------------
# Formula 1: session times from Jolpica, results and incidents from OpenF1
# ---------------------------------------------------------------------------
# OpenF1 is free and keyless for historical data, which is anything more than
# 30 minutes after a session finished. The free allowance is 30 requests a
# minute, so every call is spaced out below and the run gives up quietly if it
# is ever rate limited rather than half-filling the calendar.
#
# Finished sessions are stored in docs/f1_results.json, which the build
# publishes to Pages alongside the calendar and reads back on the next run.
# That is why a completed session costs no requests at all after the first
# time. Nothing is committed to the repository and no workflow change is
# needed.

OPENF1_URL = "https://api.openf1.org/v1/%s"

# The build publishes this file to Pages and reads it back on the next run.
F1_CACHE_URL_DEFAULT = ("https://2dwb9p9kdj-ship-it.github.io/"
                        "sports-calendar/f1_results.json")

# Our session labels are not always what OpenF1 calls them.
F1_OPENF1_NAMES = {"Grand Prix": "Race"}

# Sessions Sean watches, so their results stay hidden until he ticks them off.
F1_LOCKED_KINDS = ("qualifying", "sprint_qualifying", "sprint", "race")

# Feed team names are long. These are the versions that go in the calendar.
F1_TEAMS = {
    "Red Bull Racing": "Red Bull",
    "Haas F1 Team": "Haas",
    "Kick Sauber": "Sauber",
    "Stake F1 Team Kick Sauber": "Sauber",
    "RB": "Racing Bulls",
    "Visa Cash App RB": "Racing Bulls",
    "Aston Martin Aramco": "Aston Martin",
    "Alpine F1 Team": "Alpine",
    "Cadillac F1 Team": "Cadillac",
    "Audi F1 Team": "Audi",
}

# Short words in a race control message that should stay in capitals.
RC_KEEP = {"DRS", "SC", "VSC", "FIA", "TBC", "GP", "F1", "DNF", "DNS", "DSQ"}

# Bumped whenever the stored results would be written differently. A stored
# session whose marker does not match is looked up again, while still being
# shown, so the calendar never goes blank during a rebuild.
F1_BUILD = 2

# Race control ends every message about an incident with the time the incident
# happened, and repeats that time on each follow-up. It is therefore the
# incident's identifier, and the way to collapse "noted", "under
# investigation" and the final decision into a single line.
_RC_STAMP = re.compile(r"\s*\((\d{1,2}:\d{2}:\d{2})\)\s*$")
_RC_CARS = re.compile(r"\bCARS?\s+(\d+)\s*\(([A-Za-z]{3})\)"
                      r"(?:\s+AND\s+(\d+)\s*\(([A-Za-z]{3})\))?", re.I)

RC_INCIDENT = ("STOPPED", "SPUN", "COLLISION", "CONTACT", "CAR OFF",
               "PENALTY", "DISQUALIFIED", "RETIRED", "BLACK AND WHITE")
RC_NO_ACTION = ("NO FURTHER ACTION", "NO FURTHER INVESTIGATION",
                "NO INVESTIGATION", "TAKEN NO FURTHER")

_openf1_last = [0.0]
_openf1_off = [False]
_openf1_calls = [0]


def openf1(endpoint, **params):
    """One throttled call to OpenF1. Returns a list, or None on any failure."""
    if _openf1_off[0]:
        return None
    query = "&".join("%s=%s" % (key, urllib.parse.quote(str(value)))
                     for key, value in params.items())
    url = OPENF1_URL % endpoint + ("?" + query if query else "")
    spacing = getattr(cfg, "OPENF1_MIN_SECONDS", 2.2)
    wait = spacing - (time.monotonic() - _openf1_last[0])
    if wait > 0:
        time.sleep(wait)
    _openf1_last[0] = time.monotonic()
    _openf1_calls[0] += 1
    try:
        payload = json.loads(fetch_text(url))
    except urllib.error.HTTPError as err:
        if err.code == 429:
            note("OPENF1 rate limited, no more result lookups this run")
            _openf1_off[0] = True
        elif err.code == 404:
            return []          # OpenF1 says 404 when a query matches nothing
        else:
            note("OPENF1 %s failed: HTTP %s" % (endpoint, err.code))
        return None
    except Exception as err:  # noqa: BLE001
        note("OPENF1 %s failed: %s" % (endpoint, err))
        return None
    return payload if isinstance(payload, list) else None


def empty_f1_cache():
    return {"drivers": {}, "sessions": {}}


def load_f1_cache():
    """Read back the results file this build published last time."""
    url = getattr(cfg, "F1_CACHE_URL", F1_CACHE_URL_DEFAULT)
    if not url:
        return empty_f1_cache()
    buster = "?t=%d" % int(NOW.timestamp())
    try:
        data = json.loads(fetch_text(url + buster))
    except Exception as err:  # noqa: BLE001
        note("F1 CACHE could not be read (%s), results will be fetched again "
             "this run" % err)
        return empty_f1_cache()
    if not isinstance(data, dict):
        return empty_f1_cache()
    data.setdefault("drivers", {})
    data.setdefault("sessions", {})
    note("F1 CACHE holds %d finished session(s)" % len(data["sessions"]))
    return data


def save_f1_cache(cache):
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    name = getattr(cfg, "F1_CACHE_FILE", "f1_results.json")
    path = os.path.join(cfg.OUTPUT_DIR, name)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, ensure_ascii=False, indent=1)
        print("Wrote %s" % path)
    except Exception as err:  # noqa: BLE001
        note("F1 CACHE could not be written (%s)" % err)


def _lap_time(seconds):
    """91.824 becomes 1:31.824."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    minutes = int(value // 60)
    rest = value - minutes * 60
    if minutes:
        return "%d:%06.3f" % (minutes, rest)
    return "%.3f" % rest


def _race_time(seconds):
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    hours = int(value // 3600)
    minutes = int((value - hours * 3600) // 60)
    rest = value - hours * 3600 - minutes * 60
    if hours:
        return "%d:%02d:%06.3f" % (hours, minutes, rest)
    return "%d:%06.3f" % (minutes, rest)


def _gap_text(value):
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value.strip()
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds >= 60:
        return "+" + (_lap_time(seconds) or "")
    return "+%.3f" % seconds


def _ordinal(number):
    try:
        value = int(number)
    except (TypeError, ValueError):
        return str(number)
    if 10 <= value % 100 <= 20:
        return "%dth" % value
    return "%d%s" % (value, {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th"))


def _last_set_time(duration):
    """Qualifying holds three values, one per phase. Take the last one set."""
    if isinstance(duration, list):
        for value in reversed(duration):
            if value not in (None, ""):
                return value
        return None
    return duration


def _tidy_rc(text, codes):
    """Race control shouts in capitals. Turn it into a readable sentence."""
    words = []
    for word in str(text).split():
        bare = word.strip("()[]{}.,:;!?").upper()
        words.append(word.upper() if (bare in codes or bare in RC_KEEP)
                     else word.lower())
    line = " ".join(words)
    for word in ("car", "turn", "lap", "pit"):
        line = line.replace(" %s " % word, " %s " % word.capitalize())
    line = line.rstrip(" .-")
    return line[:1].upper() + line[1:]


def _driver_name(row, number):
    """A surname. OpenF1 writes full names as 'Ayumu IWASA', so when the
    surname field is missing the shouted half has to be tidied."""
    name = str(row.get("last_name") or "").strip()
    if not name:
        parts = str(row.get("full_name") or "").split()
        name = parts[-1].title() if parts else ""
    return name or ("Car %s" % number)


def f1_driver_map(cache, session_key, numbers):
    """number -> name, team and three letter code, for THIS session.

    Looked up every time a session's results are fetched. Drivers move
    between teams mid-season, so a team remembered from March is wrong by
    October: Lawson was shown in a Red Bull for months after he left it.
    The season-wide store is kept only as a fallback for a number this
    session's own list does not mention."""
    known = cache["drivers"]
    for row in openf1("drivers", session_key=session_key) or []:
        number = str(row.get("driver_number"))
        if number == "None":
            continue
        team = row.get("team_name") or ""
        known[number] = {
            "name": _driver_name(row, number),
            "code": (row.get("name_acronym") or "").upper(),
            "team": F1_TEAMS.get(team, team),
        }
    return known


def f1_classification(cache, session_key):
    rows = openf1("session_result", session_key=session_key)
    if not rows:
        return None
    drivers = f1_driver_map(cache, session_key,
                            [row.get("driver_number") for row in rows])

    def order(row):
        """Classified by position, then retirements by distance covered,
        which is how a timing screen lists them."""
        position = row.get("position")
        if position:
            return (0, position, 0)
        return (1, 0, -(row.get("number_of_laps") or 0))

    out = []
    for row in sorted(rows, key=order):
        number = str(row.get("driver_number"))
        who = drivers.get(number, {"name": "Car %s" % number, "team": "",
                                   "code": ""})
        out.append({"position": row.get("position"), "number": number,
                    "name": who["name"], "team": who["team"],
                    "code": who["code"], "laps": row.get("number_of_laps"),
                    "duration": row.get("duration"),
                    "gap": row.get("gap_to_leader"),
                    "dnf": bool(row.get("dnf")), "dns": bool(row.get("dns")),
                    "dsq": bool(row.get("dsq"))})
    return out


def f1_fastest_lap(session_key):
    """The quickest lap of a race. Pit out laps do not count."""
    laps = openf1("laps", session_key=session_key)
    if not laps:
        return None
    best = None
    for lap in laps:
        if lap.get("is_pit_out_lap"):
            continue
        try:
            value = float(lap.get("lap_duration"))
        except (TypeError, ValueError):
            continue
        if best is None or value < best["seconds"]:
            best = {"seconds": value, "number": str(lap.get("driver_number")),
                    "lap": lap.get("lap_number")}
    return best


def f1_grid(quali_key, race_key, classification):
    """The grid as it will actually line up, with where each driver qualified.

    OpenF1 files the grid against the qualifying session, not the race, so
    that is tried first. The race key is kept as a fallback."""
    rows = None
    for label, key in (("qualifying", quali_key), ("race", race_key)):
        rows = openf1("starting_grid", session_key=key)
        if rows:
            note("OPENF1 starting grid found on the %s session (%s)"
                 % (label, key))
            break
    if not rows:
        return None
    qualified = {row["number"]: row["position"] for row in classification or []}
    out = []
    for row in sorted(rows, key=lambda r: r.get("position") or 99):
        number = str(row.get("driver_number"))
        out.append({"position": row.get("position"), "number": number,
                    "qualified": qualified.get(number)})
    return out


def _rc_names(text, by_number):
    """'Car 5 (BOR)' becomes 'Bortoleto'; a pair becomes 'A and B'."""
    def swap(match):
        first = by_number.get(match.group(1))
        second = by_number.get(match.group(3)) if match.group(3) else None
        if first and second:
            return "%s and %s" % (first, second)
        if first and not match.group(3):
            return first
        return match.group(0)
    return _RC_CARS.sub(swap, text)


def _rc_safety_cars(messages):
    """Safety cars and virtual safety cars, counted apart and named right.

    Race control writes the virtual one as 'VSC DEPLOYED' as often as
    'VIRTUAL SAFETY CAR DEPLOYED', so looking only for the word virtual
    reports a VSC as a full safety car."""
    full, virtual = [], []
    for message in messages:
        text = str(message.get("message", "") or "").upper()
        if "DEPLOYED" not in text:
            continue
        lap = message.get("lap_number")
        if "VIRTUAL" in text or re.search(r"\bVSC\b", text):
            virtual.append(lap)
        else:
            full.append(lap)

    def phrase(label, laps):
        if not laps:
            return None
        known = [str(lap) for lap in laps if lap]
        if not known:
            return "%s deployed %s" % (
                label, "once" if len(laps) == 1 else "%d times" % len(laps))
        if len(known) == 1:
            return "%s on lap %s" % (label, known[0])
        return "%s on laps %s and %s" % (label, ", ".join(known[:-1]),
                                         known[-1])

    return [line for line in (phrase("Safety car", full),
                              phrase("Virtual safety car", virtual)) if line]


def _rc_incidents(messages, codes, by_number):
    """One line per incident, saying how it ended rather than every step.

    Race control reports the same incident several times: noted, under
    investigation, the decision, then the penalty being served. Grouping on
    the incident time keeps one line, and an incident the stewards looked at
    and let go is dropped rather than taking up a line to say nothing."""
    groups, order = {}, []
    for message in messages:
        text = str(message.get("message", "") or "")
        upper = text.upper()
        if not any(word in upper for word in RC_INCIDENT):
            continue
        found = _RC_STAMP.search(text)
        key = found.group(1) if found else upper
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(text)

    lines = []
    for key in order:
        texts = groups[key]
        if any(phrase in texts[-1].upper() for phrase in RC_NO_ACTION):
            continue
        # The decision itself reads better than the later "penalty served".
        chosen = None
        for text in texts:
            upper = text.upper()
            if "PENALTY" in upper and "SERVED" not in upper:
                chosen = text
                break
        chosen = _RC_STAMP.sub("", chosen or texts[-1])
        chosen = re.sub(r"^\s*FIA STEWARDS:\s*", "", chosen, flags=re.I)
        # Tidy first, then put the names in, so the names keep their capitals.
        line = _rc_names(_tidy_rc(chosen, codes), by_number)
        lines.append(line.replace(" - ", ", "))
    return lines


def f1_incidents(session_key, kind, classification):
    """Dot points built from race control messages and the classification.

    Everything here is derived mechanically. Race control already writes in
    near-English, so these read reasonably, but nothing here is judgement
    about what mattered."""
    codes = {row["code"] for row in classification or [] if row.get("code")}
    by_number = {row["number"]: row["name"] for row in classification or []}
    messages = openf1("race_control", session_key=session_key) or []

    lines = []

    def add(line):
        if line and line not in lines:
            lines.append(line)

    # Who set the pace.
    ranked = [row for row in classification or [] if row.get("position")]
    ranked.sort(key=lambda row: row["position"])
    if len(ranked) >= 2:
        raw = _last_set_time(ranked[1].get("gap"))
        margin = None
        if isinstance(raw, (int, float)):
            margin = "%.3fs" % float(raw)
        elif isinstance(raw, str) and raw.strip():
            margin = raw.strip()
        if kind == "practice" and margin:
            add("%s quickest, %s clear of %s"
                % (ranked[0]["name"], margin, ranked[1]["name"]))
        elif kind in ("qualifying", "sprint_qualifying") and margin:
            add("%s on pole, %s clear of %s"
                % (ranked[0]["name"], margin, ranked[1]["name"]))

    # Red flags.
    reds = [m for m in messages if str(m.get("flag", "")).upper() == "RED"]
    if len(reds) == 1:
        add("Session red flagged once")
    elif len(reds) > 1:
        add("Session red flagged %d times" % len(reds))

    for line in _rc_safety_cars(messages):
        add(line)

    for line in _rc_incidents(messages, codes, by_number):
        add(line)

    # A driver who barely ran is usually a driver with a problem.
    if kind == "practice":
        counts = sorted(row.get("laps") or 0 for row in classification or [])
        if len(counts) >= 5 and counts[len(counts) // 2] > 0:
            median = counts[len(counts) // 2]
            quiet = [row for row in classification or []
                     if (row.get("laps") or 0) and row["laps"] < median * 0.7]
            if quiet:
                fewest = min(quiet, key=lambda row: row["laps"])
                add("%s completed only %d lap%s, fewest of anyone"
                    % (fewest["name"], fewest["laps"],
                       "" if fewest["laps"] == 1 else "s"))

    return lines[:getattr(cfg, "F1_BULLET_LIMIT", 6)]


def f1_result_lines(record, kind):
    """The body of the calendar note for one finished session."""
    rows = record.get("rows") or []
    lines = []

    if kind in ("qualifying", "sprint_qualifying"):
        ranked = [row for row in rows if row.get("position")]
        total = len(ranked)
        second = 10 + max(0, total - 10) // 2
        heads = (("SQ3", "SQ2", "SQ1") if kind == "sprint_qualifying"
                 else ("Q3", "Q2", "Q1"))
        groups = [(heads[0], 1, 10), (heads[1], 11, second),
                  (heads[2], second + 1, total)]
        for head, first, last in groups:
            block = [row for row in ranked if first <= row["position"] <= last]
            if not block:
                continue
            if lines:
                lines.append("")
            lines.append(head)
            for row in block:
                shown = _lap_time(_last_set_time(row.get("duration")))
                lines.append("%d. %s (%s) %s"
                             % (row["position"], row["name"], row["team"],
                                shown or "no time"))
        grid = record.get("grid")
        if grid:
            lines.append("")
            lines.append("Starting grid")
            for row in grid:
                name = record.get("names", {}).get(row["number"], row["number"])
                dropped = (row.get("qualified")
                           and row["qualified"] < row["position"])
                lines.append("%d. %s%s"
                             % (row["position"], name,
                                " (qualified %s)" % _ordinal(row["qualified"])
                                if dropped else ""))

    elif kind in ("race", "sprint"):
        fastest = record.get("fastest") or {}
        place = 0
        for row in rows:
            place += 1
            out = False
            if row.get("dsq"):
                value, out = "DSQ", True
            elif row.get("dns"):
                value, out = "DNS", True
            elif row.get("dnf"):
                value, out = "DNF", True
            elif row.get("position") == 1:
                value = _race_time(row.get("duration")) or ""
            else:
                value = (_gap_text(row.get("gap"))
                         or _race_time(row.get("duration")) or "")
            # A retirement is worth more with the distance it reached, and
            # OpenF1 gives no finishing position to a car too far behind to
            # be classified, so the numbering carries on instead.
            laps = row.get("laps")
            if out and laps:
                value += ", %d lap%s" % (laps, "" if laps == 1 else "s")
            mark = ""
            if fastest.get("number") == row["number"]:
                mark = " (fastest lap %s)" % _lap_time(fastest.get("seconds"))
            lines.append("%s. %s (%s) %s%s"
                         % (row.get("position") or place, row["name"],
                            row["team"], value, mark))

    else:  # practice
        for row in rows:
            if row.get("position") == 1:
                value = _lap_time(row.get("duration")) or "no time"
            else:
                value = (_gap_text(row.get("gap"))
                         or _lap_time(row.get("duration")) or "no time")
            laps = row.get("laps")
            tail = ", %d lap%s" % (laps, "" if laps == 1 else "s") if laps else ""
            lines.append("%s. %s (%s) %s%s"
                         % (row.get("position") or "-", row["name"],
                            row["team"], value, tail))

    bullets = record.get("bullets") or []
    if bullets:
        if lines:
            lines.append("")
        lines.extend("- " + line for line in bullets)
    return lines


def f1_kind(label):
    low = label.lower()
    if low.startswith("practice"):
        return "practice"
    if low == "sprint qualifying":
        return "sprint_qualifying"
    if low == "qualifying":
        return "qualifying"
    if low == "sprint":
        return "sprint"
    return "race"


def f1_session_index():
    """Every OpenF1 session of the season, so each one can be matched by name
    and start time rather than by guessing at meeting names."""
    rows = openf1("sessions", year=cfg.F1_SEASON)
    if not rows:
        note("OPENF1 returned no session list, F1 results are unavailable "
             "this run")
        return []
    index = []
    for row in rows:
        start = parse_utc(str(row.get("date_start", ""))
                          .replace("T", " ").split("+")[0])
        if start is None:
            continue
        index.append({"key": row.get("session_key"),
                      "name": str(row.get("session_name") or "").strip(),
                      "start": start})
    note("OPENF1 session list: %d sessions in %s" % (len(index), cfg.F1_SEASON))
    return index


def f1_match(index, label, start):
    """Find the OpenF1 session that matches one of our scheduled sessions."""
    wanted = F1_OPENF1_NAMES.get(label, label).lower()
    best, best_gap = None, None
    for item in index:
        if item["name"].lower() != wanted:
            continue
        gap = abs((item["start"] - start).total_seconds())
        if gap > 6 * 3600:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = item, gap
    return best


def build_f1_events():
    sport = cfg.SPORTS["f1"]
    feed_counts.setdefault("f1:sessions", 0)
    try:
        payload = fetch_json(JOLPICA_URL.format(year=cfg.F1_SEASON))
    except Exception as err:  # noqa: BLE001
        note("SKIPPED Formula 1: %s" % err)
        return []

    races = payload["MRData"]["RaceTable"]["Races"]
    results_on = getattr(cfg, "F1_RESULTS", True)
    finalise = dt.timedelta(hours=getattr(cfg, "F1_FINALISE_HOURS", 6))
    budget = getattr(cfg, "F1_MAX_NEW_SESSIONS", 30)

    cache = load_f1_cache() if results_on else empty_f1_cache()
    index = f1_session_index() if results_on else []
    stored = cache["sessions"]

    events = []
    count = shown = locked = unmatched = fetched = 0
    waiting = no_grid = rebuilt = empty_results = 0
    sampled = set()

    for race in reversed(races):
        grand_prix = race.get("raceName", "Grand Prix")
        circuit = race.get("Circuit", {}).get("circuitName", "")
        round_no = race.get("round", "")

        # Every session of this weekend, in one list, so qualifying can reach
        # the grid that belongs to the race.
        planned = []
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
            planned.append({"key": key, "label": label, "minutes": minutes,
                            "start": start, "kind": f1_kind(label)})

        by_kind = {item["kind"]: item for item in planned}

        for item in planned:
            label, start = item["label"], item["start"]
            kind, minutes = item["kind"], item["minutes"]
            title = "%s F1: %s (%s)" % (sport["emoji"], label, grand_prix)
            uid = "f1-%s-%s-%s@sports-calendar" % (cfg.F1_SEASON, round_no,
                                                   item["key"])
            notes = "%s\nRound %s" % (cfg.F1_COMPETITION, round_no)
            count += 1

            # Nothing to look up until the session is over and OpenF1 has
            # released it, which is 30 minutes after the finish.
            ended = start + dt.timedelta(minutes=minutes or 60)
            ready = results_on and NOW > ended + dt.timedelta(minutes=40)

            record = None
            if ready:
                match = f1_match(index, label, start)
                if match is None:
                    unmatched += 1
                else:
                    session_key = str(match["key"])
                    record = stored.get(session_key)
                    settled = NOW > ended + finalise
                    needs_rows = (record is None or not record.get("final")
                                  or record.get("build") != F1_BUILD)
                    if record is not None and record.get("build") != F1_BUILD:
                        rebuilt += 1

                    # The grid can still change on Saturday night, so it is
                    # looked at again until the race has actually started.
                    target = None
                    if kind == "qualifying":
                        target = by_kind.get("race")
                    elif kind == "sprint_qualifying":
                        target = by_kind.get("sprint")
                    needs_grid = False
                    if target is not None and NOW > ended:
                        needs_grid = (record is None
                                      or not record.get("grid_final"))

                    if (needs_rows or needs_grid) and (fetched >= budget
                                                       or _openf1_off[0]):
                        waiting += 1
                    elif needs_rows or needs_grid:
                        fetched += 1
                        record = dict(record or {})
                        if needs_rows:
                            rows = f1_classification(cache, session_key)
                            if rows is not None:
                                record["rows"] = rows
                                record["bullets"] = f1_incidents(
                                    session_key, kind, rows)
                                record["final"] = settled
                                record["build"] = F1_BUILD
                                if kind in ("race", "sprint"):
                                    record["fastest"] = f1_fastest_lap(
                                        session_key)
                        if needs_grid and record.get("rows"):
                            race_match = f1_match(index, target["label"],
                                                  target["start"])
                            grid = None
                            if race_match is not None:
                                grid = f1_grid(session_key,
                                               str(race_match["key"]),
                                               record["rows"])
                            if grid:
                                record["grid"] = grid
                                record["names"] = {
                                    row["number"]: row["name"]
                                    for row in record["rows"]}
                            # Stop looking once the race is under way, even if
                            # no grid was ever published, or every build would
                            # keep asking for something that is not coming.
                            record["grid_final"] = NOW > target["start"]
                            if not grid and record["grid_final"]:
                                no_grid += 1
                        if record.get("rows"):
                            stored[session_key] = record

            if record and record.get("rows"):
                if kind in F1_LOCKED_KINDS and not is_watched(uid):
                    add_pending(uid, "F1 %s (%s)" % (label, grand_prix),
                                start, cfg.F1_COMPETITION)
                    locked += 1
                else:
                    body = f1_result_lines(record, kind)
                    if not body and (record.get("rows") or record.get("bullets")):
                        empty_results += 1
                    if body:
                        notes += "\n\n" + "\n".join(body)
                        shown += 1
                        # One sample per session type, so a run that goes
                        # green while producing nonsense is visible here.
                        if kind not in sampled:
                            sampled.add(kind)
                            example = next((line for line in body
                                            if "(" in line), body[0])
                            note("F1 sample %s: %s" % (label, example))
                            # A bullet too, since the classification can be
                            # perfect while the written commentary is wrong.
                            bullet = next((line for line in body
                                           if line.startswith("- ")), None)
                            note("F1 bullet %s: %s"
                                 % (label, bullet or "none in this session"))

            events.extend(make_event(uid, title, start, minutes, circuit, notes))

    feed_counts["f1:sessions"] = count
    add_check("f1 stored results", not empty_results,
              "%d stored session(s) produced result lines" % shown,
              "Build warning: %d stored Formula 1 session(s) hold results "
              "but produced no lines in the calendar" % empty_results)
    if results_on:
        if index:
            live = {str(item["key"]) for item in index}
            for key in [k for k in stored if k not in live]:
                del stored[key]
        save_f1_cache(cache)
        note("F1 %d sessions, %d with results shown, %d waiting to be "
             "unlocked, %d looked up this run, %d OpenF1 requests"
             % (count, shown, locked, fetched, _openf1_calls[0]))
        if unmatched:
            note("F1 %d finished session(s) had no matching OpenF1 session"
                 % unmatched)
        if no_grid:
            note("F1 %d session(s) have no starting grid published anywhere "
                 "in OpenF1, so the grid is left out of those" % no_grid)
        if rebuilt:
            note("F1 %d stored session(s) are being written again in the "
                 "current layout, the old version shows until each is done"
                 % rebuilt)
        if waiting:
            note("F1 %d session(s) still to look up, they will fill in over "
                 "the next %d build(s)"
                 % (waiting, (waiting + budget - 1) // budget))

    print("  formula-1: %d sessions" % count)
    return events


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Words that have turned up as a note's second line when a feed was being
# read wrongly. "Reminder" is what a VALARM calls itself, and it sat in every
# NBA note for a build on 8 October.
NOTE_JUNK = ("reminder", "alarm", "alert", "notification", "watch",
             "download", "http", "calendar not up to date")


def check_notes(body):
    """Read the finished calendar and assert every event has usable notes.

    Deliberately reads the written output rather than the values the builders
    held, because the faults worth catching are the ones where a builder was
    happy with what it produced. make_event escapes real newlines to the two
    characters backslash-n, so that is what a note's lines are split on."""
    missing = thin = junk = 0
    events = 0
    has_note = False
    for line in body:
        if line == "BEGIN:VEVENT":
            events += 1
            has_note = False
        elif line.startswith("DESCRIPTION:"):
            has_note = True
            parts = [p.strip() for p in line[12:].split("\\n")]
            parts = [p for p in parts if p]
            if len(parts) < 2:
                thin += 1
            elif parts[1].lower().startswith(NOTE_JUNK):
                junk += 1
        elif line == "END:VEVENT" and not has_note:
            missing += 1

    faults = missing + thin + junk
    detail = ("all %d events have a competition and a stage line" % events
              if not faults else
              "%d events: %d with no notes, %d with only one line, "
              "%d whose second line looks like feed boilerplate"
              % (events, missing, thin, junk))
    add_check("event notes", not faults, detail,
              "Build warning: %d calendar event(s) have missing or wrong "
              "notes" % faults)


def run_checks():
    """Print every assertion, passes included, so a silent pass is visible."""
    # A deliberate failure, for proving the whole alarm chain still works:
    # check fails, warning reaches notify.txt, the 9pm shortcut fires, the
    # unlock page shows the banner. Switched on only by running the workflow
    # by hand with test_warning ticked, so it can never fire on a scheduled
    # build. An untested alarm is worse than no alarm, because it is believed.
    if os.environ.get("TEST_WARNING", "").strip().lower() in ("1", "true", "yes"):
        add_check("test warning", False,
                  "deliberate, from the test_warning tick box on this run",
                  "Build warning: this is a test of the warning system, "
                  "nothing is actually wrong")

    failed = [c for c in checks if not c["ok"]]
    for item in checks:
        note("CHECK %s: %s, %s"
             % (item["name"], "PASS" if item["ok"] else "FAIL", item["detail"]))
    if failed:
        note("CHECK %d of %d failed. The 9pm notification will fire every "
             "night until this is fixed." % (len(failed), len(checks)))
    return failed


def save_feed_counts(total):
    """Publish this run's per-feed event counts next to the calendar."""
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(cfg.OUTPUT_DIR, "feed_counts.json")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                       "total": total, "feeds": feed_counts},
                      handle, ensure_ascii=False, indent=1, sort_keys=True)
        print("Wrote %s" % path)
    except Exception as err:  # noqa: BLE001
        note("FEED COUNTS could not be written (%s)" % err)


def shrink_guard_trips(total):
    """True when a feed that worked last time has quietly stopped working.

    A feed that times out does not raise, it just returns nothing, so the
    build used to publish a calendar with a whole league missing from it and
    report success. Nothing on disk survives between runs, so the previous
    run's counts are read back from Pages, the same way the F1 cache is."""
    if os.environ.get("ALLOW_SHRINK", "").strip().lower() in ("1", "true", "yes"):
        note("SHRINK GUARD skipped, this run was started with allow_shrink "
             "ticked")
        return False

    url = getattr(cfg, "PUBLISHED_COUNTS_URL", "")
    keep = getattr(cfg, "MIN_FEED_FRACTION", 0)
    if not url or not keep:
        return False
    try:
        previous = fetch_json(url)
    except Exception as err:  # noqa: BLE001
        note("SHRINK GUARD has no previous counts to compare against (%s), "
             "publishing anyway" % err)
        return False

    before = (previous or {}).get("feeds") or {}
    if not before:
        return False

    lost = []
    for key, was in sorted(before.items()):
        if key not in feed_counts:
            continue      # the slug was renamed here, so there is nothing
                          # to compare it against
        now = feed_counts[key]
        if was > 0 and now < max(1, int(was * keep)):
            lost.append("%s had %d, now has %d" % (key, was, now))

    if not lost:
        note("SHRINK GUARD ok: %d events across %d feeds, none of them down "
             "on the last published run" % (total, len(feed_counts)))
        return False

    note("ERROR shrink guard: the calendar has NOT been overwritten, because "
         "%d feed(s) lost fixtures since the last published run: %s. Look at "
         "the SKIPPED and FEED lines above. A timeout fixes itself on the "
         "next run two hours later. If the drop is real, because a season "
         "slug was rolled over here, run the workflow by hand from the "
         "Actions tab with allow_shrink ticked."
         % (len(lost), "; ".join(lost)))
    return True


def main():
    print("Building calendar...")
    watched.update(load_watched())
    note("WATCHED %d matches already seen" % len(watched))
    body = []
    for league in cfg.LEAGUES:
        body.extend(build_league_events(league))
    body.extend(build_official_events())
    body.extend(build_ics_events())
    body.extend(build_manual_events())
    try:
        body.extend(build_f1_events())
    except Exception as err:  # noqa: BLE001
        note("F1 builder failed (%s), the rest of the calendar is unaffected"
             % err)

    count = len([1 for line in body if line == "BEGIN:VEVENT"])
    note("TOTAL events written: %d" % count)
    if count == 0:
        note("ERROR no events produced, refusing to overwrite the calendar")
        write_diagnostics()
        return 1
    if shrink_guard_trips(count):
        write_diagnostics()
        return 1
    save_feed_counts(count)
    check_notes(body)
    run_checks()

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
    write_pending()
    write_notify()
    write_diagnostics()
    return 0


def write_pending():
    """Publish the list of finished matches whose score is still hidden."""
    pending.sort(key=lambda item: item["date"], reverse=True)
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(cfg.OUTPUT_DIR, "pending.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "warnings": check_warnings(),
                   "pending": pending[:cfg.PENDING_LIMIT]},
                  handle, ensure_ascii=False, indent=1)
    unscored = sum(1 for item in pending if not item.get("scored", True))
    note("PENDING %d finished matches waiting to be ticked off, %d of them "
         "still waiting for a score from the feed" % (len(pending), unscored))
    print("Wrote %s" % path)


def write_notify():
    """One short block of text for the daily reminder shortcut to read.

    The shortcut fires when this file does NOT contain the word "none", so
    "none" is what a quiet, healthy night writes. A failed check is put above
    the match list, and on a quiet night it replaces "none" entirely, which
    makes the notification fire for the fault alone. That is why no warning
    may contain the letters "none" (add_check guards it)."""
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(cfg.OUTPUT_DIR, "notify.txt")

    warnings = check_warnings()
    if not pending:
        text = "\n".join(warnings) if warnings else "none"
    else:
        titles = [item["title"] for item in pending[:cfg.NOTIFY_LIMIT]]
        more = len(pending) - len(titles)
        text = "%d match%s to unlock\n%s" % (
            len(pending), "" if len(pending) == 1 else "es",
            "\n".join(titles))
        if more > 0:
            text += "\nand %d more" % more
        if warnings:
            text = "\n".join(warnings) + "\n" + text

    # Last line of defence: a warning that slipped the word through would
    # silence the notification for everything, not just itself.
    if warnings and "none" in text.lower():
        note("CHECK a warning contained the word that silences the "
             "notification, and was reworded to keep the 9pm ping alive")
        text = re.sub("(?i)none", "zero", text)

    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    print("Wrote %s" % path)


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
