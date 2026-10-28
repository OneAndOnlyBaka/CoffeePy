#!/bin/sh
# Picks the access point's channel before hostapd starts (ExecStartPre in hostapd's
# coffeepy-channel.conf drop-in, installed by install.sh's access point step): of 1, 6 and
# 11 - the only 2.4GHz channels that don't overlap each other - the one the neighbouring
# networks disturb least, and writes it into hostapd.conf.
#
# Why here and not once at install time: the radio can only look at other channels while it
# isn't an access point yet - running, it hears its own channel and nothing else. So the
# choice is made again at every boot, which also follows a neighbour who moved. A fixed
# channel in between (the old default, 7) overlaps both 6 and 11, where most routers sit,
# and two networks half on top of each other disturb each other more than two on the
# same channel, which at least take turns.
#
# Each neighbour counts with its signal strength (in mW, so one loud router outweighs many
# faint ones), weighted by how much its channel overlaps the candidate: fully on the same
# channel, less with every channel apart, not at all from five channels away. Any failure -
# no scan, nothing to parse - leaves the channel as it was: a scan is an improvement, never
# a condition for the access point to start.
set -u

CONF="${HOSTAPD_CONF:-/etc/hostapd/hostapd.conf}"
IFACE="${AP_INTERFACE:-wlan0}"
IW="${IW:-/usr/sbin/iw}"
EVENTS="${COFFEEPY_EVENTS:-$(dirname "$0")/../data/storage/events.jsonl}"

current="$(sed -n 's/^channel=\([0-9]*\)$/\1/p' "$CONF" 2>/dev/null | head -n 1)"
[ -n "$current" ] || exit 0

ip link set "$IFACE" up 2>/dev/null
scan="$("$IW" dev "$IFACE" scan 2>/dev/null)" || scan=""
if [ -z "$scan" ]; then
    sleep 2  # the radio is sometimes still busy right after boot
    scan="$("$IW" dev "$IFACE" scan 2>/dev/null)" || scan=""
fi
if [ -z "$scan" ]; then
    logger -t coffeepy-ap "channel scan failed - staying on channel $current"
    exit 0
fi

best="$(printf '%s\n' "$scan" | awk '
    /^BSS / { if (ch) { n++; chans[n] = ch; sigs[n] = sig }; ch = ""; sig = "" }
    /signal:/ { sig = $2 }
    /DS Parameter set: channel/ { ch = $NF }
    END {
        if (ch) { n++; chans[n] = ch; sigs[n] = sig }
        split("1 6 11", candidates, " ")
        for (k = 1; k <= 3; k++) {
            c = candidates[k]; load = 0
            for (i = 1; i <= n; i++) {
                apart = chans[i] - c; if (apart < 0) apart = -apart
                if (apart < 5 && sigs[i] != "") load += 10 ^ (sigs[i] / 10) * (1 - apart / 5)
            }
            if (k == 1 || load < best_load) { best_load = load; best = c }
        }
        print best, n + 0
    }')"
channel="${best% *}"
networks="${best#* }"

if [ "$channel" != "$current" ]; then
    sed -i "s/^channel=.*/channel=$channel/" "$CONF"
    logger -t coffeepy-ap "channel $current -> $channel ($networks networks around)"
    mkdir -p "$(dirname "$EVENTS")" 2>/dev/null
    printf '{"time": %s, "action": "wifi.channel", "actor": "access point", "target": "%s", "details": "channel %s -> %s, %s networks around", "outcome": "success"}\n' \
        "$(date +%s)" "$IFACE" "$current" "$channel" "$networks" >> "$EVENTS" 2>/dev/null
else
    logger -t coffeepy-ap "staying on channel $channel ($networks networks around)"
fi
exit 0
