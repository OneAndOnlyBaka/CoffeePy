---
name: user-manual
description: Keeps CoffeePy's user manual (docs/MANUAL.de.md + docs/MANUAL.en.md) and its screenshots in step with the product. Use whenever a change touches anything a user sees or does - a kiosk/dashboard/portal screen or its wording, a translation, an installer or provisioning-tool option, a setting in .env, the update or backup flow - and before reporting such a change as done.
---

# The user manual

`docs/MANUAL.de.md` and `docs/MANUAL.en.md` are the manual for the people who set up and use
CoffeePy - not for developers (that is the README and CLAUDE.md). They have the same structure,
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
.claude/skills/user-manual/scripts/capture.sh                        # everything, ~15 min
.claude/skills/user-manual/scripts/capture.sh --only kiosk,portal    # any of kiosk,dashboard,portal,provision
```

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

## Traps

- **Demo scans log the real kiosk in** - that is how the kiosk screens get a logged-in member.
  `capture_web.py` logs out with a nav tap before the next step; the cleanup must only run once
  that has happened (capture.sh does it in that order).
- The provisioning tool's progress and final screens are drawn by sourcing
  `scripts/provision.sh` with example values (`summary_text`, `ui_progress`) - no real install,
  no real password in a picture. Keep provision.sh sourceable (its `main` only runs when executed).
- A modal opened by a click can miss when a 500ms panel poll replaces the button at that moment
  (`open_modal` retries) - the same race applies to any new click-to-open step.
