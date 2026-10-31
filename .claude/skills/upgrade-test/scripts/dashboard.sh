#!/bin/bash
# Sends one dashboard form as the admin would - login, CSRF, POST - and prints where it went
# and the dashboard's messages. Runs on the Pi against its own Caddy, with the admin from
# /opt/coffeepy/.env: the password never leaves the device.
#
#   dashboard.sh /dashboard/backup/now/
#   dashboard.sh /dashboard/backup/usb/setup/ disk=/dev/sda confirm=1
#   dashboard.sh /dashboard/backup/own/FILE/restore/ parts=database confirm=DELETE
#   dashboard.sh /dashboard/update/upload/ bundle=@~/bundles/current.tar   (a file: -F's @)
#   dashboard.sh GET /dashboard/backup/status/                             (prints the body)
set -euo pipefail

METHOD=POST
[ "${1:-}" = GET ] && { METHOD=GET; shift; }
URL_PATH="${1:?usage: dashboard.sh [GET] PATH [field=value ...]}"
shift
fields=""
for f in "$@"; do fields+=" $(printf '%q' "$f")"; done
PI="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)/.claude/skills/pi-device/scripts/pi.sh"
"$PI" "set -euo pipefail
user=\$(sudo sed -n 's/^DJANGO_SUPERUSER_USERNAME=//p' /opt/coffeepy/.env)
pass=\$(sudo sed -n 's/^DJANGO_SUPERUSER_PASSWORD=//p' /opt/coffeepy/.env)
jar=\$(mktemp -p ~); page=\$(mktemp -p ~); trap 'rm -f \"\$jar\" \"\$page\"' EXIT
base=https://localhost:8443
c=(curl -sS --cacert /opt/coffeepy/data/tls/ca.crt -b \"\$jar\" -c \"\$jar\")
token() { awk '\$6 == \"csrftoken\" {print \$7}' \"\$jar\"; }
\"\${c[@]}\" -o /dev/null \"\$base/dashboard/login/\"
\"\${c[@]}\" -o /dev/null -e \"\$base/dashboard/login/\" --data-urlencode \"csrfmiddlewaretoken=\$(token)\" \
	--data-urlencode \"username=\$user\" --data-urlencode \"password=\$pass\" \"\$base/dashboard/login/\"
grep -q sessionid \"\$jar\" || { echo 'login failed'; exit 1; }
if [ $METHOD = GET ]; then \"\${c[@]}\" \"\$base$URL_PATH\"; echo; exit 0; fi
args=(-F \"csrfmiddlewaretoken=\$(token)\")
for f in $fields; do
	case \"\$f\" in *=@~/*) f=\"\${f%%=@*}=@\$HOME/\${f#*=@~/}\" ;; esac
	args+=(-F \"\$f\")
done
# A 302 alone proves nothing (a password the login rules outdate redirects every page to the
# password change, auth.password_outdated): where it went, and what the page then says.
read -r code to < <(\"\${c[@]}\" -o \"\$page\" -w '%{http_code} %{redirect_url}\\n' -e \"\$base$URL_PATH\" \"\${args[@]}\" \"\$base$URL_PATH\")
echo \"\$(date +%T) POST $URL_PATH: HTTP \$code\${to:+ -> \${to#\$base}}\"
[ -n \"\$to\" ] && \"\${c[@]}\" -o \"\$page\" \"\$to\"
grep -o '<p class=\"msg[^>]*>[^<]*' \"\$page\" | sed 's/.*>/  message: /' || true
grep -o 'class=\"errorlist[^<]*<li>[^<]*' \"\$page\" | sed 's/.*<li>/  form error: /' || true"
