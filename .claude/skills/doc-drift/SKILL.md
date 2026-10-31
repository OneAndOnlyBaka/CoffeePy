---
name: doc-drift
description: Finds and fixes places where CoffeePy's documentation (docs/, README.md, CLAUDE.md, the skills) no longer matches the code - wrong numbers, labels, flows, file or function names, missing features, German and English manuals saying different things. Use before finishing a branch that changes behaviour, before a release, and whenever the user asks to check the docs or "Doku-Drift".
---

# Documentation drift

About 3,600 lines of documentation describe one codebase in two languages. Each passage was right
when written, and none of them changes by itself when the code does. This skill keeps them in step
on three levels: tests that fail at once, a report that lists what to re-read, and a full audit
for the rest.

## 1. The tests - always

```bash
python manage.py test coffee.tests.test_docs coffee.tests.test_translations
```

`coffee/tests/test_docs.py` fails when:

- the two manuals (or the two member sheets) differ in chapters, sections or screenshots;
- a link, picture or `#anchor` in docs/ or the README points nowhere;
- the member sheet (`docs/MEMBERS.*.md`) contains a digit - its numbers belong to the manual;
- a **pinned fact** changed in the code and not in the manual: kiosk timings, the phone link's
  validity, the portal session, the link lock, the time to hold up a new card, the kiosk
  pillory's length, the login-rules table (from `POLICY_LIMITS` and the model defaults), the
  provisioning tool's settings menu against the installation chapter's table, every dashboard
  page in both manuals.

**When you write a number or a list into the docs that comes from the code, pin it** - add an
assertion to `DocFactsTestCase` that reads the constant and looks for the sentence in both
manuals. That is the only kind of drift that can't come back unnoticed.

## 2. The report - for a branch

```bash
python3 -I .claude/skills/doc-drift/scripts/drift_report.py --since master
```

Candidates to read, not verdicts:

- **paths** - a path in backticks that doesn't exist (device paths under `data/` are skipped);
- **audit** - an audit action named in the docs that no code (Python, or the host's shell
  scripts) writes;
- **labels** - a UI label the English manual quotes (an exact msgid) whose German translation
  never appears in the German manual: the German one then names a button that doesn't exist;
- **touched** - for every code file the branch changed: each doc line naming the file, a changed
  constant or function, or a changed UI text. Read each one against the new code.

## 3. The full audit - before a release, or when asked

Four read-only agents in parallel, one per area, with the briefs in
[references/audit-briefs.md](references/audit-briefs.md): the manual's kiosk/portal/dashboard
chapters against the views and templates; installation, operation and CI docs against the
scripts and workflows; ARCHITECTURE/SECURITY/PRIVACY/CRA/CALCULATIONS/CLAUDE.md against models,
services and host code; German against English, content and labels. Tell each one what step 1
and 2 already cover, so they spend their reading on what only reading finds.

**Verify every finding in the code before changing anything.** The audit of 2026-10-31 was
right on almost every point, but "the doc is wrong" and "the code is wrong" look the same from
the outside:

- A rule written down on purpose wins over a general one: the sort distribution counts orders,
  not strokes - CALCULATIONS.md says so, so the manual was wrong, not `services.py`.
- A finding that is a **code bug** (a link pointing at the wrong page, a countdown that vanished,
  a settings key that never matched `.env`) is fixed in the code, with a test - and named as such
  in the report to the user.

## Fixing

- **One place per fact** - the table in [docs/INDEX.md](../../../docs/INDEX.md) says where each
  kind of fact lives; everywhere else links there instead of repeating it. Versions (Django,
  Python, PostgreSQL) are never written into prose.
- **Both languages, same commit.** German labels are taken from `locale/de/LC_MESSAGES/django.po`
  (the msgstr of the English msgid), the provisioning tool's from its `t "…" "…"` pairs.
- The member sheet stays at one printed page and carries no numbers (it points at the countdown).
- A screen that looks different is the `user-manual` skill's job (screenshots); this skill only
  changes text, unless a fix changes a screen.
- CLAUDE.md and the skills drift too - a function renamed, a "the only way" that stopped being
  the only one. They are in the report's scope; treat them like docs.

## What drifted before

So the next audit knows where to look first (2026-10-31, after ~40 commits without one):

- labels shortened or paraphrased in German (*Link ungültig* for *Link nicht mehr gültig*);
- "the only way …" / "unchanged since …" / "every …" statements, outlived by a later feature;
- numbers and lists: tabs that became optional, a pillory limited to seven, a chart that showed
  money where the manual said cups;
- settings keys spelled differently in two places that agreed with each other but not with
  `.env` (`TIME_ZONE` vs `DJANGO_TIME_ZONE` - no backup ever carried the time zone);
- dead configuration (`KIOSK_*_SECONDS` in settings.py, `BACKUP_DEPTH` read from an `.env` that
  never had it);
- features in the code that the manual never mentioned (CSV exports, *May log in*, the portal's
  *Log out*).
