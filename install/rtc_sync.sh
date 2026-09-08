#!/bin/sh
# Writes the system clock back to the DS3231 - but only when the two have genuinely
# diverged, not on every tick.
#
# Why this exists at all: the dashboard's "System time" page sets the clock from inside
# the `web` container (CAP_SYS_TIME). That reaches the kernel's clock, which is shared
# with the host, but nothing in that path can reach the RTC - and an offline appliance
# never gets the NTP sync that would otherwise let the kernel update the RTC by itself
# ("11 minute mode"). Without this, an admin's correction is gone at the next power cut,
# because the boot-time hctosys copies the stale RTC value straight back over it.
#
# Why the threshold: the DS3231 is temperature-compensated and drifts perhaps a tenth of
# a second per day, while the Pi's own clock drifts seconds per day with no NTP to
# discipline it. Writing unconditionally would replace the accurate clock with the
# inaccurate one every few minutes. A step of more than a minute is not drift - it's
# somebody having set the time on purpose, which is exactly what has to survive.
set -eu

THRESHOLD_SECONDS="${RTC_SYNC_THRESHOLD_SECONDS:-60}"

[ -e /dev/rtc0 ] || exit 0

rtc_time="$(hwclock --show 2>/dev/null || true)"
if [ -z "$rtc_time" ]; then
    logger -t coffeepy-rtc-sync "RTC present but not readable - is the DS3231 wired up?"
    exit 0
fi

rtc_epoch="$(date -d "$rtc_time" +%s 2>/dev/null || true)"
[ -n "$rtc_epoch" ] || exit 0
system_epoch="$(date +%s)"

difference=$((system_epoch - rtc_epoch))
[ "$difference" -lt 0 ] && difference=$((-difference))

if [ "$difference" -gt "$THRESHOLD_SECONDS" ]; then
    hwclock --systohc
    logger -t coffeepy-rtc-sync "system clock was ${difference}s off the RTC - wrote it back"
fi
