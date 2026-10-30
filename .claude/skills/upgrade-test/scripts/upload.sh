#!/bin/bash
# Uploads a bundle from ~/bundles through the dashboard - login, CSRF, the form an admin uses -
# instead of dropping it into incoming/. Runs on the Pi against its own Caddy, with the admin
# from /opt/coffeepy/.env: the password never leaves the device. Then follow it with
# `apply.sh --follow NAME`.
#
#   upload.sh current
set -euo pipefail

NAME="${1:?usage: upload.sh NAME}"
PI="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)/.claude/skills/pi-device/scripts/pi.sh"
"$PI" "set -euo pipefail
user=\$(sudo sed -n 's/^DJANGO_SUPERUSER_USERNAME=//p' /opt/coffeepy/.env)
pass=\$(sudo sed -n 's/^DJANGO_SUPERUSER_PASSWORD=//p' /opt/coffeepy/.env)
jar=\$(mktemp -p ~); trap 'rm -f \"\$jar\"' EXIT
base=https://localhost:8443
c=(curl -sS --cacert /opt/coffeepy/data/tls/ca.crt -b \"\$jar\" -c \"\$jar\")
token() { awk '\$6 == \"csrftoken\" {print \$7}' \"\$jar\"; }
\"\${c[@]}\" -o /dev/null \"\$base/dashboard/login/\"
\"\${c[@]}\" -o /dev/null -e \"\$base/dashboard/login/\" --data-urlencode \"csrfmiddlewaretoken=\$(token)\" \
	--data-urlencode \"username=\$user\" --data-urlencode \"password=\$pass\" \"\$base/dashboard/login/\"
grep -q sessionid \"\$jar\" || { echo 'login failed'; exit 1; }
# A 302 alone proves nothing: a password the login rules outdate sends every page to the
# password change first (auth.password_outdated) - only a redirect back to the update page is an upload.
read -r code to < <(\"\${c[@]}\" -o /dev/null -w '%{http_code} %{redirect_url}\\n' -e \"\$base/dashboard/update/\" -F \"csrfmiddlewaretoken=\$(token)\" \
	-F \"bundle=@\$HOME/bundles/$NAME.tar\" \"\$base/dashboard/update/upload/\")
case \"\$to\" in */dashboard/update/) ;; *) echo \"upload refused: HTTP \$code -> \$to\"; exit 1 ;; esac
echo \"\$(date +%T) uploaded $NAME.tar through the dashboard: HTTP \$code\""
