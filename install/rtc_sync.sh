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
#
# Every run also says how the RTC is doing in data/storage/rtc.json, for the kiosk's warning
# sign and the dashboard (coffee/rtc.py): ok, missing (no /dev/rtc0 - not wired up, or the
# overlay isn't loaded), unreadable (the chip doesn't answer, or reports its oscillator
# stopped) or lost (it had lost the time - a flat battery, as a rule). "lost" stays until
# the RTC has kept its time across a reboot, since only that shows the battery is good again.
set -eu

THRESHOLD_SECONDS="${RTC_SYNC_THRESHOLD_SECONDS:-60}"
# Before this, an RTC's time can't be real: the DS3231 starts at 2000-01-01 without power.
PLAUSIBLE_EPOCH=1735689600  # 2025-01-01
STORAGE="${COFFEEPY_STORAGE_DIR:-$(dirname "$0")/../data/storage}"
STATUS="${STORAGE}/rtc.json"
BOOT_ID="${BOOT_ID:-$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)}"
RTC_DEVICE="${RTC_DEVICE:-/dev/rtc0}"  # both overridable for coffee/tests/test_rtc.py
# Whether NTP set the clock: then it is right, whatever the RTC or the app's own plausibility
# check (coffee/clock.py, which reads this) would say - and must not be "corrected".
NTP_SYNCED="${NTP_SYNCED:-$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo no)}"

field() { # the value of a "key": "value" or "key": number pair in the old status
    sed -n "s/.*\"$1\": \"\{0,1\}\([^\",}]*\).*/\1/p" "$STATUS" 2>/dev/null | head -n 1
}

report() { # state detail [rtc_epoch] [difference]
    previous="$(field state)"
    lost_boot="$(field lost_boot)"
    if [ "$1" = lost ]; then
        lost_boot="$BOOT_ID"
    elif [ "$1" = ok ] && [ -n "$lost_boot" ]; then
        if [ "$lost_boot" = "$BOOT_ID" ]; then
            set -- lost "$2" "${3:-}" "${4:-}"  # set again this boot - not yet kept through a power cut
        else
            lost_boot=""
        fi
    fi
    mkdir -p "$STORAGE"
    printf '{"state": "%s", "detail": "%s", "checked": %s, "rtc_epoch": %s, "difference": %s, "lost_boot": "%s", "ntp": "%s"}\n' \
        "$1" "$2" "$(date +%s)" "${3:-0}" "${4:-0}" "$lost_boot" "$NTP_SYNCED" > "${STATUS}.tmp"
    chmod 0644 "${STATUS}.tmp"
    mv "${STATUS}.tmp" "$STATUS"
    if [ "$1" != "$previous" ]; then  # into the audit log (dashboard/audit.py collects these)
        outcome=success
        [ "$1" = ok ] || outcome=failure
        printf '{"time": %s, "action": "rtc.%s", "actor": "rtc check", "target": "/dev/rtc0", "details": "%s", "outcome": "%s"}\n' \
            "$(date +%s)" "$1" "$2" "$outcome" >> "${STORAGE}/events.jsonl"
    fi
}

if [ ! -e "$RTC_DEVICE" ]; then
    report missing "no $RTC_DEVICE"
    exit 0
fi

system_epoch="$(date +%s)"
rtc_time="$(hwclock --show 2>/dev/null || true)"
if [ -z "$rtc_time" ]; then
    logger -t coffeepy-rtc-sync "RTC present but not readable - is the DS3231 wired up?"
    # A DS3231 whose oscillator stopped refuses every read until it is set once. Only with a
    # believable system clock - otherwise the next admin correction does it (see above).
    if [ "$system_epoch" -ge "$PLAUSIBLE_EPOCH" ] && hwclock --systohc 2>/dev/null \
            && hwclock --show >/dev/null 2>&1; then
        logger -t coffeepy-rtc-sync "RTC had stopped - restarted it with the system clock"
        report lost "oscillator had stopped - set again from the system clock"
    else
        report unreadable "the RTC does not answer"
    fi
    exit 0
fi

rtc_epoch="$(date -d "$rtc_time" +%s 2>/dev/null || true)"
if [ -z "$rtc_epoch" ]; then
    report unreadable "unexpected answer from hwclock"
    exit 0
fi

difference=$((system_epoch - rtc_epoch))
[ "$difference" -lt 0 ] && difference=$((-difference))

if [ "$difference" -gt "$THRESHOLD_SECONDS" ]; then
    hwclock --systohc
    logger -t coffeepy-rtc-sync "system clock was ${difference}s off the RTC - wrote it back"
fi
if [ "$rtc_epoch" -lt "$PLAUSIBLE_EPOCH" ]; then
    report lost "the RTC had lost the time" "$rtc_epoch" "$difference"
else
    report ok "" "$rtc_epoch" "$difference"
fi
