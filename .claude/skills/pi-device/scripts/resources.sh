#!/bin/bash
# CPU (% of one core) and memory per process group on the Pi, sampled over N seconds.
#
#   resources.sh [SECONDS]     default 30
#
# docker stats is no help here: the Pi kernel boots with cgroup_disable=memory, so it shows
# 0B for every container. This reads /proc directly instead (PSS, so shared pages are not
# counted twice, plus what sits in the zram swap).
set -euo pipefail

SECONDS_="${1:-30}"
"$(dirname "$0")/pi.sh" "sudo bash -s $SECONDS_" <<'EOF'
group() {
	case "$1" in
		chrom*) echo chromium ;; postgres*) echo postgres ;; *gunicorn*) echo gunicorn ;;
		python*) echo python ;; Xorg|openbox*) echo X ;; dockerd|containerd*) echo docker ;;
		mosquitto) echo mosquitto ;; *) echo other ;;
	esac
}
ticks() {
	for p in /proc/[0-9]*; do
		c=$(cat "$p/comm" 2>/dev/null) || continue
		set -- $(cut -d' ' -f14,15 "$p/stat" 2>/dev/null) ; echo "$(group "$c") $(( ${1:-0} + ${2:-0} ))"
	done | awk '{t[$1]+=$2} END {for (k in t) print k, t[k]}' | sort
}
A=$(ticks); sleep "$1"; B=$(ticks)
echo "CPU over ${1}s (100% = one core of four):"
join <(echo "$A") <(echo "$B") | awk -v hz="$(getconf CLK_TCK)" -v s="$1" '{printf "  %-10s %5.1f%%\n", $1, ($3-$2)/hz/s*100}' | sort -k2 -nr
echo "Memory (PSS + swapped):"
for p in /proc/[0-9]*; do
	c=$(cat "$p/comm" 2>/dev/null) || continue
	awk -v g="$(group "$c")" '/^Pss:/{p=$2} /^SwapPss:/{w=$2} END{print g, p+0, w+0}' "$p/smaps_rollup" 2>/dev/null
done | awk '{p[$1]+=$2; w[$1]+=$3} END {for (k in p) printf "  %-10s %5d MB + %4d MB swap\n", k, p[k]/1024, w[k]/1024}' | sort -k2 -nr
echo "load $(cut -d' ' -f1-3 /proc/loadavg) | available $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) MB" \
	"| $(vcgencmd measure_temp 2>/dev/null) | throttled $(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)"
EOF
