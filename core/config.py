from pathlib import Path
import os
from dotenv import load_dotenv

# Project root (backend folder)
BASE_DIR = Path(__file__).resolve().parent.parent

# .env should be inside backend/
ENV_PATH = BASE_DIR / ".env"

# Load env
load_dotenv(dotenv_path=ENV_PATH)

# Variables
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Debug (optional)
print("\n--- Configuration Debug ---")
print(f"BASE_DIR: {BASE_DIR}")
print(f"ENV_PATH: {ENV_PATH}")
print(f"GROQ_API_KEY Loaded: {'✅ Yes' if GROQ_API_KEY else '❌ No'}")
print("---------------------------\n")

# Validation
if not GROQ_API_KEY:
    raise RuntimeError(f"GROQ_API_KEY not loaded. Expected at: {ENV_PATH}")