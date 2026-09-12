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
import hashlib
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
NBA_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
NHL_URL = "https://api-web.nhle.com/v1/club-schedule-season/{code}/{season}"
JOLPICA_URL = "https://api.jolpi.ca/ergast/f1/{year}.json?limit=100"
USER_AGENT = "sports-calendar/1.1 (personal calendar generator)"
TIMEOUT = 45

diagnostics = []
seen_events = {}
watched = set()
pending = []


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


def add_pending(uid, title, start, competition):
    pending.append({"id": short_id(uid), "uid": uid, "title": title,
                    "date": start.strftime("%Y-%m-%d %H:%M") + " UTC",
                    "competition": competition})


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

        number = row.get("matchnumber", index)
        uid = "%s-%s@sports-calendar" % (slugs[0], number)
        home_score, away_score = parse_scores(row)
        plain_home, plain_away = home_label, away_label
        if cfg.INCLUDE_SCORES and home_score is not None:
            if is_watched(uid):
                home_label += " [%s]" % home_score
                away_label += " [%s]" % away_score
            else:
                add_pending(uid, "%s v %s" % (plain_home, plain_away), start,
                            league.get("competition", ""))

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

    unmapped = sorted(n for n in seen_names if n not in league.get("names", {}))
    if unmapped:
        diagnostics.append("FEED NAMES %s: %s" % (slugs[0], " | ".join(unmapped)))
    print("  %s: %d events kept" % (slugs[0], count))
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


def build_nhl_events(entry):
    url = NHL_URL.format(code=entry["team_code"], season=entry["season"])
    payload = fetch_json(url)
    events, count = [], 0

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
        title = "%s %s @ %s" % (cfg.SPORTS[entry["sport"]]["emoji"],
                                team_label(pseudo, away),
                                team_label(pseudo, home))
        remember_event(start, team_label(pseudo, home),
                       team_label(pseudo, away))
        notes = "%s\n%s" % (entry["competition"], stage)
        events.extend(make_event("nhl-%s@sports-calendar" % game.get("id"), title,
                                 start, cfg.SPORTS[entry["sport"]]["minutes"],
                                 venue, notes))
        count += 1
    return events, count


def build_official_events():
    """NBA and NHL straight from the leagues, so preseason and playoffs appear."""
    builders = {"nba": build_nba_events, "nhl": build_nhl_events}
    events = []

    for entry in getattr(cfg, "OFFICIAL_SOURCES", []):
        name = entry["source"]
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


def build_ics_events():
    """Fixtures pulled from published team calendars."""
    events = []
    earliest = parse_utc(getattr(cfg, "ICS_EARLIEST", "2000-01-01") + " 00:00")

    for source in getattr(cfg, "ICS_SOURCES", []):
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
        wanted = source.get("team", "").lower()
        names = source.get("names", {})
        skip_words = source.get("skip_if_contains", [])
        kept = duplicates = skipped = unparsed = 0
        seen_tags = set()
        summary = start = uid = location = None
        description = categories = None

        for line in _unfold_ics(text):
            if line.startswith("BEGIN:VEVENT"):
                summary = start = uid = location = None
                description = categories = None
            elif line.startswith("DESCRIPTION:"):
                description = line[12:]
            elif line.startswith("CATEGORIES:"):
                categories = line[11:]
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

                uid = "ics-%s@sports-calendar" % (uid or start.isoformat())
                if cfg.INCLUDE_SCORES and score:
                    if is_watched(uid):
                        if source.get("away_first"):
                            away_label += " [%s]" % score[0]
                            home_label += " [%s]" % score[1]
                        else:
                            home_label += " [%s]" % score[0]
                            away_label += " [%s]" % score[1]
                    else:
                        add_pending(uid, "%s v %s" % (home_label, away_label),
                                    start, source.get("name", ""))

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
                events.extend(make_event(uid, title, start, sport["minutes"],
                                         location, competition))
                kept += 1
                if kept <= 3:
                    note("ICS %s sample: %s" % (source.get("name", "?"), title))

        note("ICS %s: kept %d, %d duplicates dropped, %d filtered out, "
             "%d unreadable | tags: %s"
             % (source.get("name", "?"), kept, duplicates, skipped, unparsed,
                ", ".join(sorted(seen_tags)) or "none"))
        print("  ics %s: %d events" % (source.get("name", "?"), kept))

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
    watched.update(load_watched())
    note("WATCHED %d matches already seen" % len(watched))
    body = []
    for league in cfg.LEAGUES:
        body.extend(build_league_events(league))
    body.extend(build_official_events())
    body.extend(build_ics_events())
    body.extend(build_manual_events())
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
    write_pending()
    write_diagnostics()
    return 0


def write_pending():
    """Publish the list of finished matches whose score is still hidden."""
    pending.sort(key=lambda item: item["date"], reverse=True)
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(cfg.OUTPUT_DIR, "pending.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"generated": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "pending": pending[:cfg.PENDING_LIMIT]},
                  handle, ensure_ascii=False, indent=1)
    note("PENDING %d finished matches with the score still hidden" % len(pending))
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
