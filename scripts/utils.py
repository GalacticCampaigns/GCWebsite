# ChronicleForge/scripts/utils.py

import os
import re
import json
import requests
import time
import shutil
import subprocess
from dataclasses import is_dataclass, asdict

# --- 1. DYNAMIC PATH RESOLUTION (System Agnostic) ---
# Automatically finds the root of the ChronicleForge directory
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
# PROJECT_ROOT is /ChronicleForge
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR) 
# FORGE_BASE is /GalCam-stack
FORGE_BASE = os.path.dirname(PROJECT_ROOT)

# The Vault: Persistent storage shared across environments
VAULT_DIR = os.path.join(FORGE_BASE, "vault")
# The Master Registry: Single Source of Truth
LOCAL_REGISTRY = os.path.join(PROJECT_ROOT, "assets", "campaign-registry.json")

# Transient directories for worker processes
TEMP_GIT_DIR = os.path.join(PROJECT_ROOT, "temp_git_sync")
TEMP_EXPORT_DIR = os.path.join(PROJECT_ROOT, "temp_worker")

# --- 2. ENVIRONMENT & PERMISSIONS (Pillar 4) ---

def get_env_type():
    """Detects if the Forge is running in a Cloud (Codespace/Actions) or Local Node (Pi)."""
    if os.getenv("CODESPACES") == "true" or os.getenv("GITHUB_ACTIONS") == "true":
        return "CLOUD"
    return "LOCAL"

def fix_permissions(target_path):
    """
    Reclaims file ownership from Docker/Root processes. 
    Automatically skips in Cloud environments where root-conflicts don't exist.
    """
    if get_env_type() == "CLOUD":
        return # Pillar 4: Skip in Codespaces
    
    try:
        import getpass
        user = getpass.getuser()
        # Only run sudo if necessary to keep the script system-agnostic
        subprocess.run(["sudo", "chown", "-R", f"{user}:{user}", target_path], check=True)
    except Exception as e:
        print(f"      [System] Warning: Could not fix permissions for {target_path}: {e}")

# --- 3. VAULT PATH RESOLUTION ---

def get_repo_folder_name(repo_path_str):
    """Extracts directory name from GitHub path (e.g., 'campaigns' from 'user/campaigns')."""
    if not repo_path_str: return "unknown_repo"
    return repo_path_str.split("/")[-1].replace(".git", "")

def resolve_vault_path(repo_folder, data_path, sub_path=""):
    """Forge Logic: Constructs absolute paths within the Vault Hierarchy."""
    base = os.path.join(VAULT_DIR, repo_folder, data_path.strip("/"))
    if sub_path:
        return os.path.join(base, sub_path.strip("/"))
    return base

# --- 4. FORENSIC & IDENTITY HELPERS ---

def pad_timestamp(ts: str) -> str:
    """Standardizes timestamps to 6-digit microsecond precision for sorting fidelity."""
    if not ts or not isinstance(ts, str): return ts
    try:
        # Match UTC or Offset suffix
        match = re.search(r'([+-]\d{2}:\d{2}|Z)$', ts)
        if not match: return ts
        offset = match.group(0)
        dt_part = ts[:-len(offset)]
        if '.' in dt_part:
            time_main, fraction = dt_part.split('.')
            return f"{time_main}.{fraction.ljust(6, '0')[:6]}{offset}"
        return f"{dt_part}.000000{offset}"
    except Exception: return ts

def extract_pure_hash(url_or_path: str) -> str | None:
    """Surgically extracts 32-char hex hashes for suffix-free asset storage."""
    if not url_or_path: return None
    clean_path = url_or_path.split('?')[0]
    filename = clean_path.split('/')[-1]
    match = re.search(r'([a-f0-9]{32}|[a-f0-9]{16})', filename.lower())
    return match.group(1) if match else None

def to_snowflake(val):
    """Ensures Discord IDs are treated as Strings to prevent JS precision loss."""
    if val is None or val == "" or str(val).lower() == "none":
        return None
    return str(val)

# --- 5. DATA I/O & SERIALIZATION ---

def download_master_registry(url):
    """Fetches the Single Source of Truth from the remote repository."""
    print(f"      [System] Syncing Brain: Fetching master registry...")
    try:
        # Cache busting ensures the Forge never works on stale metadata
        response = requests.get(f"{url}?t={int(time.time())}", timeout=15)
        if response.status_code == 200:
            data = response.json()
            os.makedirs(os.path.dirname(LOCAL_REGISTRY), exist_ok=True)
            with open(LOCAL_REGISTRY, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return data
        return f"Registry Pull Error: HTTP {response.status_code}"
    except Exception as e:
        return f"Registry Pull Fatal: {str(e)}"

def save_registry(data):
    """Saves the registry in a git-friendly 'Squashed-Object' format."""
    fix_permissions(LOCAL_REGISTRY)
    output = "{\n"
    if "activeCampaign" in data:
        output += f'  "activeCampaign": "{data["activeCampaign"]}",\n'
    output += '  "campaigns": {\n'
    camp_keys = list(data.get("campaigns", {}).keys())
    for i, camp_key in enumerate(camp_keys):
        camp = data["campaigns"][camp_key]
        output += f'    "{camp_key}": {{\n'
        for k, v in camp.items():
            if k == "logs": continue
            output += f'      "{k}": {json.dumps(v, ensure_ascii=False)},\n'
        output += '      "logs": [\n'
        logs = camp.get("logs", [])
        for j, log in enumerate(logs):
            comma = "," if j < len(logs) - 1 else ""
            output += f'        {json.dumps(log, ensure_ascii=False)}{comma}\n'
        output += '      ]\n'
        output += f'    }}{"," if i < len(camp_keys) - 1 else ""}\n'
    output += "  }\n}"
    with open(LOCAL_REGISTRY, 'w', encoding='utf-8') as f:
        f.write(output)

def clear_temp_directory(temp_path):
    """Cleans transient worker directories to prevent disk bloat."""
    if os.path.exists(temp_path):
        shutil.rmtree(temp_path, ignore_errors=True)
    os.makedirs(temp_path, exist_ok=True)

# --- 6. FORMATTING & NAMING ---

def slugify_filename(name):
    """Normalizes channel names into OS-safe narrative slugs."""
    name = name.lower().replace(".json", "")
    name = name.replace(" ", "-").replace("_", "-")
    clean = re.sub(r'[^a-z0-9\-]', '', name)
    clean = re.sub(r'-+', '-', clean).strip("-")
    return f"{clean}.json"

def format_pretty_title(name):
    """Reverses slugs for human-readable report formatting."""
    name = name.replace(".json", "")
    return name.replace("-", " ").replace("_", " ").title()

def sanitize_resource_name(name, limit=100):
    """Enforces the 100-character limit to ensure OS compatibility (Pillar 3)."""
    if not name: return "unknown"
    base, ext = os.path.splitext(name)
    clean_base = re.sub(r'[^a-zA-Z0-9\.\-\_]', '_', base)
    return f"{clean_base[:limit]}{ext}"