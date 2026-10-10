#!/bin/bash
# vibe-trading-cn 健康检查脚本
# 由 systemd timer（每 5 分钟）调用 + 由监控（Nginx / Prometheus）curl 调用
# 退出码：0 = 健康，1 = 不健康

set -euo pipefail

LOG_PREFIX="[healthcheck]"
EXIT_CODE=0

# ─── 检查 1: systemd 进程是否在跑 ───
if ! systemctl is-active --quiet vibe-trading-cn.service; then
    echo "${LOG_PREFIX} ❌ service not active"
    EXIT_CODE=1
else
    echo "${LOG_PREFIX} ✅ service active"
fi

# ─── 检查 2: scheduler state 文件是否新鲜（< 2 个 interval）───
STATE_PATH="${VIBE_TRADING_SCHEDULER_STATE_PATH:-/var/lib/vibe-trading-cn/scheduler-state.json}"
if [ ! -f "${STATE_PATH}" ]; then
    echo "${LOG_PREFIX} ⚠️  state file missing: ${STATE_PATH}"
    # 首次启动后还没跑过 → 不算失败
    EXIT_CODE=$((EXIT_CODE > 1 ? EXIT_CODE : 0))
else
    LAST_RUN_TS=$(jq -r '.last_run_ts // 0' "${STATE_PATH}" 2>/dev/null || echo 0)
    NOW_TS=$(date +%s)
    INTERVAL="${VIBE_TRADING_INTERVAL_SECONDS:-3600}"
    STALE_THRESHOLD=$((INTERVAL * 2))

    if [ "${LAST_RUN_TS}" = "0" ]; then
        echo "${LOG_PREFIX} ⚠️  state file has no last_run_ts"
    else
        AGE=$((NOW_TS - LAST_RUN_TS))
        if [ "${AGE}" -gt "${STALE_THRESHOLD}" ]; then
            echo "${LOG_PREFIX} ❌ scheduler stale: last run ${AGE}s ago (threshold ${STALE_THRESHOLD}s)"
            EXIT_CODE=1
        else
            echo "${LOG_PREFIX} ✅ scheduler fresh: last run ${AGE}s ago"
        fi
    fi
fi

# ─── 检查 3: 最近一次 run 是否有错误任务 ───
if [ -f "${STATE_PATH}" ]; then
    ERROR_COUNT=$(jq -r '[.last_results // {} | to_entries[] | select(.value < 0)] | length' "${STATE_PATH}" 2>/dev/null || echo 0)
    if [ "${ERROR_COUNT}" -gt 0 ]; then
        echo "${LOG_PREFIX} ⚠️  ${ERROR_COUNT} task(s) failed in last run"
        EXIT_CODE=$((EXIT_CODE > 1 ? EXIT_CODE : 0))
    else
        echo "${LOG_PREFIX} ✅ last run all tasks OK"
    fi
fi

# ─── 检查 4: 磁盘空间（state + decision log 写入目录）───
DATA_DIR="$(dirname "${STATE_PATH}")"
DISK_USAGE=$(df -P "${DATA_DIR}" | awk 'NR==2 {print $5}' | tr -d '%')
if [ "${DISK_USAGE}" -gt 90 ]; then
    echo "${LOG_PREFIX} ❌ disk usage ${DISK_USAGE}% > 90%"
    EXIT_CODE=1
else
    echo "${LOG_PREFIX} ✅ disk usage ${DISK_USAGE}%"
fi

# ─── 告警（可选）───
if [ "${EXIT_CODE}" -ne 0 ] && [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
    MSG="[vibe-trading-cn] health check FAILED on $(hostname) at $(date -Iseconds)"
    case "${ALERT_WEBHOOK_TYPE:-wechat}" in
        wechat)
            curl -s -X POST "${ALERT_WEBHOOK_URL}" \
                -H "Content-Type: application/json" \
                -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"${MSG}\"}}" \
                > /dev/null || true
            ;;
        dingtalk)
            curl -s -X POST "${ALERT_WEBHOOK_URL}" \
                -H "Content-Type: application/json" \
                -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"${MSG}\"}}" \
                > /dev/null || true
            ;;
        slack)
            curl -s -X POST "${ALERT_WEBHOOK_URL}" \
                -H "Content-Type: application/json" \
                -d "{\"text\":\"${MSG}\"}" \
                > /dev/null || true
            ;;
    esac
    echo "${LOG_PREFIX} 🚨 alert sent"
fi

exit ${EXIT_CODE}
