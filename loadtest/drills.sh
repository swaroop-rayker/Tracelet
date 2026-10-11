#!/bin/sh
# M9 degradation drills on the production box (MILESTONES M9, SPEC F15.AC6, NFR3.AC2).
# Run on the VM from the checkout, with a live test link:
#   sh loadtest/drills.sh <slug> > /tmp/drills.log 2>&1
# Each drill breaks one dependency, checks the visitor is still sent on, checks the
# degraded behaviour F15.AC6 specifies, and restores. Visits use an automation user agent,
# so none is human and none can alert. Swap pressure is not staged here: the load test
# drove it (docs/LOADTEST.md).
set -u
SLUG="$1"
URL="https://tracelet.duckdns.org/r/$SLUG"
UA="Go-http-client/1.1 (Tracelet degradation drill)"
DC="docker compose"
API=$($DC ps -q api)

sent_on() {
    # Sent on: a redirect to, or a page carrying, the link's destination. Responses go to
    # memory: on a full disk curl cannot save them, which once looked like a failure.
    out=$(curl -s -m 15 -D /dev/shm/drill.h -o /dev/shm/drill.b -w "%{http_code} %{time_total}" -A "$UA" "$URL")
    dest=$(grep -i '^location:' /dev/shm/drill.h | tr -d '\r' | cut -d' ' -f2)
    [ -z "$dest" ] && dest=$(grep -o 'url=[^"]*' /dev/shm/drill.b | head -1 | cut -d= -f2-)
    case "$dest" in https://example.com*) ok=SENT_ON ;; *) ok=NOT_SENT_ON ;; esac
    echo "  visit: $out -> ${dest:-none} [$ok]"
}

block_host() {  # $1 host, $2 address: resolve it to a dead address inside the api only
    docker exec -u 0 "$API" sh -c "cp /etc/hosts /tmp/hosts.drill && echo '$2 $1' >> /etc/hosts"
}
unblock_hosts() {
    docker exec -u 0 "$API" sh -c "[ -f /tmp/hosts.drill ] && cat /tmp/hosts.drill > /etc/hosts && rm /tmp/hosts.drill"
}

echo "== baseline"
sent_on

echo "== 1. database down"
$DC stop db >/dev/null 2>&1
sleep 3
sent_on; sent_on
echo "  readyz: $(curl -s -m 10 https://tracelet.duckdns.org/readyz | head -c 160)"
$DC start db >/dev/null 2>&1
i=0; until $DC exec -T db pg_isready -q 2>/dev/null || [ $i -ge 60 ]; do i=$((i+1)); sleep 1; done
sleep 5
echo "  database back after ${i}s"
sent_on

echo "== 2. Telegram down"
block_host api.telegram.org 0.0.0.0
sent_on
echo "  telegram-test: $($DC exec -T api tracelet admin telegram-test 2>&1 | tail -1)"
unblock_hosts
echo "  outbox not done: $($DC exec -T db psql -U tracelet -d tracelet -Atc "select count(*) from outbox where status <> 'done'")"

echo "== 3. geo-database update failure (tor-exits: small, no profile rebuild)"
before=$($DC exec -T api readlink /data/geoip/tor-exits/current)
block_host check.torproject.org 0.0.0.0
echo "  update: $($DC exec -T api tracelet geodb update tor-exits --force 2>&1 | tail -2 | tr '\n' ' ')"
after=$($DC exec -T api readlink /data/geoip/tor-exits/current)
echo "  serving before/after: $before / $after"
unblock_hosts
sent_on

echo "== 4. external API timing out (ipwho.is blackholed)"
block_host ipwho.is 10.255.255.1
sent_on; sent_on
sleep 20
echo "  breaker/S9 log lines: $($DC logs --since 1m api 2>&1 | grep -ciE 'ipwhois|breaker|external_geo|s9')"
unblock_hosts

echo "== 5. disk near full"
total=$(df -B1 --output=size / | tail -1); used=$(df -B1 --output=used / | tail -1)
fill=$(( total * 96 / 100 - used ))
sudo fallocate -l "$fill" /var/tmp/tracelet-drill.fill
echo "  disk now $(df --output=pcent / | tail -1 | tr -d ' ')"
sent_on
echo "  banner: $($DC exec -T api python -c 'from tracelet.config import get_settings; from tracelet.health import degradation as d; print([(c.key, c.severity) for c in d._disk_memory(get_settings())])' 2>&1 | tail -1)"
sudo rm -f /var/tmp/tracelet-drill.fill
echo "  disk back to $(df --output=pcent / | tail -1 | tr -d ' ')"
sent_on

echo "== done; services:"
$DC ps --format "{{.Service}} {{.Status}}"
