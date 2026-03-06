"""Calc Agent — A simple calculator A2A-compatible agent.

Supports basic math: "2 + 3", "10 * 5", "100 / 4", "7 - 2".
Run with: uvicorn agent:app --port 9001
"""

import re

from fastapi import FastAPI

app = FastAPI(title="Calc Agent")


def evaluate(expr: str) -> str:
    """Safely evaluate a simple arithmetic expression."""
    expr = expr.strip()
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*([+\-*/])\s*(-?\d+(?:\.\d+)?)", expr)
    if not match:
        return f"Cannot parse: '{expr}'. Use format: '2 + 3'"

    a, op, b = float(match.group(1)), match.group(2), float(match.group(3))
    if op == "+":
        result = a + b
    elif op == "-":
        result = a - b
    elif op == "*":
        result = a * b
    elif op == "/":
        if b == 0:
            return "Error: division by zero"
        result = a / b

    # Show as int if whole number
    if result == int(result):
        return str(int(result))
    return str(result)


@app.post("/a2a")
async def handle_task(request: dict):
    """A2A task handler — evaluates simple math expressions."""
    message = request.get("message", {})
    parts = message.get("parts", [])
    text = parts[0].get("text", "") if parts else ""

    answer = evaluate(text)

    return {
        "id": request.get("id", "task-1"),
        "status": {"state": "completed"},
        "artifacts": [
            {
                "parts": [{"type": "text", "text": answer}],
            }
        ],
    }


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "calc-agent", "version": "1.0.0"}
