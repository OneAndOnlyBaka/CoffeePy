---
name: user-manual
description: Keeps CoffeePy's user manual (docs/MANUAL.de.md + docs/MANUAL.en.md) and its screenshots in step with the product. Use whenever a change touches anything a user sees or does - a kiosk/dashboard/portal screen or its wording, a translation, an installer or provisioning-tool option, a setting in .env, the update or backup flow - and before reporting such a change as done.
---

# The user manual

`docs/MANUAL.de.md` and `docs/MANUAL.en.md` are the manual for the people who set up and use
CoffeePy - not for developers (that is docs/ and CLAUDE.md). They have the same structure,
section for section, and every screen of the product appears in both as a screenshot in that
language under `docs/pictures/manual/<de|en>/<name>.png`. `docs/MANUAL.md` only links the two.

**A change that a user can see is not finished until the manual matches it.** That means, in
the same piece of work:

1. The text in **both** languages - same section, same facts. German addresses the reader
   with "du", like the German UI (locale/de).
2. The **screenshots** of every screen the change affects, in both languages - retaken, not
   edited by hand.
3. If a screen, setting, page or step is new: a place for it in both manuals, and in
   `capture_web.py` / `capture_tui.sh` so the next retake includes it.

Say in the report which manual sections and screenshots were updated - or why none were.

**Unless the user defers it.** When they say to build something first and update the docs and
screenshots only after they've checked it ("erst anschauen, dann Bilder und Doku"), that order
wins over the rule above, until they explicitly approve:

- Do not run `capture.sh` at all - not "just to look", not into a temp dir with a restore
  afterwards. It rewrites `docs/pictures/manual/` and toggles the kiosk's debug port and demo
  data on the device they are about to look at.
- Do not touch the manuals or the capture scripts either.
- To see the result yourself: deploy, then `pi-device`'s `screenshot.sh` of what the touchscreen
  shows (or a headless browser writing to the scratchpad). Then hand it over and wait.
- In the report, list the manual sections and screenshots that will need updating once approved.

## What lives where

| Change in ... | Manual section | Screenshots |
|---|---|---|
| `kiosk/templates/`, `static/kiosk/`, `kiosk/views.py` texts | 4. Kiosk | `kiosk-*` |
| `portal/` | 5. Phone portal | `portal-*` |
| `dashboard/` | 6. Dashboard (and 3. First steps for login/setup) | `dashboard-*` |
| `scripts/provision.sh`, `.env.example`, `install/install.sh` options | 2. Installation (the settings table!) | `provision-*` |
| update watcher, backups, `install/maintenance.sh`, certificates | 7. Running and maintaining, 8. Troubleshooting | `kiosk-maintenance`, `dashboard-update/backup/certificate` |
| `locale/de/LC_MESSAGES/django.po` (wording) | wherever the text is quoted | the screens showing it |
| hardware, housing | 1. What you need | - |

The manuals quote button and screen names exactly as the UI shows them (bold or *italic*):
when a label changes in the .po file or a template, search both manuals for the old one.

## Retaking screenshots

Everything goes through the test Pi (pi-device skill) and is scripted - never paste a screenshot
by hand, the next retake would silently drop it.

```bash
python3 -m venv /tmp/manual && /tmp/manual/bin/pip install pillow   # once
export MANUAL_PYTHON=/tmp/manual/bin/python
.claude/skills/user-manual/scripts/capture.sh --shots kiosk-settings,dashboard-members   # just these, both languages
.claude/skills/user-manual/scripts/capture.sh --only kiosk,portal    # whole areas: kiosk,dashboard,portal,provision
.claude/skills/user-manual/scripts/capture.sh                        # everything, ~15 min - rarely needed
```

**Work out which images a change touches before capturing anything** - the user asked for
it: a full run costs a quarter of an hour of the device, most changes touch two or three
images. List them first (the table in "What lives where", the `![...](pictures/manual/de/...)`
next to every manual passage you changed, and every screen whose template, CSS or texts you
touched - a shared partial or kiosk.css counts for all screens using it), say which ones and
why, then run `--shots` with exactly those. Only a change to something every screen shares
(base CSS, the nav, the corner buttons) justifies `--only` or a full run.

- Deploy the change to the Pi first (`pi-device` deploy.sh) - the screenshots show what runs there.
- The **kiosk** is captured on the Pi, in its own Chromium. **Dashboard and portal** are
  captured from this machine, in a throwaway headless Chromium profile (`chromium-browser`, one
  per language so the browser's own widgets - "Choose File" - match), pointed at the Pi's
  address; the touchscreen is left alone for them.
- `capture.sh` turns the kiosk's debug port on, seeds demo data (`demo_data.py`: Anna, Ben, ...;
  David owes money for the pillory), captures both languages, and **always** removes the demo
  data and turns the port off again, also when a step fails. Check the Pi afterwards anyway:
  no "Anna" in the dashboard's members, kiosk back on its start screen.
- Kiosk screens are 1024×600 (the device), dashboard 1280×800, portal a 390×844 phone at 2×,
  the provisioning tool a 100×30 terminal. PNGs are reduced to 256 colours at the end.
- A new screen: add a step to `capture_web.py` (kiosk/dashboard/portal) or `capture_tui.sh`
  (provisioning tool), named `<area>-<what>` like the others, and reference it in both manuals.
- After a retake, **look at** the changed images before committing - put them side by side in a
  contact sheet per area and language and Read that. A capture that ran into a timing problem
  shows a half-loaded page, not an error; and the German screenshots are where untranslated
  text and German labels too long for their box show up (both happened on the first run:
  the logout countdown, the portal's stat tiles). `coffee/tests/test_translations.py` now
  catches unmarked template text, not overflow.

## Real data never ends up in a screenshot

The test Pi may hold **real member data** next to the demo members (the user imports real
databases onto it). Nothing personal may reach `docs/pictures/` - nicknames, member uids, card
ids. This is automatic, not something to ask about:

- `capture_web.py` blurs it: before every dashboard shot and the kiosk's ranking shots it runs
  `BLUR_JS`, which blurs every element holding a number of six or more digits - except the demo
  cards' uids (`demo_uids()`), so a member list still shows what it is about - or the nickname of
  a member that isn't one of `demo_data.py`'s (`private_names()`). **A new capture step that
  can show members** - a new dashboard page, a kiosk screen with names, the portal - **calls
  `b.js(blur)` before `b.shot(...)`** like the others; a new demo member goes into the exclusion
  list in `private_names()` as well as into `demo_data.py`.
- Before anything is shown to the user or committed, **look at every retaken image** that can
  contain members (contact sheet, see above): only the demo names (Anna B., Ben K., Clara M., David S.,
  Emma W., Felix H.) may be legible, no uid or card number at all. If something slipped through, fix
  the blur and retake - never edit the image by hand, the next retake would bring it back.
- The same goes for any other screenshot of the device: one taken to show the user something
  (pi-device `screenshot.sh`, a headless browser) stays in the scratchpad, and if it goes
  anywhere else, blur first.
- Aggregates (cups, turnover, balances without a name), coffee sort names, the admin's own
  user name and IP addresses on the local network are fine.

## Traps

- **Demo scans log the real kiosk in** - that is how the kiosk screens get a logged-in member.
  `capture_web.py` logs out with a nav tap before the next step; the cleanup must only run once
  that has happened (capture.sh does it in that order).
- The provisioning tool's progress and final screens are drawn by sourcing
  `scripts/provision.sh` with example values (`summary_text`, `ui_progress`) - no real install,
  no real password in a picture. Keep provision.sh sourceable (its `main` only runs when executed).
- A modal opened by a click can miss when a 500ms panel poll replaces the button at that moment
  (`open_modal` retries) - the same race applies to any new click-to-open step.
