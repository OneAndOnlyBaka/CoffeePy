#!/usr/bin/env python3
"""Lists where the documentation may have drifted from the code - candidates to read, not
verdicts. The hard invariants are tests (coffee/tests/test_docs.py); this covers what would be
too noisy for a test.

    python3 -I .claude/skills/doc-drift/scripts/drift_report.py              # whole tree
    python3 -I .claude/skills/doc-drift/scripts/drift_report.py --since master   # + what a branch touched

Sections:
  paths     a `path/in/backticks` that doesn't exist (device paths under data/ etc. are skipped)
  audit     an audit action named in the docs (`member.export`) that no code writes
  labels    a UI label the English manual quotes (bold or italic, an exact msgid) whose German
            translation the German manual never uses - the German one then says something else
  touched   (--since) for every changed code file: the doc lines that name it, its changed
            constants or functions - each one a sentence to re-read
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DOCS = sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md", ROOT / "CLAUDE.md"] + sorted(
    (ROOT / ".claude" / "skills").glob("*/SKILL.md"))
# Paths that only exist on the device, in a container or as a name of something else.
NOT_IN_REPO = ("data/", "linux/", "nfc/", "library/", "/", "~", "pictures/", "docs/pictures/")


def rel(path):
    return path.relative_to(ROOT)


def lines(path):
    return path.read_text().splitlines()


def check_paths():
    found = []
    for doc in DOCS:
        for n, line in enumerate(lines(doc), 1):
            for p in re.findall(r"`([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?)`", line):
                if p.startswith(NOT_IN_REPO) or re.search(r"[<>*]", p):
                    continue
                candidates = [ROOT / p, doc.parent / p]
                app, _, name = p.partition("/")  # "kiosk/index.html" means kiosk/templates/kiosk/index.html
                candidates.append(ROOT / app / "templates" / app / name)
                if not any(c.exists() for c in candidates):
                    found.append(f"{rel(doc)}:{n}: `{p}`")
    return found


def audit_actions():
    actions = set()
    for py in ROOT.glob("*/**/*.py"):
        if ".venv" in py.parts or "tests" in py.parts:
            continue
        actions |= set(re.findall(r'"([a-z_]+\.[a-z_]+)"', py.read_text()))
    for sh in (ROOT / "install").glob("**/*.sh"):
        actions |= set(re.findall(r"\b(?:event|act)\s+([a-z_]+\.[a-z_]+)", sh.read_text()))
    return actions


def check_audit():
    known, found = audit_actions(), []
    prefixes = {a.split(".")[0] for a in known if a.split(".")[0] not in ("os", "json", "self", "request")}
    for doc in DOCS:
        for n, line in enumerate(lines(doc), 1):
            for a in re.findall(r"[`*]([a-z_]+\.[a-z_]+)[`*]", line):
                if re.search(r"\.(py|md|sh|json|jsonl|ya?ml|txt|env|html|css|js|png|conf|service|timer|log|crt|key|pub|"
                             r"tar|gz|sql|dump|po|mo|toml|cfg|example|db|local)$", a):
                    continue
                if a.split(".")[0] in prefixes and a not in known:
                    found.append(f"{rel(doc)}:{n}: {a}")
    return found


def translations():
    po = (ROOT / "locale" / "de" / "LC_MESSAGES" / "django.po").read_text()
    pairs = re.findall(r'^(?:msgctxt "[^"]*"\n)?msgid "(.+)"\nmsgstr "(.+)"$', po, re.M)
    result = {}
    for english, german in pairs:
        result.setdefault(english, set()).add(german)
    return result


def check_labels():
    """Only exact msgids count as labels; a word that is also English prose ("Order", "Pay")
    is still checked, because the German manual quotes its translation as a label too."""
    german_of, found = translations(), []
    english = (ROOT / "docs" / "MANUAL.en.md").read_text()
    german = re.sub(r"\s+", " ", (ROOT / "docs" / "MANUAL.de.md").read_text())
    for label in sorted(set(re.findall(r"(?:\*\*|(?<!\*)\*)([^*\n]{3,60}?)(?:\*\*|\*(?!\*))", english))):
        if label in german_of and not any(g in german for g in german_of[label]):
            found.append(f"docs/MANUAL.en.md: *{label}* -> German manual has none of {sorted(german_of[label])}")
    return found


def check_touched(ref):
    changed = subprocess.run(["git", "diff", "--name-only", f"{ref}...HEAD"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.split()
    changed += subprocess.run(["git", "diff", "--name-only"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    found = []
    for name in sorted(set(changed)):
        if name.endswith(".md") or name.startswith(("docs/", "locale/")) or "/tests/" in name:
            continue
        diff = subprocess.run(["git", "diff", ref, "--", name], cwd=ROOT, capture_output=True, text=True).stdout
        names = {Path(name).name, name}
        names |= set(re.findall(r"^[+-]\s*(?:def |class )?([A-Z][A-Z0-9_]{3,}|[a-z_]{5,})\s*[(=]", diff, re.M))
        names |= set(re.findall(r'^[+-].*?(?:_|gettext|translate) ?\(?"([^"]{8,})"', diff, re.M))  # changed UI text
        for doc in DOCS:
            for n, line in enumerate(lines(doc), 1):
                hit = next((x for x in names if x in line), None)
                if hit:
                    found.append(f"{name} -> {rel(doc)}:{n} ({hit})")
    return found


def main():
    sections = {"paths": check_paths(), "audit": check_audit(), "labels": check_labels()}
    if "--since" in sys.argv:
        sections["touched"] = check_touched(sys.argv[sys.argv.index("--since") + 1])
    for title, found in sections.items():
        print(f"== {title}: {len(found)}")
        for entry in found:
            print("  " + entry)


if __name__ == "__main__":
    main()
