# Sports calendar

A single calendar of your teams' fixtures, rebuilt automatically four times a
day and published to a web address your Apple devices subscribe to.

Cost: $0 per month.  No API keys, no accounts beyond GitHub, no card.

---

## What each file does

| File | What it is |
| --- | --- |
| `leagues.py` | The settings.  Teams, leagues, emoji, event lengths.  This is the only file you will ever edit. |
| `generate.py` | The engine.  Downloads the fixtures and writes the calendar file. |
| `.github/workflows/build.yml` | The timer.  Tells GitHub to run the engine four times a day and publish the result. |

Two files are created when it runs, inside a `docs` folder:

- `docs/calendar.ics` is the calendar itself.
- `docs/diagnostics.txt` is a plain-text report of anything that looked odd.

---

## Step 1: put the files into your repository

The easiest way, no command line needed.

1. Go to `github.com/2dwb9p9kdj-ship-it/sports-calendar`.
2. Click **Add file**, then **Upload files**.
3. Drag `generate.py`, `leagues.py` and `README.md` onto the page.
4. Scroll down and click **Commit changes**.

The workflow file lives in a folder, so it needs the other method:

5. Click **Add file**, then **Create new file**.
6. In the filename box, type exactly: `.github/workflows/build.yml`
   As you type each `/`, GitHub turns it into a folder.  That is correct.
7. Paste in the contents of `build.yml`.
8. Click **Commit changes**.

---

## Step 2: run it for the first time

1. Click the **Actions** tab at the top of the repository.
2. If GitHub asks you to enable workflows, click the green button to enable.
3. In the left sidebar click **Build calendar**.
4. Click **Run workflow**, then the green **Run workflow** button.
5. Wait about a minute and refresh.  A green tick means it worked.

If you get a red cross, click into the run and copy the red error text.  That
text is what I need to fix it.

---

## Step 3: find your calendar address

After a successful run, your calendar lives at:

```
https://2dwb9p9kdj-ship-it.github.io/sports-calendar/calendar.ics
```

Open that address in a browser.  You should get a download or a page of text
starting with `BEGIN:VCALENDAR`.  Either is fine, it means the file is there.

---

## Step 4: subscribe on your Mac, so it reaches every device

Doing this on the Mac (rather than the iPhone) is what makes it sync
everywhere through iCloud.

1. Open the **Calendar** app on your Mac.
2. Menu bar: **File**, then **New Calendar Subscription**.
3. Paste the address from step 3.  Click **Subscribe**.
4. In the box that appears:
   - **Name**: Sport
   - **Location**: **iCloud**.  This is the important one.  If you leave it as
     "On My Mac" it will not appear on your iPhone or iPad.
   - **Auto-refresh**: Every hour
   - **Remove**: Alerts (tick it, you said no alerts)
5. Click **OK**.

The calendar now appears on every device signed into the same iCloud account,
usually within a few minutes.

---

## Changing things later

Want to add a team, drop a league, or roll over to next season?  Edit
`leagues.py` on GitHub (click the file, then the pencil icon), commit, and the
calendar rebuilds itself.  Nothing else to do.

---

## What this version does not do yet

- **No scores.**  Phase 1 is deliberately spoiler-safe.  The switch that turns
  scores on is `INCLUDE_SCORES` at the bottom of `leagues.py`, but do not turn
  it on yet: it would add scores to every finished match at once with no way to
  hide the ones you have not watched.
- **No score breakdowns in the notes.**  Half-time and quarter-by-quarter
  scores come from different sources per sport, which is phase 2.
- **No unlock page.**  That is phase 3.
- **Fixed event lengths.**  Events block out the typical length of the sport
  rather than the real finish time, because the fixture feeds do not publish
  when a match actually ended.
