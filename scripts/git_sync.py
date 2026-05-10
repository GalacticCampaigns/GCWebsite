# ChronicleForge/scripts/git_sync.py

import os
import json
import shutil
import subprocess
from datetime import datetime
from .utils import VAULT_DIR, PROJECT_ROOT, TEMP_GIT_DIR, get_env_type
from .config import FORGE_CONFIG

DEBUG_MODE = False

def run_git(args, cwd):
    """
    Hardened Git wrapper with timeout protection and detailed error capture.
    Returns subprocess.CompletedProcess on success, or a descriptive string on error.
    """
    if DEBUG_MODE:
        print(f"[Git Debug] Running: git {' '.join(args)} in {cwd}")
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60 # Protection against SSH hangs or network lag
        )
        if res.returncode != 0:
            return f"Git Error ({res.returncode}): {res.stderr.strip()}"
        return res
    except subprocess.TimeoutExpired:
        return "Git Timeout: Command exceeded 60s."
    except Exception as e:
        return f"Git Fatal: {str(e)}"

def is_self_repo(repo_path_str):
    """
    Mono-Repo Check: Determines if the target repo is the current Forge fork.
    If WEBSITE_REPO is 'self' or matches the config, we skip external cloning.
    """
    config_repo = FORGE_CONFIG.website_repo
    if not repo_path_str or not config_repo:
        return False
    return repo_path_str.lower() == "self" or repo_path_str.lower() == config_repo.lower()

def get_repo_url(repo_path_str):
    """
    Pillar 4: Multi-Identity Abstraction. 
    Constructs the SSH URL using aliases for specific owners.
    """
    env = get_env_type()
    if DEBUG_MODE:
        print(f"      [Git Debug] Environment detected: {env}")
        
    if env == "CLOUD":
        return f"git@github.com:{repo_path_str}.git"
    
    owner = repo_path_str.split("/")[0] if "/" in repo_path_str else ""
    ssh_host = FORGE_CONFIG.get_git_alias(owner)
    return f"git@{ssh_host}:{repo_path_str}.git"

def pull_latest(repo_folder, repo_path_str, branch="main"):
    """
    Vault Sync: Ensures the local mirror matches the 'Gold Standard' remote.
    """
    repo_local_path = os.path.join(VAULT_DIR, repo_folder)
    
    if DEBUG_MODE:
        print(f"      [Git Debug] Resolving vault path for {repo_folder}: {repo_local_path}")
    
    # If the campaign data is stored inside this same mono-repo, skip external pull
    if is_self_repo(repo_path_str):
        print(f"      [Git] campaign '{repo_folder}' is part of Mono-Repo. Using local state.")
        return True

    repo_url = get_repo_url(repo_path_str)

    if not os.path.exists(repo_local_path):
        print(f"      [Git] Initializing Vault mirror for {repo_folder}...")
        if DEBUG_MODE:
            print(f"      [Git Debug] Directory does not exist. Cloning from {repo_url}")
        os.makedirs(repo_local_path, exist_ok=True)
        res = run_git(["clone", "-b", branch, repo_url, "."], cwd=repo_local_path)
        return True if not isinstance(res, str) else res

    # Establish Gold Standard
    run_git(["fetch", "--all"], cwd=repo_local_path)
    res = run_git(["reset", "--hard", f"origin/{branch}"], cwd=repo_local_path)
    
    print(f"      🔄 [Git] Vault/{repo_folder} synchronized with origin/{branch}.")
    return True

def push_updates(repo_folder, branch="main"):
    """
    Deep Freeze: Commits and pushes narrative data to GitHub.
    """
    repo_local_path = os.path.join(VAULT_DIR, repo_folder)
    
    # Check if this campaign is a local directory or a linked repo
    if not os.path.exists(os.path.join(repo_local_path, ".git")):
        # If it's a subfolder of the Forge (Mono-Repo), we let the final finalize_run handle the push
        print(f"      ℹ️ [Git] {repo_folder} is a local sub-path. Staging for final mono-push.")
        run_git(["add", "."], cwd=PROJECT_ROOT)
        return True

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    run_git(["add", "."], cwd=repo_local_path)
    
    # Porcelain Check
    status = run_git(["status", "--porcelain"], cwd=repo_local_path)
    if isinstance(status, str) or not status.stdout.strip():
        if DEBUG_MODE:
            if isinstance(status, str):
                print(f"      [Git Debug] Status failed: {status}")
            else:
                print(f"      [Git Debug] No changes detected by git status. Skipping push.")
        return True

    if DEBUG_MODE:
        print(f"      [Git Debug] Changes detected:\n{status.stdout}")

    # Ensure user identity is set (critical for GitHub Actions)
    run_git(["config", "user.name", "Chronicle Forge Bot"], cwd=repo_local_path)
    run_git(["config", "user.email", "noreply@noreply.com"], cwd=repo_local_path)

    commit = run_git(["commit", "-m", f"chore(forge): deep freeze sync {ts}"], cwd=repo_local_path)
    if isinstance(commit, str):
        if DEBUG_MODE:
            print(f"      [Git Debug] Commit failed: {commit}")
        return commit

    push = run_git(["push", "origin", branch], cwd=repo_local_path)
    if DEBUG_MODE:
        if isinstance(push, str):
            print(f"      [Git Debug] Push failed: {push}")
        else:
            print(f"      [Git Debug] Push successful.\n{push.stdout}")
    return True if not isinstance(push, str) else push

def sync_website_registry(repo_path_str, updated_registry_dict, branch="main"):
    """
    Brain Sync: Updates the master registry.
    In Mono-Repo mode, it updates the local file and pushes the whole project root.
    """
    # --- MONO-REPO (SELF-SYNC) LOGIC ---
    if is_self_repo(repo_path_str):
        print(f"      [Git] Mono-Repo detected. Updating local registry...")
        
        # In Mono-Repo, the registry is in PROJECT_ROOT (e.g. ChronicleForge/campaign-registry.json)
        target_file = os.path.join(PROJECT_ROOT, "campaign-registry.json")
        with open(target_file, 'w', encoding='utf-8') as f:
            json.dump(updated_registry_dict, f, indent=2, ensure_ascii=False)

        # Final push for the entire project state
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        run_git(["add", "."], cwd=PROJECT_ROOT)
        
        status = run_git(["status", "--porcelain"], cwd=PROJECT_ROOT)
        if isinstance(status, str) or not status.stdout.strip():
            print("      ℹ️ [Git] Mono-Repo is already current.")
            return True

        # Ensure user identity is set (critical for GitHub Actions)
        run_git(["config", "user.name", "Chronicle Forge Bot"], cwd=PROJECT_ROOT)
        run_git(["config", "user.email", "noreply@noreply.com"], cwd=PROJECT_ROOT)

        commit = run_git(["commit", "-m", f"chore(forge): automated cycle sync {ts}"], cwd=PROJECT_ROOT)
        if isinstance(commit, str):
            if DEBUG_MODE:
                print(f"      [Git Debug] Mono-Repo commit failed: {commit}")
            return commit

        push = run_git(["push", "origin", branch], cwd=PROJECT_ROOT)
        if DEBUG_MODE:
            if isinstance(push, str):
                print(f"      [Git Debug] Mono-Repo push failed: {push}")
            else:
                print(f"      [Git Debug] Mono-Repo push successful.\n{push.stdout}")
        return True if not isinstance(push, str) else push

    # --- MULTI-REPO (EXTERNAL) LOGIC ---
    repo_url = get_repo_url(repo_path_str)
    repo_local_path = os.path.join(TEMP_GIT_DIR, "brain_registry_sync")
    
    if os.path.exists(repo_local_path):
        shutil.rmtree(repo_local_path, ignore_errors=True) 
    os.makedirs(repo_local_path, exist_ok=True)

    clone = run_git(["clone", "--depth", "1", "-b", branch, repo_url, "."], cwd=repo_local_path)
    if isinstance(clone, str): return clone

    # Find registry recursively
    target_file = None
    for root, dirs, files in os.walk(repo_local_path):
        if "campaign-registry.json" in files:
            target_file = os.path.join(root, "campaign-registry.json")
            break
    
    if not target_file: target_file = os.path.join(repo_local_path, "campaign-registry.json")

    with open(target_file, 'w', encoding='utf-8') as f:
        json.dump(updated_registry_dict, f, indent=2, ensure_ascii=False)

    run_git(["add", "."], cwd=repo_local_path)
    run_git(["commit", "-m", "chore(brain): automated registry update"], cwd=repo_local_path)
    push = run_git(["push", "origin", branch], cwd=repo_local_path)
    
    shutil.rmtree(repo_local_path, ignore_errors=True)
    return True if not isinstance(push, str) else push