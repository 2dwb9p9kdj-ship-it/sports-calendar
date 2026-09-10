"""
Configuration for the sports calendar generator.

Everything you might want to change lives in this file. generate.py reads it
and never needs editing for a new team or a new season.

HOW A SEASON ROLLS OVER
-----------------------
fixturedownload uses a slug per season, e.g. "epl-2026" for 2026/27.
When a new season starts, change the "slug" and "season" values here only.

TEAM NAMES
----------
"follow" holds the names EXACTLY as the data feed writes them (often short,
e.g. "Broncos"). "names" maps a feed name to the name shown in the calendar.
If a feed name is not in "names", the feed name is used as-is and the run
writes it to docs/diagnostics.txt so it can be corrected.
"""

# ---------------------------------------------------------------------------
# Sports: emoji, title format, and how long an event blocks out (minutes)
# ---------------------------------------------------------------------------
SPORTS = {
    "football":          {"emoji": "\u26bd\ufe0f", "format": "vs", "minutes": 120},
    "afl":               {"emoji": "\U0001f3c9", "format": "vs", "minutes": 165},
    "rugby_league":      {"emoji": "\U0001f3c9", "format": "vs", "minutes": 110},
    "rugby_union":       {"emoji": "\U0001f3c9", "format": "vs", "minutes": 120},
    "basketball_us":     {"emoji": "\U0001f3c0", "format": "@",  "minutes": 150},
    "basketball_au":     {"emoji": "\U0001f3c0", "format": "vs", "minutes": 120},
    "baseball":          {"emoji": "\u26be\ufe0f", "format": "@",  "minutes": 180},
    "ice_hockey":        {"emoji": "\U0001f3d2", "format": "@",  "minutes": 150},
    "american_football": {"emoji": "\U0001f3c8", "format": "@",  "minutes": 195},
    "cricket_t20":       {"emoji": "\U0001f3cf", "format": "vs", "minutes": 210},
    "cricket_odi":       {"emoji": "\U0001f3cf", "format": "vs", "minutes": 480},
    "cricket_test":      {"emoji": "\U0001f3cf", "format": "vs", "minutes": 420},
    "f1":                {"emoji": "\U0001f3ce\ufe0f", "format": None, "minutes": None},
}

# ---------------------------------------------------------------------------
# Leagues fed by fixturedownload.com (free, no API key)
# ---------------------------------------------------------------------------
# gender:        "M" or "W". Appended in brackets after every team name.
# international: True means use country flags and Australian nicknames.
# follow:        feed team names to keep. Empty list = keep every match.
# competition:   line 1 of the calendar notes.
# stage:         line 2. "{round}" is replaced by the feed's round label.
# ---------------------------------------------------------------------------
LEAGUES = [
    # ---- Football, Chelsea -------------------------------------------------
    {"slug": "epl-2026", "sport": "football", "gender": "M",
     "follow": ["Chelsea"], "names": {},
     "competition": "Premier League", "stage": "2026/27 Regular Season, {round}"},

    {"slug": "wsl-2026", "sport": "football", "gender": "W",
     "follow": ["Chelsea"], "names": {},
     "competition": "Women's Super League", "stage": "2026/27 Regular Season, {round}"},

    {"slug": "champions-league-2026", "sport": "football", "gender": "M",
     "follow": ["Chelsea"], "names": {},
     "competition": "UEFA Champions League", "stage": "2026/27 Season, {round}"},

    {"slug": "europa-league-2026", "sport": "football", "gender": "M",
     "follow": ["Chelsea"], "names": {},
     "competition": "UEFA Europa League", "stage": "2026/27 Season, {round}"},

    {"slug": "conference-league-2026", "sport": "football", "gender": "M",
     "follow": ["Chelsea"], "names": {},
     "competition": "UEFA Conference League", "stage": "2026/27 Season, {round}"},

    # ---- Football, Perth Glory --------------------------------------------
    {"slug": "aleague-men-2026", "sport": "football", "gender": "M",
     "follow": ["Perth Glory", "Perth"], "names": {"Perth": "Perth Glory"},
     "competition": "A-League Men", "stage": "2026/27 Regular Season, {round}"},

    {"slug": "aleague-women-2026", "sport": "football", "gender": "W",
     "follow": ["Perth Glory", "Perth"], "names": {"Perth": "Perth Glory"},
     "competition": "A-League Women", "stage": "2026/27 Regular Season, {round}"},

    {"slug": "australia-cup-2026", "sport": "football", "gender": "M",
     "follow": ["Perth Glory", "Perth"], "names": {"Perth": "Perth Glory"},
     "competition": "Australia Cup", "stage": "2026 {round}"},

    # ---- Australian rules, Essendon ---------------------------------------
    {"slug": "afl-2026", "sport": "afl", "gender": "M",
     "follow": ["Essendon"], "names": {},
     "competition": "Australian Football League", "stage": "2026 Season, {round}"},

    {"slug": "aflw-2026", "sport": "afl", "gender": "W",
     "follow": ["Essendon"], "names": {},
     "competition": "AFL Women's", "stage": "2026 Season, {round}"},

    # ---- Rugby league, Brisbane Broncos -----------------------------------
    {"slug": "nrl-2026", "sport": "rugby_league", "gender": "M",
     "follow": ["Broncos"], "names": {"Broncos": "Brisbane Broncos"},
     "competition": "National Rugby League", "stage": "2026 Season, {round}"},

    {"slug": "nrlw-2026", "sport": "rugby_league", "gender": "W",
     "follow": ["Broncos"], "names": {"Broncos": "Brisbane Broncos"},
     "competition": "NRL Women's Premiership", "stage": "2026 Season, {round}"},

    # ---- Rugby union, Western Force ---------------------------------------
    {"slug": "super-rugby-pacific-2026", "sport": "rugby_union", "gender": "M",
     "follow": ["Western Force", "Force"], "names": {"Force": "Western Force"},
     "competition": "Super Rugby Pacific", "stage": "2026 Season, {round}"},

    # ---- Basketball --------------------------------------------------------
    {"slug": "nba-2026", "sport": "basketball_us", "gender": "M",
     "follow": ["Denver Nuggets"], "names": {},
     "competition": "National Basketball Association",
     "stage": "2026/27 Regular Season"},

    {"slug": "nbl-2026", "sport": "basketball_au", "gender": "M",
     "follow": ["Perth Wildcats", "Perth"], "names": {"Perth": "Perth Wildcats"},
     "competition": "National Basketball League", "stage": "2026/27 Season, {round}"},

    {"slug": "wnbl-2026", "sport": "basketball_au", "gender": "W",
     "follow": ["Perth Lynx", "Perth"], "names": {"Perth": "Perth Lynx"},
     "competition": "Women's National Basketball League",
     "stage": "2026/27 Season, {round}"},

    # ---- Baseball, ice hockey, American football --------------------------
    {"slug": "mlb-2026", "sport": "baseball", "gender": "M",
     "follow": ["Colorado Rockies"], "names": {},
     "competition": "Major League Baseball", "stage": "2026 Regular Season"},

    {"slug": "nhl-2026", "sport": "ice_hockey", "gender": "M",
     "follow": ["Colorado Avalanche"], "names": {},
     "competition": "National Hockey League", "stage": "2026/27 Regular Season"},

    {"slug": "nfl-2026", "sport": "american_football", "gender": "M",
     "follow": ["Denver Broncos"], "names": {},
     "competition": "National Football League", "stage": "2026 Season, {round}"},

    # ---- Cricket, Perth Scorchers -----------------------------------------
    {"slug": "bbl-2026", "sport": "cricket_t20", "gender": "M",
     "follow": ["Perth Scorchers", "Scorchers"],
     "names": {"Scorchers": "Perth Scorchers"},
     "competition": "Big Bash League", "stage": "2026/27 Season, {round}"},

    {"slug": "wbbl-2026", "sport": "cricket_t20", "gender": "W",
     "follow": ["Perth Scorchers", "Scorchers"],
     "names": {"Scorchers": "Perth Scorchers"},
     "competition": "Women's Big Bash League", "stage": "2026 Season, {round}"},

    # ---- Australian national teams, tournaments only -----------------------
    {"slug": "rugby-league-world-cup-2026", "sport": "rugby_league", "gender": "M",
     "international": True, "follow": ["Australia"], "names": {},
     "competition": "Rugby League World Cup 2026", "stage": "{round}"},

    {"slug": "fifa-world-cup-2026", "sport": "football", "gender": "M",
     "international": True, "follow": ["Australia"], "names": {},
     "competition": "FIFA World Cup 2026", "stage": "{round}"},

    {"slug": "mens-t20-world-cup-2026", "sport": "cricket_t20", "gender": "M",
     "international": True, "follow": ["Australia"], "names": {},
     "competition": "ICC Men's T20 World Cup 2026", "stage": "{round}"},
]

# ---------------------------------------------------------------------------
# Formula 1 (Jolpica API, free, no key)
# ---------------------------------------------------------------------------
F1_SEASON = 2026
F1_COMPETITION = "2026 Formula 1 World Championship"
F1_INCLUDE_PRACTICE = True

# Session key in the API -> (title text, minutes)
F1_SESSIONS = {
    "FirstPractice":  ("Practice 1", 60),
    "SecondPractice": ("Practice 2", 60),
    "ThirdPractice":  ("Practice 3", 60),
    "SprintQualifying": ("Sprint Qualifying", 45),
    "SprintShootout": ("Sprint Qualifying", 45),
    "Sprint":         ("Sprint", 30),
    "Qualifying":     ("Qualifying", 60),
    "Race":           ("Grand Prix", 120),
}

# ---------------------------------------------------------------------------
# International teams: flags and Australian nicknames
# ---------------------------------------------------------------------------
AUS_NICKNAMES = {
    ("football", "M"): "Socceroos",
    ("football", "W"): "Matildas",
    ("rugby_union", "M"): "Wallabies",
    ("rugby_union", "W"): "Wallaroos",
    ("rugby_league", "M"): "Kangaroos",
    ("rugby_league", "W"): "Jillaroos",
    ("basketball_us", "M"): "Boomers",
    ("basketball_us", "W"): "Opals",
    ("basketball_au", "M"): "Boomers",
    ("basketball_au", "W"): "Opals",
    # Cricket has no nickname, so it falls back to (M) / (W).
}

COUNTRY_FLAGS = {
    "Australia": "\U0001f1e6\U0001f1fa", "China": "\U0001f1e8\U0001f1f3",
    "Japan": "\U0001f1ef\U0001f1f5", "South Korea": "\U0001f1f0\U0001f1f7",
    "New Zealand": "\U0001f1f3\U0001f1ff", "England": "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    "Scotland": "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",
    "Wales": "\U0001f3f4\U000e0067\U000e0062\U000e0077\U000e006c\U000e0073\U000e007f",
    "Ireland": "\U0001f1ee\U0001f1ea", "France": "\U0001f1eb\U0001f1f7",
    "Italy": "\U0001f1ee\U0001f1f9", "Spain": "\U0001f1ea\U0001f1f8",
    "Germany": "\U0001f1e9\U0001f1ea", "Portugal": "\U0001f1f5\U0001f1f9",
    "Netherlands": "\U0001f1f3\U0001f1f1", "Belgium": "\U0001f1e7\U0001f1ea",
    "Argentina": "\U0001f1e6\U0001f1f7", "Brazil": "\U0001f1e7\U0001f1f7",
    "United States": "\U0001f1fa\U0001f1f8", "USA": "\U0001f1fa\U0001f1f8",
    "Canada": "\U0001f1e8\U0001f1e6", "Mexico": "\U0001f1f2\U0001f1fd",
    "India": "\U0001f1ee\U0001f1f3", "Pakistan": "\U0001f1f5\U0001f1f0",
    "South Africa": "\U0001f1ff\U0001f1e6", "Sri Lanka": "\U0001f1f1\U0001f1f0",
    "Bangladesh": "\U0001f1e7\U0001f1e9", "Afghanistan": "\U0001f1e6\U0001f1eb",
    "West Indies": "\U0001f3f4", "Zimbabwe": "\U0001f1ff\U0001f1fc",
    "Fiji": "\U0001f1eb\U0001f1ef", "Samoa": "\U0001f1fc\U0001f1f8",
    "Tonga": "\U0001f1f9\U0001f1f4", "Papua New Guinea": "\U0001f1f5\U0001f1ec",
    "Saudi Arabia": "\U0001f1f8\U0001f1e6", "Qatar": "\U0001f1f6\U0001f1e6",
    "Iran": "\U0001f1ee\U0001f1f7", "Iraq": "\U0001f1ee\U0001f1f6",
    "Vietnam": "\U0001f1fb\U0001f1f3", "Thailand": "\U0001f1f9\U0001f1ed",
    "Philippines": "\U0001f1f5\U0001f1ed", "Indonesia": "\U0001f1ee\U0001f1e9",
    "Uzbekistan": "\U0001f1fa\U0001f1ff", "Jordan": "\U0001f1ef\U0001f1f4",
    "Chinese Taipei": "\U0001f1f9\U0001f1fc", "North Korea": "\U0001f1f0\U0001f1f5",
    "Norway": "\U0001f1f3\U0001f1f4", "Sweden": "\U0001f1f8\U0001f1ea",
    "Denmark": "\U0001f1e9\U0001f1f0", "Switzerland": "\U0001f1e8\U0001f1ed",
    "Croatia": "\U0001f1ed\U0001f1f7", "Poland": "\U0001f1f5\U0001f1f1",
    "Morocco": "\U0001f1f2\U0001f1e6", "Senegal": "\U0001f1f8\U0001f1f3",
    "Nigeria": "\U0001f1f3\U0001f1ec", "Ghana": "\U0001f1ec\U0001f1ed",
    "Egypt": "\U0001f1ea\U0001f1ec", "Uruguay": "\U0001f1fa\U0001f1fe",
    "Colombia": "\U0001f1e8\U0001f1f4", "Chile": "\U0001f1e8\U0001f1f1",
}

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
CALENDAR_NAME = "Sport"
OUTPUT_DIR = "docs"
OUTPUT_FILE = "calendar.ics"

# Phase 1 is spoiler-safe: no scores anywhere. Phase 2 flips this on once the
# unlock page exists.
INCLUDE_SCORES = False
