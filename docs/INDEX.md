# CoffeePy documentation

| Document | For | What's in it |
|---|---|---|
| [QUICKSTART.md](QUICKSTART.md) | anyone installing it | try it on a PC, install it on a Pi (with or without internet), bring over V1's data, set up development |
| [MANUAL.en.md](MANUAL.en.md) · [MANUAL.de.md](MANUAL.de.md) | people using and running it | every screen of kiosk, phone portal, dashboard and provisioning tool, with screenshots; maintenance and troubleshooting |
| [MEMBERS.en.md](MEMBERS.en.md) · [MEMBERS.de.md](MEMBERS.de.md) | members, printed next to the machine | one page: order, cancel, pay, link a phone, what to do when something goes wrong |
| [ARCHITECTURE.md](ARCHITECTURE.md) | anyone who wants to know how it works | the system (hardware, host services, containers, network, backups, updates) and the software (apps, data model, flows, roles, audit log) |
| [CALCULATIONS.md](CALCULATIONS.md) | admins and the curious | balance, pillory, rankings, thank-you messages, statistics and portal comparisons - the exact rules |
| [SECURITY.md](SECURITY.md) | admins, reviewers | the security mechanisms, layer by layer, and their known limits |
| [PRIVACY.md](PRIVACY.md) | whoever is responsible for the device | running it in line with the GDPR: data, retention, rights, works council, a checklist |
| [CRA.md](CRA.md) | whoever supplies or assesses a device | Cyber Resilience Act: applicability, risk register, Annex I requirements against the software, open actions |
| [CI.md](CI.md) | maintainers | tests, audits, lint, releases, signing update bundles |
| [CLAUDE.md](../CLAUDE.md) | whoever changes the code | conventions and the traps that already cost time |

**One place per fact.** How something works is described once, in ARCHITECTURE.md;
SECURITY.md, PRIVACY.md and CRA.md say what it means for their concern and link there; the
QUICKSTART and the manuals say what to do. A number, version or recommendation lives in exactly
one place and is linked from everywhere else, not repeated - copies drift apart:

| Fact | Its one place |
|---|---|
| Hardware, OS image (64-bit Lite), RAM, what the device uses | the manual's [What you need](MANUAL.en.md#1-what-you-need) |
| Kiosk timings (auto-booking, logout, return to Order), name rules, what each screen does | the manual's [The kiosk](MANUAL.en.md#4-the-kiosk) |
| Every `.env` setting | the manual's settings table in [Installation](MANUAL.en.md#2-installation) |
| How balances, rankings and statistics are calculated | [CALCULATIONS.md](CALCULATIONS.md) |
| What is stored and for how long | [PRIVACY.md](PRIVACY.md) |
| Versions of Django, Python, PostgreSQL | `requirements.txt`, the Dockerfiles, `docker-compose.yml` - never written into prose |

The member sheet (MEMBERS) carries no numbers at all - it points at what the screen shows (the
countdown) - so it can't drift from the manual. The two manuals are checked against each other
by `coffee/tests/test_docs.py`: same chapters and sections, same screenshots, every link and
every `#anchor` in docs/ resolving, and the numbers the manuals take from the code pinned to it.
The `doc-drift` skill (`.claude/skills/doc-drift/`) does the rest: a report of what a branch's
changes may have outdated, and a full audit before a release.

The manuals are in German and English; everything else is in English. The manual's
screenshots live in `pictures/manual/<language>/` and are retaken with the user-manual skill
(`.claude/skills/user-manual/`) whenever a screen changes; `pictures/` holds the images the
README shows.
