#!/bin/sh
# Memory and CPU on the production box during a load test (ADR-0027, NFR6.AC1, RISKS R6).
# Run on the VM, detached:  nohup sh loadtest/vm_sample.sh 1200 > /tmp/vm_sample.csv &
# One line every 5 s for the given number of seconds: host memory in use (total minus
# available), swap in use, memory pressure (PSI some avg10), and each container's memory
# and CPU as docker stats reports them.
set -u
seconds="${1:-1200}"
end=$(( $(date +%s) + seconds ))
echo "time,used_mb,available_mb,swap_used_mb,psi_mem_some10,caddy_mem,api_mem,db_mem,caddy_cpu,api_cpu,db_cpu"
while [ "$(date +%s)" -lt "$end" ]; do
    mem=$(awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} /SwapTotal/{st=$2} /SwapFree/{sf=$2} END {printf "%d,%d,%d", (t-a)/1024, a/1024, (st-sf)/1024}' /proc/meminfo)
    psi=$(awk '/^some/{split($2,x,"="); print x[2]}' /proc/pressure/memory 2>/dev/null)
    stats=$(docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' \
        | awk '{n=$1; sub(/^tracelet-/,"",n); sub(/-1$/,"",n); m[n]=$2; c[n]=$5} END {printf "%s,%s,%s,%s,%s,%s", m["caddy"], m["api"], m["db"], c["caddy"], c["api"], c["db"]}')
    echo "$(date -u +%H:%M:%S),$mem,${psi:-},$stats"
    sleep 5
done
