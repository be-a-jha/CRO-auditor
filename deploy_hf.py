#!/usr/bin/env python3
"""Deploy the web app to a Hugging Face Docker Space and get a shareable link.

  pip install huggingface_hub
  hf auth login                      # paste a token with "write" access from huggingface.co/settings/tokens
  python deploy_hf.py cro-auditor --key --password "pick-a-password"

--key        store ANTHROPIC_API_KEY (from your .env) as a private secret of the Space
--password   store APP_PASSWORD: only people you give it to can start audits (anyone can view sample reports)
Run it again after any change to redeploy. Your .env, runs (out/) and caches are never uploaded.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_FILES = ["README.md", "Dockerfile", ".dockerignore", "requirements.txt", "app.py", "run.py",
             "cro/*", "ui/*", "sample_output/*"]
IGNORE = ["*/__pycache__/*", "*.pyc", "*.env", ".env.*"]


def main():
    ap = argparse.ArgumentParser(description="Deploy to a Hugging Face Space (Docker).")
    ap.add_argument("space", help="Space name (e.g. cro-auditor) or owner/name")
    ap.add_argument("--key", action="store_true", help="store ANTHROPIC_API_KEY from .env as a Space secret")
    ap.add_argument("--password", default="", help="store APP_PASSWORD as a Space secret (recommended)")
    ap.add_argument("--private", action="store_true", help="make the Space private (only you can open it)")
    a = ap.parse_args()

    from huggingface_hub import HfApi
    api = HfApi()
    try:
        me = api.whoami()["name"]
    except Exception:
        sys.exit("Not logged in. Run `hf auth login` (or `huggingface-cli login`) with a write token first.")
    repo_id = a.space if "/" in a.space else f"{me}/{a.space}"

    api.create_repo(repo_id, repo_type="space", space_sdk="docker", private=a.private, exist_ok=True)
    if a.key:
        from run import load_env
        load_env(str(ROOT / ".env"))
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            sys.exit("No ANTHROPIC_API_KEY in .env or the environment.")
        api.add_space_secret(repo_id, "ANTHROPIC_API_KEY", key)
        print("ANTHROPIC_API_KEY stored as a private Space secret.")
    if a.password:
        api.add_space_secret(repo_id, "APP_PASSWORD", a.password)
        print("APP_PASSWORD stored as a private Space secret.")

    print(f"Uploading app files and bundled sample reports to {repo_id} ...")
    api.upload_folder(folder_path=str(ROOT), repo_id=repo_id, repo_type="space", allow_patterns=APP_FILES,
                      ignore_patterns=IGNORE,
                      commit_message="Deploy CRO auditor")
    owner, name = repo_id.split("/")
    host = re.sub(r"[^a-z0-9-]", "-", f"{owner}-{name}".lower())
    print(f"\nDone. The Space builds for a few minutes (it installs Chrome), then the app is live at:\n"
          f"  https://{host}.hf.space\n"
          f"Build logs and settings: https://huggingface.co/spaces/{repo_id}")
    if not a.password:
        print("Set APP_PASSWORD in Space settings before enabling live audits.")


if __name__ == "__main__":
    main()
