#!/usr/bin/env bash
# start.sh — 同时启动 Flask 后端与 Vite 前端开发服务器
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 颜色 ─────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo -e "${GREEN}🚀 Code Agent 启动脚本${NC}"
echo "────────────────────────────────────────────"

stop_existing_port() {
  local port="$1"
  local existing_pids
  existing_pids="$(lsof -ti :$port 2>/dev/null || true)"
  if [ -n "$existing_pids" ]; then
    echo -e "${YELLOW}⚠️  检测到已有进程占用 ${port} 端口，正在清理...${NC}"
    kill $existing_pids 2>/dev/null || true
    for _ in $(seq 1 10); do
      if ! lsof -ti :$port >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
  fi
}

# ── 检查 .env（可选）──────────────────────────────
# 模型配置默认使用本地 Ollama（见 components/config.py），
# 需要切换其他服务时再创建 .env 覆盖以下变量：
if [ ! -f ".env" ]; then
  echo -e "${YELLOW}ℹ️  未找到 .env，使用默认配置: 本地 Ollama (http://localhost:11434)${NC}"
  echo "  如需配置其他模型，请复制 .env.example 为 .env，修改 LLM_PROVIDER 和对应密钥。"
fi

# ── 读取公共服务配置 ──────────────────────────────
# 先选定后端实际使用的 Python，再由 components.config 从项目根 .env
# 读取公共服务参数。不要 source .env，避免把模型密钥导入 shell。
if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
  PYTHON_COMMAND=("$SCRIPT_DIR/.venv/bin/python")
elif command -v conda &>/dev/null && conda env list | grep -q "code_agent"; then
  PYTHON_COMMAND=(conda run -n code_agent python)
else
  PYTHON_COMMAND=(python3)
fi

SERVER_CONFIG_OUTPUT="$("${PYTHON_COMMAND[@]}" - <<'PY'
from components.config import Config, load_environment

load_environment()

server = Config()
host = str(server.host)
port = int(server.port)
frontend_port = int(server.frontend_port)
healthcheck_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
if ":" in healthcheck_host and not healthcheck_host.startswith("["):
    healthcheck_host = f"[{healthcheck_host}]"
healthcheck_url = f"http://{healthcheck_host}:{port}/api/status"

print("\t".join((host, str(port), str(frontend_port), healthcheck_url)))
PY
)"
IFS=$'\t' read -r API_HOST API_PORT FRONTEND_PORT HEALTHCHECK_URL <<< "$SERVER_CONFIG_OUTPUT"

if [ -z "$API_HOST" ] || [ -z "$API_PORT" ] || [ -z "$FRONTEND_PORT" ] || [ -z "$HEALTHCHECK_URL" ]; then
  echo -e "${RED}❌ 无法读取公共服务配置${NC}"
  exit 1
fi

# ── 后端 ──────────────────────────────────────────
echo -e "${GREEN}▶ 启动后端 (${HEALTHCHECK_URL%/api/status})${NC}"
stop_existing_port "$API_PORT"
"${PYTHON_COMMAND[@]}" api_server.py &
BACKEND_PID=$!
echo "  后端 PID: $BACKEND_PID"

# 等待后端就绪
echo -n "  等待后端就绪 "
BACKEND_READY=false
for i in $(seq 1 30); do
  sleep 1
  echo -n "."
  if curl -sf "$HEALTHCHECK_URL" >/dev/null 2>&1; then
    echo -e " ${GREEN}OK${NC}"
    BACKEND_READY=true
    break
  fi
done

if [ "$BACKEND_READY" != true ]; then
  echo -e "\n${RED}❌ 后端在 30 秒内未就绪，未启动前端。请检查上方 Python 错误信息。${NC}"
  kill "$BACKEND_PID" 2>/dev/null || true
  exit 1
fi

# ── 前端 ──────────────────────────────────────────
echo -e "${GREEN}▶ 启动前端 (http://localhost:${FRONTEND_PORT})${NC}"
stop_existing_port "$FRONTEND_PORT"
cd frontend
npm run dev &
FRONTEND_PID=$!
echo "  前端 PID: $FRONTEND_PID"

echo "────────────────────────────────────────────"
echo -e "${GREEN}✅ 服务已启动${NC}"
echo "  前端:  http://localhost:${FRONTEND_PORT}"
echo "  后端:  ${HEALTHCHECK_URL%/api/status}"
echo ""
echo "按 Ctrl+C 停止所有服务"

# ── 退出时清理 ────────────────────────────────────
cleanup() {
  echo -e "\n${YELLOW}⏹ 停止服务...${NC}"
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM

wait
