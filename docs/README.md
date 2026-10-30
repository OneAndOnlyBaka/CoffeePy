# CoffeePy documentation

| Document | For | What's in it |
|---|---|---|
| [QUICKSTART.md](QUICKSTART.md) | anyone installing it | try it on a PC, install it on a Pi (with or without internet), bring over V1's data, set up development |
| [MANUAL.en.md](MANUAL.en.md) · [MANUAL.de.md](MANUAL.de.md) | people using and running it | every screen of kiosk, phone portal, dashboard and provisioning tool, with screenshots; maintenance and troubleshooting |
| [ARCHITECTURE.md](ARCHITECTURE.md) | anyone who wants to know how it works | the system (hardware, host services, containers, network, backups, updates) and the software (apps, data model, flows, roles, audit log) |
| [CALCULATIONS.md](CALCULATIONS.md) | admins and the curious | balance, pillory, rankings, thank-you messages, statistics and portal comparisons - the exact rules |
| [SECURITY.md](SECURITY.md) | admins, reviewers | the security mechanisms, layer by layer, and their known limits |
| [PRIVACY.md](PRIVACY.md) | whoever is responsible for the device | running it in line with the GDPR: data, retention, rights, works council, a checklist |
| [CRA.md](CRA.md) | whoever supplies or assesses a device | Cyber Resilience Act: applicability, risk register, Annex I requirements against the software, open actions |
| [CI.md](CI.md) | maintainers | tests, audits, lint, releases, signing update bundles |
| [CLAUDE.md](../CLAUDE.md) | whoever changes the code | conventions and the traps that already cost time |

The manuals are in German and English; everything else is in English. The manual's
screenshots live in `pictures/manual/<language>/` and are retaken with the user-manual skill
(`.claude/skills/user-manual/`) whenever a screen changes; `pictures/` holds the images the
README shows.
