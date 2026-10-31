#!/usr/bin/env python3
"""Fails on every known vulnerability in one of this repo's images that an upgrade fixes - the
CI's audit job (.github/workflows/ci.yml) runs it on Grype's JSON for each image it builds.

One exception, listed as a warning on the PR instead of failing it: a finding in the Python
interpreter itself that only a newer Python *minor* version fixes (3.14 -> 3.16). Which minor
the images run is a decision - Django has to support it - and no dependency update can make it,
so it used to turn every Dependabot PR red until someone wrote it into .grype.yaml. A fix within
the running minor (3.14.10 -> 3.14.11) still fails: a new base image brings it.

    grype docker:IMAGE --only-fixed -o json=grype.json; scripts/audit_images.py grype.json
"""
import json
import re
import sys


def minor(version):
    match = re.match(r"(\d+)\.(\d+)", version or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def needs_newer_python(match):
    artifact = match["artifact"]
    if artifact.get("name") != "python" or artifact.get("type") != "binary":
        return False
    running = minor(artifact.get("version"))
    fixes = [minor(v) for v in match["vulnerability"].get("fix", {}).get("versions", [])]
    return bool(running and fixes) and all(fix and fix > running for fix in fixes)


def main(path):
    report = json.load(open(path))
    failing, waiting = [], []
    for match in report.get("matches", []):
        vuln, artifact = match["vulnerability"], match["artifact"]
        fixed_in = ", ".join(vuln.get("fix", {}).get("versions", [])) or "?"
        row = f"{artifact['name']} {artifact.get('version', '?')}: {vuln['id']} ({vuln.get('severity', '?')}) - fixed in {fixed_in}"
        (waiting if needs_newer_python(match) else failing).append(row)
    for row in failing:
        print(f"FAIL {row}")
    for row in waiting:
        # An annotation, not just a log line: it shows on the PR, so it is seen - and whether the
        # code is reachable from here gets written into .grype.yaml when someone looks at it.
        print(f"::warning title=Needs a newer Python::{row} - not fixable with the Python these images run")
    print(f"{len(failing)} fixable, {len(waiting)} only in a newer Python")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
