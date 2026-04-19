#!/bin/bash
# Scripted "demo" of `agentgate deploy`. Output only — no real deploy.
# Run by VHS to record a predictable, brand-accurate GIF for PH/HN.
# The sleeps pace the reveal; total runtime ≈ 14 seconds.

set -e

GREEN='\033[32m'
BLUE='\033[34m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

echo '$ pip install agentgatesh'
sleep 0.6
printf "${DIM}Successfully installed agentgatesh-0.1.0${RESET}\n\n"
sleep 0.6

echo '$ cat agent.py'
sleep 0.3
cat <<'PY'
from fastapi import FastAPI
app = FastAPI()

@app.post("/a2a")
def task(req: dict):
    name = req.get("input", "world")
    return {"artifacts": [{"text": f"Hello, {name}!"}]}
PY
sleep 1.4

echo ''
echo '$ agentgate deploy ./my-agent --name hello --price 0.05'
sleep 0.5
printf "${GREEN}\xE2\x9C\x93${RESET} Packaging agent...\n"
sleep 0.45
printf "${GREEN}\xE2\x9C\x93${RESET} Uploading to agentgate.sh...\n"
sleep 0.5
printf "${GREEN}\xE2\x9C\x93${RESET} Building Docker image...\n"
sleep 0.7
printf "${GREEN}\xE2\x9C\x93${RESET} Starting container on port 9103...\n"
sleep 0.45
printf "${BOLD}${GREEN}\xE2\x9C\x93 Agent is live!${RESET}\n\n"
printf "  ${BOLD}Agent ID:${RESET}  a71a462b-a652-4bc7-bdaf-4d0cb74c02a7\n"
printf "  ${BOLD}URL:${RESET}       ${BLUE}https://agentgate.sh/agents/hello/task${RESET}\n"
printf "  ${BOLD}Card:${RESET}      ${BLUE}https://agentgate.sh/agents/hello/card${RESET}\n"
printf "  ${BOLD}Price:${RESET}     \$0.05 per task\n\n"
sleep 1.7

echo '$ curl -s -X POST https://agentgate.sh/agents/hello/task \'
echo '    -H "Content-Type: application/json" -d '"'"'{"input":"world"}'"'"
sleep 0.7
printf "${DIM}{\"artifacts\":[{\"text\":\"Hello, world!\"}]}${RESET}\n"
sleep 2.0
