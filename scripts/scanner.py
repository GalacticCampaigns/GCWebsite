# ChronicleForge/scripts/scanner.py
import re
import requests
import time
from .utils import slugify_filename, format_pretty_title
from .config import FORGE_CONFIG

# Default keywords (to be moved to refinery-config.json in next phase)
DEFAULT_IGNORE = FORGE_CONFIG.get("scout", "ignore_keywords")

def should_auto_ignore(name, custom_keywords=None):
    """Checks if a channel/thread should be ignored based on keywords."""
    keywords = custom_keywords or DEFAULT_IGNORE
    n = name.lower()
    return any(word in n for word in keywords)

def discord_api_get(endpoint, token):
    """Hardened API GET with vocal error reporting for Forge stability."""
    headers = {"Authorization": f"Bot {token}"}
    url = f"https://discord.com/api/v10/{endpoint}"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 5)
            time.sleep(retry_after)
            return discord_api_get(endpoint, token)
        
        if resp.status_code == 200:
            return resp.json()
        
        # Return the status code as part of the error for Sentinel checks
        return {"error_code": resp.status_code, "message": resp.json().get('message', 'Access Denied')}
    
    except Exception as e:
        return {"error": str(e)}

def get_new_discoveries(camp_data, bot_token, nav):
    """
    Orchestrates the Forge Scouting phase:
    1. Legacy Sentinel Pass: Detects deleted/orphaned channels.
    2. Chapter Discovery: Finds new narrative channels.
    3. Thread Discovery: Captures active and archived narrative branches.
    """
    camp_name = camp_data.get('name', 'Unknown Campaign')
    guild_id = str(camp_data.get("guild_id", ""))
    category_id = str(camp_data.get("category_id", ""))
    forum_ids = [str(f) for f in camp_data.get("forum_ids", [])]
    pattern = camp_data.get("auto_scan_pattern", "ch|chapter|prelude")

    if not guild_id:
        nav.update_report(camp_name, "❌ CONFIG ERROR", "Missing guild_id")
        return False

    # --- 1. THE LEGACY SENTINEL PASS ---
    # Ping Discord to see what actually still exists
    all_channels = discord_api_get(f"guilds/{guild_id}/channels", bot_token)
    if isinstance(all_channels, dict) and "error" in all_channels:
        nav.update_report(camp_name, "⚠️ SCANNER FAILURE", all_channels["error"])
        return False
    
    discord_map = {str(c["id"]): c for c in all_channels}

    for log in camp_data.get("logs", []):
        c_id = str(log["channelID"])
        
        # Sentinel: Check for Orphaned/Deleted channels
        if c_id not in discord_map:
            # If it's already legacy, keep quiet
            if log.get("syncStatus") == "legacy":
                continue
                
            # Perform a direct probe to confirm 404 vs 403
            probe = discord_api_get(f"channels/{c_id}", bot_token)
            if isinstance(probe, dict) and "error_code" in probe:
                log["syncStatus"] = "legacy"
                log["isActive"] = False
                log["source_exists"] = False
                nav.update_report(camp_name, "🚫 LEGACY LOCK", f"{log['title']} (Source Deleted/Inaccessible)")
            continue

        # Self-Heal: Ensure Parent ID is correct and schema is current
        live_info = discord_map[c_id]
        log["parentID"] = str(live_info.get("parent_id") or "")
        log.setdefault("isActive", True)
        log.setdefault("syncStatus", "active")
        log.setdefault("last_synced_id", "")
        
        # Migrate legacy isNSFW/nsfwCount fields to dynamic tags list
        legacy_nsfw = log.pop("isNSFW", None)
        log.pop("nsfwCount", None) # Remove legacy count
        tags_list = log.setdefault("tags", [])
        if legacy_nsfw or live_info.get("nsfw", False):
            if "nsfw" not in tags_list:
                tags_list.append("nsfw")

        # Migrate thread legacy isNSFW fields
        for t in log.get("threads", []):
            t_legacy = t.pop("isNSFW", None)
            t.pop("nsfwCount", None)
            t_tags = t.setdefault("tags", [])
            if t_legacy and "nsfw" not in t_tags:
                t_tags.append("nsfw")

    # --- 2. CHAPTER DISCOVERY (Standard & Forums) ---
    existing_logs = {str(log["channelID"]): log for log in camp_data.get("logs", [])}
    archive_all = camp_data.get("archive_all", False)
    
    for ch_id, ch in discord_map.items():
        # Discover in Category
        if str(ch.get("parent_id")) == category_id and ch_id not in existing_logs:
            if ch_id in forum_ids:
                continue
            is_narrative = bool(re.search(pattern, ch.get("name", ""), re.IGNORECASE))
            ignored = should_auto_ignore(ch.get("name", ""))
            
            if archive_all or is_narrative:
                entry = build_registry_entry(ch)
                if ignored or not is_narrative:
                    if archive_all:
                        entry["isActive"] = True
                        entry["visible"] = False
                        nav.update_report(camp_name, "📥 ARCHIVED ONLY", entry['title'])
                    else:
                        entry["isActive"] = False
                        entry["visible"] = False
                        nav.update_report(camp_name, "🚫 AUTO-IGNORED", entry['title'])
                else:
                    entry["isActive"] = True
                    entry["visible"] = True
                    nav.update_report(camp_name, "🚨 NEW CHAPTER", entry['title'])
                camp_data["logs"].append(entry)
                existing_logs[ch_id] = entry

    # --- 3. THREAD DISCOVERY (Active & Archived) ---
    existing_threads = {str(t["threadID"]): t for log in camp_data.get("logs", []) for t in log.get("threads", [])}
    
    # Fetch Active Threads for the whole Guild
    active_threads = discord_api_get(f"guilds/{guild_id}/threads/active", bot_token)
    discovered_threads = active_threads.get("threads", []) if isinstance(active_threads, dict) else []

    # Search specific channels and forums for Archived narrative threads
    search_targets = set(forum_ids) | set(existing_logs.keys())
    for target_id in search_targets:
        archived = discord_api_get(f"channels/{target_id}/threads/archived/public", bot_token)
        if isinstance(archived, dict) and "threads" in archived:
            discovered_threads.extend(archived["threads"])

    for thread in discovered_threads:
        t_id = str(thread["id"])
        p_id = str(thread.get("parent_id", ""))
        meta = thread.get("thread_metadata", {})
        is_archived = meta.get("archived", False)

        # Handle Re-opening of "Stable" content
        if t_id in existing_threads:
            t_entry = existing_threads[t_id]
            if not is_archived and t_entry.get("syncStatus") == "stable":
                t_entry["syncStatus"] = "active"
                nav.update_report(camp_name, "💡 WAKE-UP", f"Thread Re-opened: {t_entry['displayName']}")
            elif is_archived and t_entry.get("syncStatus") == "active":
                t_entry["syncStatus"] = "stable"
            continue

        # Assign Thread to existing Chapter
        if p_id in existing_logs:
            parent_log = existing_logs[p_id]
            t_name = thread.get("name", "Unknown Thread")
            is_ooc = should_auto_ignore(t_name)

            new_thread = {
                "threadID": t_id,
                "displayName": format_pretty_title(t_name),
                "isActive": not is_ooc,
                "syncStatus": "stable" if is_archived else "active",
                "last_synced_id": "",
                "messageCount": 0,
                "tags": []
            }
            parent_log.setdefault("threads", []).append(new_thread)
            if not is_ooc:
                nav.update_report(camp_name, "🚨 NEW THREAD", f"{new_thread['displayName']} (in {parent_log['title']})")

        # Promote Forum Posts to standalone Chapters
        elif p_id in forum_ids and t_id not in existing_logs:
            if re.search(pattern, thread.get("name", ""), re.IGNORECASE):
                entry = build_registry_entry(thread, p_id)
                camp_data["logs"].append(entry)
                existing_logs[t_id] = entry
                nav.update_report(camp_name, "🚨 FORUM CHAPTER", entry['title'])

    return True

def build_registry_entry(discord_obj, parent_id=None):
    """Factory for standard Chronicle Forge log entries."""
    raw_name = discord_obj.get("name", "unknown")
    slug_base = slugify_filename(raw_name).replace(".json", "")
        
    tags = []
    if discord_obj.get("nsfw", False):
        tags.append("nsfw")
        
    return {
        "title": format_pretty_title(raw_name),
        "channelID": str(discord_obj["id"]),
        "parentID": str(parent_id) if parent_id else str(discord_obj.get("parent_id", "")),
        "fileName": f"{slug_base}.json",
        "order": 0,
        "isActive": True,
        "visible": True,
        "syncStatus": "active",
        "last_synced_id": "",
        "messageCount": 0,
        "tags": tags,
        "lastMessageTimestamp": "",
        "source_exists": True, # Forge tracking
        "threads": []
    }