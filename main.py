"""Local launcher: `python main.py` starts the API (serves frontend/dist if built)."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
