#!/usr/bin/env python3
"""Fails on every known vulnerability in a requirements file that a newer release fixes -
the CI's audit job (.github/workflows/ci.yml) runs it once per requirements file. One without
a fix yet is listed, not failed on: there is nothing to upgrade to.

    pip-audit -r requirements.txt --format json --output audit.json; scripts/audit_python.py audit.json
"""
import json
import sys


def main(path):
    report = json.load(open(path))
    fixable, unfixed = [], []
    for dep in report.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            row = f"{dep['name']} {dep.get('version', '?')}: {vuln['id']} {' '.join(vuln.get('aliases', []))}".strip()
            if vuln.get("fix_versions"):
                fixable.append(f"{row} - fixed in {', '.join(vuln['fix_versions'])}")
            else:
                unfixed.append(f"{row} - no fix yet")
    for row in fixable + unfixed:
        print(row)
    print(f"{len(fixable)} fixable, {len(unfixed)} without a fix")
    return 1 if fixable else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
