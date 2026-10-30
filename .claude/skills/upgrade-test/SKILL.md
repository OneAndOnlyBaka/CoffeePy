---
name: upgrade-test
description: Full upgrade test of CoffeePy on the target hardware - downgrade the test Pi to the release two versions back, update it step by step through the real update mechanism, and prove that no data was lost and the device stays stable. Use for every larger change to the update path - PostgreSQL version, the update watcher/bundle/recovery, migrations that move or transform data, compose services or volumes, installer steps the watcher runs - before calling it done, and whenever the user asks for an upgrade, migration or update test.
---

# Upgrade test on the target hardware

A device in use never gets a fresh install: it walks from release to release through the update
watcher, carrying its data along. A change to that path is only tested once it has walked it -
on the real Pi, from an old release, with data on it. Unit tests and a fresh install prove
neither that the old watcher can deliver the new code nor that a migration keeps every row.

**Focus: nothing lost, and stable on the way from old to new.** Every hop is checked against a
fingerprint of the data taken before the first one; a difference nobody can explain is a failed
test, not a footnote.

Runs on the `pi-device` skill's test Pi (its setup, `pi.sh`, the rules under "Things that
already went wrong here"). The scripts below live in `scripts/` here; run them by path from the
repo root.

## First: agree on the test plan

Before touching the device, work out the plan with the developer - **targeted questions, each
with a proposal** (AskUserQuestion, recommended option first), not an open "what should I
test?". Always settle:

- **Start version and path.** Default: the newest release tag two before the current one
  (`git tag --sort=-v:refname 'v*'`), then each release in between, then this working tree:
  e.g. `v2.0.0 -> v2.0.1 -> working tree`. Propose a longer path when the change touches data
  an older release wrote differently.
- **The way back to the release.** The working tree is a pre-release, and a pre-release device
  may go back to a release of its major version (`back_to_release`; the watcher reverses the
  migrations the release doesn't know first, `migrate_back`). Default: one more hop, `working
  tree -> newest release`, then `-> working tree` again - with the fingerprint, and with a
  migration of the working tree's own in play if it has one (only its own tables may differ).
- **Data set.** The device's database is wiped for the downgrade. Proposals: (a) a legacy
  database the developer provides, imported on the old release (realistic history, real names -
  see "No real data in screenshots"), (b) `perf_data.py seed` from the pi-device skill (five
  years of synthetic history - check that it fits the old schema), (c) a backup made by that
  release, restored there. Whatever it is: members, multi-stroke orders, payments, coffee
  sorts, settings changed from their defaults (Kiosk boards, night rest, login & name rules), a
  second dashboard user. **If the Pi holds data the developer may still want, ask before
  wiping.**
- **Which power cuts** (default: the three under "Robustness"), and whether a hop of an old
  watcher is cut too.
- **Hardware the developer has to touch**: a card tap (never fake one - no `NfcScan` rows), a
  backup stick for backup/restore across versions (step 7). **Look at the stick before it is
  formatted** - mount it read-only and list it; the developer decides about what's on it.
- **Time.** A full run takes 2-3 hours of the device: ~25 min install, ~15 min per bundle
  build, ~10 min per update, ~5 min per power cut.

When a question comes up mid-run that changes what the test proves - a check that can't run,
a difference you can't explain, a step the plan didn't foresee - stop and ask again, with a
proposal.

## The run

Keep every output (fingerprints, logs, timings) in the scratchpad - `$S` below.

1. **Build the bundles first**, while the device still runs whatever it runs - each build costs
   ~15 min and re-tags images (`bundle.sh` puts the running tags back):
   `bundle.sh v2.0.1` and `bundle.sh . current` - signed here, stored on the Pi in `~/bundles/`.
2. **Install the old release**: `install_version.sh v2.0.0`, then follow `~/install.out` on the
   Pi with Monitor until the verify step. It wipes the database and the update state, removes
   units the old release doesn't have, and leaves data/tls and data/storage alone. Check
   `/dashboard/update/` shows the old version and the kiosk its start screen (pi-device
   `screenshot.sh`).
3. **Load the data** as agreed, then **the baseline**: `fingerprint.py $S/fp-base.json`.
4. **Each hop**: `apply.sh v2.0.1` (it waits and prints the log), then the checks below, then
   `fingerprint.py $S/fp-<hop>.json $S/fp-base.json`. Always compare with the *baseline*, not
   the previous hop. Do one hop through the dashboard's upload instead of `apply.sh` -
   `dashboard.sh /dashboard/update/upload/ bundle=@~/bundles/NAME.tar`, then `apply.sh --follow
   NAME` - that path is what admins use.
5. **Robustness** on the final version, with the new watcher in charge - apply `current` again
   (same version is allowed) and cut it each time:
   - `powercut.sh loaded 5` - images loaded, dump running: only the tags go back.
   - `powercut.sh switched 3` - new version in place: the full rollback, database included.
   - `powercut.sh switched 40` - during the health check or the services step.

   Each time: `powercut.sh PHASE DELAY`, `apply.sh --no-wait current`, `powercut.sh --wait`
   (shows what the recovery did), then the checks; arm the next cut while the retry runs, and
   after the last one `apply.sh --follow current` - the third attempt has to reach "done". Also: a broken bundle
   (one flipped byte in `images.tar`, or a stale `manifest.sig`) is refused and changes nothing,
   and a good one right after it goes through.
6. **Stability** once at the end: two reboots (cold, `sudo systemctl reboot`), and after each
   the checks; pi-device `resources.sh 60`, `load.py mixed`, `page_timings.py` (every page, with
   the seeded history), time until the kiosk is usable (pi-device "Measuring").
7. **Backups across versions**, when a stick is in: set it up through the *old* release's
   dashboard (`dashboard.sh /dashboard/backup/usb/setup/ disk=/dev/sda confirm=1`, ~20s), then
   on every version of the path `dashboard.sh /dashboard/backup/now/`, `fingerprint.py
   $S/bk-<v>.json` and `django.sh .claude/skills/upgrade-test/scripts/marker.py <v>` (a new coffee
   sort plus 100 on order 1: a change made *after* that backup). After each update the stick must still be "own". On the
   final version restore every backup, oldest first (`dashboard.sh
   /dashboard/backup/own/FILE/restore/ parts=database confirm=DELETE`, poll `dashboard.sh GET
   /dashboard/backup/restore/status/`): `marker.py` without a tag lists the markers - only
   those set before it may be there, and the
   fingerprint against `bk-<v>.json` only differs as below.

A change to the watcher itself has a catch: the first hop onto it is applied by the *old*
watcher, without the new protection. Cutting that hop shows what a real device risks once -
decide with the developer whether that is part of the test, and whether the change (a
PostgreSQL migration, say) has to wait for the second update after it.

## Checks after every hop

- `state.json` "done", and the log as expected (`apply.sh` prints it); the update's
  `update.apply` audit entry is there.
- `fingerprint.py` against the baseline: **RESULT: nothing lost**. A table only shown as
  changed must be explained by the migration in question - otherwise the test failed.
- Health 200; kiosk start screen (screenshot, both languages if texts changed); dashboard and
  portal pages answer (`page_timings.py`); the version list names the expected version, OS and
  image ids.
- `systemctl --failed` empty; the installed units match `install/*.service` of that version;
  `docker ps` shows no restarting container; `journalctl -p err -b` and `dmesg -l err` have
  nothing new.
- Nothing left behind: `data/updates/` holds only incoming/ (empty), installed.json,
  pre-update/, state.json, status/, system.json; no `coffeepy-previous-*` tag
  (`docker images`); no maintenance status; `df -h /` against the hop before.
- The pre-update dump is complete: `zcat … | tail -c 512` ends with pg_dump's
  "database dump complete" (an older watcher writes a custom-format `.dump`).
- For a database change: the server version (`select version()`), every table's row count
  against the baseline, and the old volume/image gone once the new one runs.

## Report

A table per hop and per power cut: duration, downtime (maintenance screen on to off), recovery
time, fingerprint result, disk use, anything unexpected. State what was *not* tested (a hop not
cut, hardware not touched) as plainly as what passed. Numbers, not impressions.

## Things learned

- **One failed ssh is no reboot** - the WiFi drops the odd connection. `powercut.sh --wait`
  compares the boot id; a loop on "ssh fails" reported a reboot that never happened.
- **A new watcher clears an old one's leftovers at its first start** (~20s after the update's
  "applied") - check the leftovers after its "watching" line, or it looks like they stayed.
- **A login rule stricter than the admin's password blocks the dashboard upload**: the login
  works, then every page redirects to the password change (`auth.password_outdated`), and the
  upload's 302 looks like success. Seed a password policy the `.env` admin still meets;
  `dashboard.sh` prints where every POST went and the page's messages.
- **An old release names the nfc-bridge image after its build directory** (no `name:` in its
  compose file) - `bundle.sh` builds in a directory called `coffeepy` for that reason.
- **The dev machine may drop the Pi's AP** ("No route to host"): `nmcli connection up CoffeePy`.
- **A fingerprint that becomes the baseline must hash its own columns**: after a migration
  added one, a hash over the old baseline's columns compared with one over the new ones and
  reported every order as changed. `fingerprint.py` keeps both apart now (`md5` vs `cmp`).
- **A backup is taken inside the "Back up now" request**, before that request's own
  `backup.trigger` audit row: restored, the row is missing and `backup.restore` takes its id -
  the fingerprint reports the audit table (and `auth_user`'s `last_login`) as changed. Those two
  are expected after a restore; anything else is not.
- Measured 2026-10-30, Pi 3: an update 8-11 min (v2.0.1's watcher 10 min, the step back to a
  release 11 min incl. 11s of migrate_back), a recovery after a power cut 27s (at loaded) to
  72s (at switched+40), the kiosk polling 93-96s after a cold boot; formatting a 4GB stick ~20s,
  a restore of the seeded data 17-21s, also from a v2.0.0 backup onto the working tree.
- `install_version.sh` and `bundle.sh` overwrite `~/CoffeePy` and `~/coffeepy-src` on the Pi;
  pi-device's `sync.sh`/`deploy.sh` afterwards put the working tree back.

## Cleaning up

`rm -rf ~/bundles ~/coffeepy-src /var/tmp/powercut.last` on the Pi, `perf_data.py cleanup` if
seeded, no `powercut-*` unit left (`systemctl list-units 'powercut-*'`), the device on the
current version, the kiosk on its start screen (screenshot). Tell the developer what data is
now on the device.
