# Briefs for the full audit

Four `general-purpose` agents, started in one message so they run in parallel. Each brief starts
with: *Read-only documentation drift audit in the CoffeePy repo. Do NOT edit any file.* and ends
with: *Report only real discrepancies: doc file:line, what it says, what the code does
(file:line), a suggested fix. Also list what exists in the code but is missing from the docs.
Compact list, no style opinions.* Add what steps 1 and 2 of the skill already found or cover.

## 1. Manual: kiosk, portal, dashboard

docs/MANUAL.en.md chapters 3-6 (First steps, The kiosk, Phone portal, Dashboard). Check every
claim against the code: labels quoted in bold or italics (templates under `*/templates/`, msgids
in `locale/de/LC_MESSAGES/django.po`), numbers (constants in `kiosk/views.py`, `portal/views.py`,
`portal/decorators.py`, `dashboard/models.py` `POLICY_LIMITS`), who may do what
(`@superuser_required`, `dashboard/urls.py`), the pages in
`dashboard/templatetags/dashboard_nav.py` against the manual, flows (`_handle_new_scan`,
`_tick`, `order_select`), and what each form really lets you change (`dashboard/forms.py`).

## 2. Installation, operation, CI

docs/QUICKSTART.md, docs/MANUAL.en.md chapters 1, 2, 7, 8, docs/CI.md, README.md - against
`.env.example`, `scripts/provision.sh` (`build_env`, `step_settings`, `ANSWER_KEYS`),
`install/install.sh` (steps, flags, `--help`), `install/update_watcher.py`,
`install/os_update.sh`, `scripts/build_*.sh`, `.github/workflows/*.yml`, `docker-compose*.yml`,
the Dockerfiles, `install/packages.txt`, `kiosk/views.py` `MAINTENANCE_STEPS`. Commands shown in
the docs: would they work as written on the device (paths, options, `/tmp` being a small tmpfs)?
Release artifact names against the release workflow.

## 3. Architecture, security, privacy, CRA, calculations, CLAUDE.md

docs/ARCHITECTURE.md, SECURITY.md, PRIVACY.md, CRA.md, CALCULATIONS.md and CLAUDE.md against
models (`coffee/`, `dashboard/`, `portal/`), `coffee/services.py` (formulas; "cups" are
`machine_strokes`), `dashboard/security.py`, `config/settings.py`, `dashboard/tls.py`, the audit
actions (`audit.log(` / `log_actor(` and the host scripts' `event`/`act`), retention and
deletion, `coffee/backups.py`, `dashboard/restore_jobs.py`, `install/update_watcher.py`,
`docker-compose.yml`, `install/*.service`, `.grype.yaml`. Flag every file path, function, setting
or command named in prose that doesn't exist, and every "only", "never", "unchanged", "all"
that the code no longer bears out.

## 4. German against English

docs/MANUAL.de.md against MANUAL.en.md, and MEMBERS.de.md against MEMBERS.en.md, section by
section - structure is already tested, so CONTENT: facts present in one language only or
different (numbers, steps, warnings, table rows, links); which one is right (check the code).
Every bold or italic German label against the msgstr of the English msgid the English manual
quotes, and the provisioning/update-builder labels against their `t "…" "…"` pairs.
