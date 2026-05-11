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

# ── 检查 .env ─────────────────────────────────────
if [ ! -f ".env" ]; then
  echo -e "${YELLOW}⚠️  未找到 .env 文件，请创建并配置 ANTHROPIC_AUTH_TOKEN / MODEL_ID${NC}"
  echo "示例 .env:"
  echo "  ANTHROPIC_AUTH_TOKEN=sk-ant-..."
  echo "  MODEL_ID=claude-sonnet-4-20250514"
  echo "  # 可选: ANTHROPIC_BASE_URL=https://..."
fi

# ── 后端 ──────────────────────────────────────────
echo -e "${GREEN}▶ 启动后端 (http://localhost:5000)${NC}"
stop_existing_port 5000
if command -v conda &>/dev/null && conda env list | grep -q "code_agent"; then
  conda run -n code_agent python api_server.py &
else
  python3 api_server.py &
fi
BACKEND_PID=$!
echo "  后端 PID: $BACKEND_PID"

# 等待后端就绪
echo -n "  等待后端就绪 "
for i in $(seq 1 15); do
  sleep 1
  echo -n "."
  if curl -sf http://localhost:5000/api/status >/dev/null 2>&1; then
    echo -e " ${GREEN}OK${NC}"
    break
  fi
done

# ── 前端 ──────────────────────────────────────────
echo -e "${GREEN}▶ 启动前端 (http://localhost:5173)${NC}"
stop_existing_port 5173
cd frontend
npm run dev &
FRONTEND_PID=$!
echo "  前端 PID: $FRONTEND_PID"

echo "────────────────────────────────────────────"
echo -e "${GREEN}✅ 服务已启动${NC}"
echo "  前端:  http://localhost:5173"
echo "  后端:  http://localhost:5000"
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
