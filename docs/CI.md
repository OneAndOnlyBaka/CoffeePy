# Build, checks and releases

How CoffeePy is tested, audited, built and published on GitHub. For using and installing it,
see the [manual](MANUAL.md) and the [quickstart](QUICKSTART.md); for changing the code,
[CLAUDE.md](../CLAUDE.md).

## Checks on every push and pull request

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml), one job per kind of check, so a red
job says what failed:

| Job | What it checks |
|---|---|
| `test` | The Django test suite on SQLite (fast), and `makemigrations --check`: a model change without its migration would only show on a device, as a crash at `migrate`. |
| `test-postgres` | The same suite against PostgreSQL 16 - the database the device runs - with the image's `pg_dump`/`openssl` ([`docker-compose.test.yml`](../docker-compose.test.yml)). Transactions, locking and date functions behave differently from SQLite: this run found five tests that SQLite had let through. Backup, restore and update code is kept at **100 % line and branch coverage**; the job fails below that. |
| `audit` | Known vulnerabilities: `pip-audit` per requirements file, and Grype on the two images this repo builds. **Every finding that a newer release fixes fails the build.** One without a fix yet is listed; one that can't be taken yet goes into [`.grype.yaml`](../.grype.yaml) with its reason. The images of others (Postgres, Mosquitto, Caddy) are only reported - their packages are theirs to fix; they are pinned to exact versions in `docker-compose.yml`, which Dependabot moves weekly. |
| `lint` | Correctness only, no style: ruff's pyflakes rules (undefined names, unused imports), ShellCheck errors in the installer and other scripts, zizmor on the workflows themselves, gitleaks over the whole history ([`.gitleaksignore`](../.gitleaksignore) lists the known false positives). |

To run the PostgreSQL suite locally: `scripts/test.sh` (Docker or Podman).

**Dependabot** ([`.github/dependabot.yml`](../.github/dependabot.yml)) opens weekly PRs for the
Python packages, the base images, the compose images and the actions - each runs all of the
above. A new Python minor version and a PostgreSQL major are left out on purpose: the first
needs Django's support, the second a dump and restore on every device.

Worth switching on in the repository's settings (free for public repositories): secret
scanning with push protection, Dependabot alerts, CodeQL's default setup, and branch protection
on `master` that requires these jobs - without it they only advise.

## Releases

[`.github/workflows/release.yml`](../.github/workflows/release.yml) builds everything an offline
Pi needs, for `linux/arm64` (64-bit Raspberry Pi OS; `linux/arm/v7` in the manual run's
dropdown for the 32-bit one).

**When:**

| Event | Result |
|---|---|
| push to `master` | a release `vX.Y.Z`, shown as *Latest* - the workflow sets the tag, the next build counts the patch up |
| a tag `vX.Y.Z` | a release of exactly that version - for a minor or major step (or `+semver: minor` in a commit message) |
| push to `develop` | a pre-release `build-<version>-<arch>`; the newest five are kept |
| any other branch | only by hand: Actions → Release → *Run workflow*, with a choice of branch and platform |

A push that only changes docs (`docs/`, `*.md`) builds nothing.

**What each release carries:**

- **Images** on GHCR: `ghcr.io/<owner>/coffeepy-web` and `coffeepy-nfc-bridge`, tagged
  `<version>-<arch>` and `latest-<arch>`.
- **The update bundle**, `coffeepy-update-<version>-<arch>-unsigned.tar`: all five images,
  `docker-compose.yml` and `install/`, for the dashboard's update page.
- **`sign_update_bundle.sh`** - see below.
- **SBOMs**, `coffeepy-sbom-<version>-<arch>.tar`: one CycloneDX file per image (Syft) - what a
  device runs, down to the Python and Alpine packages, for checking it against new
  vulnerabilities without the device and for the Cyber Resilience Act's documentation duty.
- **The provisioning tool**, `coffeepy-provision-<version>.zip`: the committed tree; unpack it
  and run `scripts/provision.sh`.

The version is [GitVersion](https://gitversion.net)'s ([`GitVersion.yml`](../GitVersion.yml); V2
starts at 2.0.0). It is in the image, the bundle's manifest, every tag and file name, and the
footer of dashboard, portal and kiosk.

### Signed bundles - the path for a device in use

The bundles from CI are **unsigned**: the signing key never leaves the machine it was made on,
so it is not at GitHub. A device checks signatures by default and refuses them. For a device
in use:

1. Make a key pair once, on your PC: `scripts/update_signing_key.sh`, and let the device trust
   it - the provisioning tool's *Updates › Own key* (manual, "Your own signing key").
2. For each release: `bash sign_update_bundle.sh coffeepy-update-<version>-arm64-unsigned.tar
   --key ~/.config/coffeepy/update-signing.key`. It checks every file against the manifest
   first, then writes `coffeepy-update-<version>-arm64.tar` and names the key's fingerprint -
   the one the device shows under *Update › Versions*.
3. Upload the signed bundle.

**OS bundles** (`scripts/build_os_bundle.sh`, [ARCHITECTURE.md](ARCHITECTURE.md#operating-system-updates))
are never built here - their packages are the operator's choice - and are signed with the same
script and key.

Installing the unsigned bundle directly needs the device's signature check off
(`UPDATE_VERIFY_SIGNATURE=false`) - **for development only**, on a test device. The
provisioning tool labels it that way, and the dashboard's update page warns for as long as it
is off.

### GHCR packages

A new package on GitHub's container registry starts out **private**, even for a public
repository. This repository's are public; in a fork, switch them once under the repository's
*Packages* (package settings → *Change visibility*), or `docker pull` asks for a login.
