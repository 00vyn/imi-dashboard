# Investment Management Intelligence Hub

A personal, $0 investment-management research tool for a Mauritius-based
finance student. Static site on GitHub Pages, data collected by GitHub
Actions, no paid APIs, no accounts, no backend server.

Live site: **https://00vyn.github.io/imi-dashboard/**

This file is the plain-English guide: what the three pages do, how the
automatic updates work, how to back up your private data, and how to check
that everything still works after you change something.

## The three pages

**`index.html` — the public dashboard.** Market values, exchange rates,
and news, automatically collected, categorised, and scored every weekday.
Nothing here is private; it's all generated JSON, safe to look at from any
device or browser.

**`research.html` — your private workspace.** Research queue, notes,
reading list, company research, and the report builder. Everything here
lives only in this browser's IndexedDB — it is never committed to the
repo, never sent anywhere, and does not sync between devices or browsers
on its own. See **Backing up your private data** below; this is the part
of the project you're responsible for keeping safe.

**`progress.html` — your progress dashboard.** Counts and coverage
computed from what's in your private workspace, plus a weekly research
challenge pulled from the public dashboard. No streaks, no points — just
what you've actually written.

All three pages link to each other from the navigation bar at the top.

## How the automatic updates work

A GitHub Action (`.github/workflows/ingest.yml`) runs automatically on
**weekdays at 04:17 UTC**. Each run:

1. Installs dependencies and runs the Python test suite. If any test
   fails, the run stops here — nothing gets collected or committed.
2. Collects the approved sources (`ingest/sources.yml`) into
   `docs/data/latest.json` and `docs/data/status.json`, skipping anything
   `robots.txt` disallows and reporting honestly on anything that failed.
3. Builds the intelligence layer (`ingest/intelligence.py`) into
   `docs/data/intelligence.json` — categorised, scored, deduplicated,
   bounded to 30 days or 400 items.
4. Commits whatever changed back to the repo.

You can also trigger it manually: **Actions tab → "update dashboard
data" → Run workflow**. Useful right after you've changed a source or
just want fresh data without waiting for the schedule.

A second workflow, `.github/workflows/probe.yml`, is the Step 1 source
probe — run it by hand whenever you're considering adding a new source,
before promoting it into `ingest/sources.yml`. It checks `robots.txt`,
tries feed autodiscovery, and writes `docs/PROBE_REPORT.md`.

## Backing up your private data

This is the one part of the project that isn't automatically safe.

The **public** data (`docs/data/*.json`) lives in git, so it's backed up
by GitHub the same way any commit is — you can always see or restore an
old version from the commit history.

Your **private** data (queue, notes, reading list, companies, reports,
skills) lives only in IndexedDB, in one specific browser, on one specific
device. If you clear that browser's site data, switch browsers, switch
computers, or reinstall your OS, that data is gone unless you exported it
first. Nothing about GitHub Pages or the repo protects it.

The fix is the **Export JSON** button in the research workspace's
toolbar. It downloads everything — queue, notes, reading list, companies,
reports, skills — as one JSON file. Recommended habit: export it
somewhere you already trust (your own cloud drive, a local backup folder,
wherever you'd keep any other personal file) every few weeks, and
definitely right before you know you'll be on a different browser or
machine. **Import JSON** on the same page loads a file back in, replacing
what's currently there — it'll ask you to confirm first, since that's a
one-way, whole-store overwrite. Note it's not merge-by-default: bring an
export from your only backup, not from a device you're about to keep
using too, or you'll lose whatever you'd added there since the last
export.

## Checking everything still works

Since there's no build step and no automated browser testing checked
into the repo (that would mean adding npm/Node infrastructure to what is
otherwise a plain HTML/CSS/JS project — a bigger change than this project
asked for), the way to confirm a change didn't break anything is to
actually click through it. This checklist covers every moving part:

**Public dashboard (`index.html`)**
- [ ] Hard-refresh. Source-health badges and the five intelligence
      sections populate (or show an honest empty state, not an error).
- [ ] Click "Why this matters" on a card — it expands with all four
      fields filled in.
- [ ] Click "+ Add to queue" on a card — it flips to "Added ✓".

**Research workspace (`research.html`)**
- [ ] Add one item in each tab (queue, notes, reading, companies,
      reports). Each appears as a card.
- [ ] Edit one of them — the form pre-fills, "Save changes" updates it
      in place rather than creating a duplicate.
- [ ] Search and filter on the queue and reading-list tabs narrow the
      list correctly; clearing them brings everything back.
- [ ] Export CSV from the queue tab downloads a file with a header row
      and one row per item.
- [ ] Link a report to a company via the dropdown; delete the company;
      confirm the report survives (no cascading delete) and the dropdown
      drops the stale option.
- [ ] Export JSON, then Import that same file back in — confirm the
      confirmation prompt appears and your data is unchanged afterward.

**Progress (`progress.html`)**
- [ ] Skills list shows five CFA-gap entries the first time you visit.
- [ ] Change a skill's proficiency, add a custom one, delete one —
      each persists after a refresh.
- [ ] The weekly challenge names a real story with a working link.
      Click "Start report" — a draft appears in the Report Builder tab
      with the title and source pre-filled.
- [ ] Add a queue item and mark it done — the follow-through bar and
      the research-activity counts move accordingly.

**Data pipeline**
- [ ] Run the "update dashboard data" workflow manually from the Actions
      tab. All steps go green, including "Run tests".
- [ ] The commit it produces touches `latest.json`, `status.json`, and
      `intelligence.json`.

If something on this list breaks after a change, that's the signal to
look closer before it ships — not a guarantee nothing else moved, but it
covers every distinct piece of interactive behaviour in the project.

## Project status

Phases 1 through 9 of the original build plan are complete: source
collection and probing, the intelligence layer (categorisation, scoring,
importance, learning prompts), the research workspace (queue, notes,
reading list, company research, report builder), search/filters/CSV
export, the progress dashboard (CFA gap coverage, skills, weekly
challenge), and this refinement pass (shared navigation and stylesheet,
this guide, the checklist above).

Known limitations, left as-is on purpose rather than silently:
- The two GlobeNewswire sources were checked for content and volume via
  a live fetch, not through `probe.py`'s own `robots.txt` check — run
  `python ingest/probe.py` for the formal verdict. `collect.py` still
  gates every fetch on `robots.txt` at collection time regardless.
- CFA gap coverage on the progress page only scans text you've actually
  written (queue, notes, company research, reports) — reading-list items
  and empty report drafts don't contribute, so it undercounts anything
  you're studying outside this tool.
- The skills list reseeds its five CFA-gap defaults if you ever delete
  all of them and reload — there's no way to distinguish "never seeded"
  from "cleared on purpose" without more schema than this warranted.
