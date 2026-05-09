# ChronicleForge/scripts/pull_logs.py

import os
import json
import argparse
from datetime import datetime
from dotenv import load_dotenv

from .utils import (
    clear_temp_directory,
    download_master_registry,
    get_repo_folder_name,
    resolve_vault_path,
    LOCAL_REGISTRY,
    TEMP_EXPORT_DIR
)

from .navigator import Navigator
from .miner import Miner
from . import scanner
from . import git_sync 

# Load environment (Prioritizes System Env Vars, falls back to .env)
load_dotenv()
REGISTRY_URL = os.getenv("REGISTRY_URL")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

def run_orchestrator(nav, token, dry_run=False, force_all=False, debug=False):
    """
    System-Agnostic Forge Orchestrator.
    Manages the lifecycle of the narrative from Discord discovery to Git vaulting.
    """
    if debug:
        git_sync.DEBUG_MODE = True
        print("      [System] Debug mode enabled.")
    print("\n" + "="*60)
    mode_text = "DRY RUN" if dry_run else "LIVE"
    env_text = "CLOUD" if os.getenv("CODESPACES") == "true" else "LOCAL NODE"
    print(f"⚒️  CHRONICLE FORGE ({mode_text} | {env_text}): {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("="*60)

    # 1. Initialize Engine
    miner = Miner(token, dry_run=dry_run)
    clear_temp_directory(TEMP_EXPORT_DIR)

    # 2. Group Campaigns by Repository for atomic Git operations
    campaigns = nav.registry.get("campaigns", {})
    repo_groups = {}
    for camp_key, camp_data in campaigns.items():
        repo_path = camp_data.get("repository")
        if not repo_path:
            continue
        if repo_path not in repo_groups:
            repo_groups[repo_path] = []
        repo_groups[repo_path].append((camp_key, camp_data))

    # 3. Process Repository Clusters
    for repo_path, camp_list in repo_groups.items():
        repo_folder = get_repo_folder_name(repo_path)
        # Inherit branch from first campaign in group
        branch = camp_list[0][1].get("branch", "main")
        
        print(f"\n📦 Repository: {repo_folder} ({repo_path})")
        
        try:
            # --- STAGE A: SYNC VAULT ---
            sync_res = git_sync.pull_latest(repo_folder, repo_path, branch=branch)
            if isinstance(sync_res, str):
                nav.update_report("System", "❌ GIT PULL FAILED", f"{repo_folder}: {sync_res}")
                continue 

            for camp_key, camp_data in camp_list:
                camp_name = camp_data.get('name', camp_key)
                print(f"  📂 Campaign: {camp_name}")

                # --- STAGE B: SCOUT ---
                # Sentinel Pass handles Legacy Lock detection and Chapter Discovery
                scanner.get_new_discoveries(camp_data, token, nav)

                # --- STAGE C: MINE & SMELT ---
                # Only process chapters that aren't Legacy Locked
                active_logs = [l for l in camp_data.get("logs", []) 
                              if l.get("isActive", True) and l.get("syncStatus") != "legacy"]
                
                for log in active_logs:
                    needs_pull, latest_api_id = nav.should_sync(log, token, force_all)
                    
                    if needs_pull:
                        print(f"      [Forge] Processing {log.get('title')}...")
                        
                        # Miner handles Total CDN Mirroring (Pillar 3)
                        sync_status = miner.sync_chapter_family(
                            repo_folder, 
                            log, 
                            camp_data, 
                            full_audit=force_all
                        )

                        # Handle API-level failures bubbled from the Miner
                        if isinstance(sync_status, dict) and "error" in sync_status:
                            nav.update_report(camp_name, "⚠️ MINER WARNING", 
                                             f"{log['title']}: {sync_status['error']}")
                        elif sync_status:
                            # --- FORENSICS & REFINEMENT ---
                            # Re-verify the Gold file to update message counts and NSFW status
                            json_rel = camp_data.get("paths", {}).get("json", "json/")
                            data_path = camp_data.get("dataPath", "./")
                            
                            gold_path = resolve_vault_path(
                                repo_folder, 
                                data_path, 
                                sub_path=os.path.join(json_rel, log["fileName"])
                            )

                            if os.path.exists(gold_path):
                                with open(gold_path, 'r', encoding='utf-8') as gf:
                                    try:
                                        final_msgs = json.load(gf)
                                        forensics = nav.analyze_and_merge(log, final_msgs)
                                        
                                        # --- INCREMENTAL DETECTION ---
                                        # 1. Capture the count from memory BEFORE applying forensics
                                        old_count = log.get("messageCount", 0)
                                        new_total = forensics["grand_total"]
                                        added_delta = new_total - old_count

                                        # 2. Update Registry memory with new forensics
                                        nav.apply_forensics_to_registry(
                                            log, forensics, 
                                            api_id_stamp=latest_api_id
                                        )

                                        # 3. Report the Delta (New or Potential)
                                        if added_delta > 0:
                                            nav.update_report(
                                                camp_name, "Refined", log.get("title"), 
                                                count=new_total, 
                                                nsfw_count=forensics["grand_nsfw"],
                                                added=added_delta
                                            )
                                    except json.JSONDecodeError:
                                        nav.update_report(camp_name, "💥 FILE ERROR", f"Corrupt JSON at {log['fileName']}")

            # --- STAGE E: DEEP FREEZE (GIT PUSH) ---
            if git_sync.DEBUG_MODE:
                print(f"      [Debug] Entering Stage E: Deep Freeze for {repo_folder}")
            if not dry_run:
                push_res = git_sync.push_updates(repo_folder)
                if isinstance(push_res, str):
                    nav.update_report("System", "❌ GIT PUSH FAILED", f"{repo_folder}: {push_res}")
            else:
                print(f"      🛡️ [Dry Run] Staging complete for {repo_folder}. Skipping Push.")

        except Exception as e:
            error_detail = f"Internal Failure: {str(e)}"
            print(f"      💥 [Repo Error] {repo_folder}: {e}")
            nav.update_report("System", "💥 CRITICAL ERROR", f"{repo_folder}: {error_detail}")
            import traceback
            traceback.print_exc()

    # 4. Global Finalization (Brain Sync & Report Dispatch)
    clear_temp_directory(TEMP_EXPORT_DIR)
    print("\n🏁 Forge Cycle Complete. Synchronizing Registry and dispatching reports...")
    nav.finalize_run(is_dry_run=dry_run)

if __name__ == "__main__":
    # Fetch the Single Source of Truth
    if not REGISTRY_URL:
        print(f"      [System] REGISTRY_URL not set. Falling back to local registry...")
        if os.path.exists(LOCAL_REGISTRY):
            try:
                with open(LOCAL_REGISTRY, 'r', encoding='utf-8') as f:
                    registry_data = json.load(f)
            except Exception as e:
                print(f"❌ CRITICAL FAILURE: Failed to read local registry. {e}")
                registry_data = str(e)
        else:
            print(f"❌ CRITICAL FAILURE: Local registry not found at {LOCAL_REGISTRY}")
            registry_data = "Local registry not found"
    else:
        registry_data = download_master_registry(REGISTRY_URL)
    
    if isinstance(registry_data, str):
        print(f"❌ CRITICAL FAILURE: Registry Load Error. {registry_data}")
    else:
        nav_instance = Navigator(registry_data)

        parser = argparse.ArgumentParser(description="GalCam Chronicle Forge Orchestrator")
        parser.add_argument("-d", "--dry-run", action="store_true", help="Refine data locally without pushing to GitHub")
        parser.add_argument("-f", "--force", dest="force_all", action="store_true", help="Force full audit of all history") 
        parser.add_argument("-v", "--verbose", "--debug", dest="debug", action="store_true", help="Enable verbose debug logging")
        args = parser.parse_args()
        
        run_orchestrator(nav_instance, DISCORD_TOKEN, **vars(args))