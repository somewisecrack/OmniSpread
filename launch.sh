#!/bin/bash
# OmniSpread Launcher — headless (no Terminal window opens)

PROJECT_DIR="/Users/rahulgirishkumar/PROJECTS/omnispread"
LOGS="$PROJECT_DIR/logs"

mkdir -p "$LOGS"

echo "=== OmniSpread Launch $(date) ===" >> "$LOGS/launcher.log"

# Set up PATH for Homebrew binaries (npm, node)
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"
export ARCHPREFERENCE=arm64

# Auto-correct architecture on Apple Silicon
CURRENT_ARCH=$(uname -m)
if [ "$CURRENT_ARCH" = "x86_64" ]; then
    if /usr/bin/arch -arm64 /usr/bin/true 2>/dev/null; then
        exec /usr/bin/arch -arm64 "$0" "$@"
    fi
fi

# Check if already running
if lsof -tiTCP:8000 -sTCP:LISTEN >/dev/null 2>&1 && lsof -tiTCP:3000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Already running, opening browser" >> "$LOGS/launcher.log"
    open "http://localhost:3000"
    exit 0
fi

# Kill old processes
lsof -tiTCP:8000 -sTCP:LISTEN | xargs kill -9 2>/dev/null
lsof -tiTCP:3000 -sTCP:LISTEN | xargs kill -9 2>/dev/null

# Clear Next.js cache
rm -rf "$PROJECT_DIR/frontend/.next/cache" 2>/dev/null

sleep 1

# Start backend
cd "$PROJECT_DIR/backend"
echo "Starting backend..." >> "$LOGS/launcher.log"

PYTHON_BIN="python3"
if [ -x "$PROJECT_DIR/backend/.venv/bin/python" ]; then
    PYTHON_BIN="$PROJECT_DIR/backend/.venv/bin/python"
elif ! "$PYTHON_BIN" -c "import uvicorn" >/dev/null 2>&1; then
    for candidate in \
        "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3" \
        "/opt/homebrew/bin/python3" \
        "/usr/local/bin/python3" \
        "/usr/bin/python3"; do
        if [ -x "$candidate" ] && "$candidate" -c "import uvicorn" >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
fi

echo "Backend python: $PYTHON_BIN" >> "$LOGS/launcher.log"
nohup "$PYTHON_BIN" -m uvicorn main:app --host 0.0.0.0 --port 8000 >> "$LOGS/backend.log" 2>&1 &
disown

sleep 2

# Start frontend
cd "$PROJECT_DIR/frontend"
echo "Starting frontend..." >> "$LOGS/launcher.log"
nohup npm run dev -- --port 3000 >> "$LOGS/frontend.log" 2>&1 &
disown

sleep 4

echo "Opening browser..." >> "$LOGS/launcher.log"
open "http://localhost:3000"
