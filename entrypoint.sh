#!/bin/sh
set -e

# The stack's migrate service prepares the database once for every container and the others
# set COFFEEPY_PREPARE=0 (docker-compose.yml) - at boot Docker starts them all at once, and
# each running migrate itself kept a Pi 3 busy for half a minute. A plain `docker run` of
# the image still gets a usable database.
[ "${COFFEEPY_PREPARE:-1}" = "0" ] || python manage.py prepare

exec "$@"
