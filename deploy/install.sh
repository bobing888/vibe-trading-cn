#!/bin/bash
# vibe-trading-cn 一键安装脚本
# 用法：sudo bash deploy/install.sh
# 前提：Linux + systemd + Python 3.11+

set -euo pipefail

REPO_URL="https://github.com/bobing888/vibe-trading-cn.git"
INSTALL_DIR="/opt/vibe-trading-cn"
SERVICE_USER="vibe-trading"
DATA_DIR="/var/lib/vibe-trading-cn"
SECRETS_DIR="/etc/vibe-trading-cn"

echo "[install] === vibe-trading-cn 安装器 ==="

# ─── 1. 系统依赖 ───
echo "[install] 1/8 安装系统依赖"
if command -v apt-get &> /dev/null; then
    apt-get update
    apt-get install -y python3 python3-venv python3-pip git jq
elif command -v yum &> /dev/null; then
    yum install -y python3 python3-pip git jq
else
    echo "[install] ❌ 不支持的包管理器（需 apt-get 或 yum）"
    exit 1
fi

# ─── 2. 创建用户 ───
echo "[install] 2/8 创建用户 ${SERVICE_USER}"
if ! id "${SERVICE_USER}" &> /dev/null; then
    useradd --system --shell /bin/false --home-dir "${DATA_DIR}" "${SERVICE_USER}"
fi

# ─── 3. 创建目录 ───
echo "[install] 3/8 创建目录"
mkdir -p "${INSTALL_DIR}" "${DATA_DIR}" "${SECRETS_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA_DIR}" "${INSTALL_DIR}"
chmod 700 "${SECRETS_DIR}"

# ─── 4. 克隆代码 ───
echo "[install] 4/8 克隆代码到 ${INSTALL_DIR}"
if [ -d "${INSTALL_DIR}/.git" ]; then
    cd "${INSTALL_DIR}" && git pull origin main
else
    git clone "${REPO_URL}" "${INSTALL_DIR}"
fi
cd "${INSTALL_DIR}"

# ─── 5. 虚拟环境 + 依赖 ───
echo "[install] 5/8 创建虚拟环境 + 装依赖"
if [ ! -d ".venv" ]; then
    sudo -u "${SERVICE_USER}" python3 -m venv .venv
fi
sudo -u "${SERVICE_USER}" .venv/bin/pip install --upgrade pip wheel
# 项目用 src/ layout + pyproject.toml（无 [tool.setuptools]，setuptools>=61 自动发现）
# 会自动安装基座 vibe-trading-ai>=0.1.16 + ccxt + langgraph + mcp 等
sudo -u "${SERVICE_USER}" .venv/bin/pip install -e .

# ─── 6. 配置文件 ───
echo "[install] 6/8 配置 secrets.env"
if [ ! -f "${SECRETS_DIR}/secrets.env" ]; then
    cp "${INSTALL_DIR}/deploy/.env.example" "${SECRETS_DIR}/secrets.env"
    chmod 600 "${SECRETS_DIR}/secrets.env"
    chown root:root "${SECRETS_DIR}/secrets.env"
    echo "[install] ⚠️  请编辑 ${SECRETS_DIR}/secrets.env 填入 API 密钥"
    echo "[install] 然后：systemctl start vibe-trading-cn.service"
else
    echo "[install] secrets.env 已存在，跳过"
fi

# ─── 7. systemd unit ───
echo "[install] 7/8 安装 systemd unit"
cp "${INSTALL_DIR}/deploy/vibe-trading-cn.service" /etc/systemd/system/
cp "${INSTALL_DIR}/deploy/vibe-trading-cn-healthcheck.service" /etc/systemd/system/
cp "${INSTALL_DIR}/deploy/vibe-trading-cn-healthcheck.timer" /etc/systemd/system/
chmod +x "${INSTALL_DIR}/deploy/healthcheck.sh"
systemctl daemon-reload
systemctl enable vibe-trading-cn.service vibe-trading-cn-healthcheck.timer

# ─── 8. 启动 ───
echo "[install] 8/8 启动服务（首次需先填 secrets.env）"
echo "[install] 完成！接下来："
echo "  1) sudo vi ${SECRETS_DIR}/secrets.env  # 填 API 密钥"
echo "  2) sudo systemctl start vibe-trading-cn.service"
echo "  3) sudo systemctl status vibe-trading-cn.service"
echo "  4) journalctl -u vibe-trading-cn -f  # 看日志"
echo "  5) sudo systemctl list-timers vibe-trading-cn-healthcheck.timer  # 健康检查"
