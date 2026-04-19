from fastapi import FastAPI

app = FastAPI()

@app.post("/a2a")
def task(req: dict):
    name = req.get("input", "world")
    return {"artifacts": [{"text": f"Hello, {name}!"}]}
