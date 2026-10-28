#!/bin/bash
# Signs an unsigned update bundle - the one the release workflow publishes - with your own key,
# so devices that check signatures take it. The workflow can't sign: the private key never
# leaves the machine it was made on (scripts/update_signing_key.sh).
#
#   scripts/sign_update_bundle.sh BUNDLE [--key FILE] [--output FILE]
#     --key FILE     default ~/.config/coffeepy/update-signing.key
#     --output FILE  default BUNDLE without "-unsigned", or with "-signed" added
#
# Only manifest.json is signed, and it holds the checksum of every other file in the bundle -
# so those are checked first: a signature says "I vouch for exactly these files". Needs bash,
# GNU tar, sha256sum and openssl (1.1.1 or newer, for Ed25519); runs on its own, outside a
# checkout. Which key a device trusts: its dashboard, Update › Versions, "Update signature".
set -euo pipefail

BUNDLE=""
KEY="${COFFEEPY_SIGNING_KEY:-$HOME/.config/coffeepy/update-signing.key}"
OUT=""
while [ $# -gt 0 ]; do
	case "$1" in
		--key) KEY="$2"; shift ;;
		--output) OUT="$2"; shift ;;
		-h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
		-*) echo "Unknown option $1 (see --help)" >&2; exit 2 ;;
		*) [ -z "$BUNDLE" ] || { echo "Only one bundle at a time" >&2; exit 2; }; BUNDLE="$1" ;;
	esac
	shift
done
[ -n "$BUNDLE" ] || { echo "usage: $0 BUNDLE [--key FILE] [--output FILE]" >&2; exit 2; }
[ -f "$BUNDLE" ] || { echo "No bundle at $BUNDLE" >&2; exit 2; }
[ -f "$KEY" ] || { echo "No signing key at $KEY - create one with scripts/update_signing_key.sh, or pass --key." >&2; exit 2; }
if [ -z "$OUT" ]; then
	case "$BUNDLE" in
		*-unsigned.tar) OUT="${BUNDLE%-unsigned.tar}.tar" ;;
		*.tar) OUT="${BUNDLE%.tar}-signed.tar" ;;
		*) OUT="${BUNDLE}-signed" ;;
	esac
fi
[ "$(realpath -m "$OUT")" != "$(realpath "$BUNDLE")" ] || { echo "--output must not be the bundle itself" >&2; exit 2; }

work="$(mktemp -d)"
trap 'rm -rf "$work" "$OUT.part"' EXIT
tar -xf "$BUNDLE" -C "$work" manifest.json 2>/dev/null \
	|| { echo "$BUNDLE is no update bundle (no manifest.json)" >&2; exit 1; }

echo "== Checking the files the manifest lists =="
members="$(tar -tf "$BUNDLE")"
python3 -c 'import json, sys
for name, digest in json.load(open(sys.argv[1]))["files"].items():
    print(digest, name)' "$work/manifest.json" > "$work/sums"
while read -r digest name; do
	grep -qxF "$name" <<<"$members" || { echo "  $name: listed, but not in the bundle" >&2; exit 1; }
	actual="$(tar -xOf "$BUNDLE" "$name" | sha256sum | cut -d' ' -f1)"
	[ "$actual" = "$digest" ] || { echo "  $name: checksum differs - the bundle is damaged" >&2; exit 1; }
	echo "  $name ok"
done < "$work/sums"

echo "== Signing =="
openssl pkeyutl -sign -inkey "$KEY" -rawin -in "$work/manifest.json" -out "$work/manifest.sig"
openssl pkey -in "$KEY" -pubout -out "$work/key.pub"
openssl pkeyutl -verify -pubin -inkey "$work/key.pub" -rawin -in "$work/manifest.json" \
	-sigfile "$work/manifest.sig" >/dev/null

cp "$BUNDLE" "$OUT.part"
grep -qxF manifest.sig <<<"$members" && tar --delete -f "$OUT.part" manifest.sig
tar -rf "$OUT.part" -C "$work" manifest.sig
mv "$OUT.part" "$OUT"

version="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("version", "?"))' "$work/manifest.json")"
echo "Wrote $OUT - version $version, signed with key $(sha256sum "$work/key.pub" | cut -c1-16)"
echo "(a device shows the key it trusts under Update › Versions, \"Update signature\")"
