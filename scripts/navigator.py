# ChronicleForge/scripts/navigator.py

import os
import json
import re
import requests
from .utils import save_registry, pad_timestamp, LOCAL_REGISTRY
from .notifier import send_update_email
from . import git_sync
from .config import FORGE_CONFIG

# Narrative Types for Refinement logic (DCE Types mapped to API Integers)
# 0: Default, 19: Reply, 21: ThreadStarter
NARRATIVE_TYPES = FORGE_CONFIG.get("forensics", "narrative_types") or [0, 19, 21]
NSFW_KEYWORDS = FORGE_CONFIG.get("forensics", "nsfw_keywords") or ["🔞", "nsfw", "underage", "18+"]

class Navigator:
    def __init__(self, registry_data):
        self.registry = registry_data
        self.report_log = {}

    def update_report(self, camp_name, action, title, count=0, nsfw_count=0, added=0):
        """Logs activity or errors to the final dispatch report."""
        if camp_name not in self.report_log:
            self.report_log[camp_name] = []
        
        self.report_log[camp_name].append({
            "action": action, 
            "title": title, 
            "count": count,       # The new grand total
            "nsfw_count": nsfw_count,
            "added": added        # The delta (new messages only)
        })
    
    def analyze_and_merge(self, log_entry, refined_messages):
        """
        Forensic Audit: Deep-scans narrative content to calculate stats 
        and safety labels.
        """
        parent_id = str(log_entry.get("channelID"))
        tags_config = FORGE_CONFIG.content_tags
        # Pre-lowercase keywords for efficiency (safely handles None/missing keywords)
        tags_keywords = {
            tag_name: [w.lower() for w in (tag_info.get("keywords") or [])]
            for tag_name, tag_info in tags_config.items()
        }
        
        # Initialize stats tracking for all known sub-threads
        t_stats = {}
        for t in log_entry.get("threads", []):
            t_stats[str(t["threadID"])] = {
                "total": 0,
                "abs_last_id": "",
                "nsfw": 0,  # Legacy
                "tag_counts": {tag_name: 0 for tag_name in tags_config}
            }
        
        forensics = {
            "abs_max_id": 0,      
            "narrative_max_ts": "", 
            "narrative_max_id": 0,
            "parent_total": 0,
            "parent_nsfw": 0,  # Legacy
            "grand_total": 0,
            "grand_nsfw": 0,   # Legacy
            "tag_stats": {
                tag_name: {"grand_count": 0, "parent_count": 0} for tag_name in tags_config
            },
            "thread_stats": t_stats
        }

        for msg in refined_messages:
            msg_id_int = int(msg.get("id"))
            msg_chan_id = str(msg.get("channel_id"))
            is_narrative = msg.get("type") in NARRATIVE_TYPES

            # A. Loop Breaker: Track absolute highest ID regardless of type
            if msg_id_int > forensics["abs_max_id"]:
                forensics["abs_max_id"] = msg_id_int

            # B. Narrative Audit (Counts and Safety)
            if is_narrative:
                forensics["grand_total"] += 1

                # Hierarchical Calculation: Parent vs Thread
                if msg_chan_id == parent_id:
                    forensics["parent_total"] += 1
                elif msg_chan_id in forensics["thread_stats"]:
                    forensics["thread_stats"][msg_chan_id]["total"] += 1
                
                # Dynamic Tags Detection
                for tag_name, tag_info in tags_config.items():
                    keywords = tags_keywords[tag_name]
                    emoji_val = tag_info.get("emoji")
                    
                    raw_scope = tag_info.get("search_scope")
                    if isinstance(raw_scope, list):
                        search_scopes = [s.lower() for s in raw_scope]
                    elif isinstance(raw_scope, str):
                        search_scopes = [raw_scope.lower()]
                    else:
                        search_scopes = ["reactions", "body", "title"] if keywords else ["reactions"]
                        
                    is_msg_tagged = False
                    
                    # 1. Match by Emoji (if configured)
                    if emoji_val:
                        for r in (msg.get("reactions") or []):
                            emoji_obj = r.get("emoji") or {}
                            ename = emoji_obj.get("name", "")
                            ecode = emoji_obj.get("code", "")
                            if emoji_val == ename or emoji_val == ecode:
                                is_msg_tagged = True
                                break
                                
                    # 2. Match by Keywords (if configured)
                    if not is_msg_tagged and keywords:
                        # Determine what to search
                        search_texts = []
                        if "reactions" in search_scopes:
                            # Search reaction names/codes
                            for r in (msg.get("reactions") or []):
                                emoji_obj = r.get("emoji") or {}
                                ename = emoji_obj.get("name", "").lower()
                                ecode = emoji_obj.get("code", "").lower()
                                search_texts.extend([ename, ecode])
                        if "body" in search_scopes:
                            search_texts.append((msg.get("content") or "").lower())
                        if "title" in search_scopes:
                            # Find the title for the current message's channel/thread
                            chan_title = ""
                            if msg_chan_id == parent_id:
                                chan_title = log_entry.get("title") or ""
                            else:
                                for t in log_entry.get("threads", []):
                                    if str(t.get("threadID")) == msg_chan_id:
                                        chan_title = t.get("displayName") or ""
                                        break
                            search_texts.append(chan_title.lower())
                                
                        # Run keyword matching
                        for text in search_texts:
                            if any(word in text for word in keywords):
                                is_msg_tagged = True
                                break
                    
                    if is_msg_tagged:
                        forensics["tag_stats"][tag_name]["grand_count"] += 1
                        if msg_chan_id == parent_id:
                            forensics["tag_stats"][tag_name]["parent_count"] += 1
                        elif msg_chan_id in forensics["thread_stats"]:
                            forensics["thread_stats"][msg_chan_id]["tag_counts"][tag_name] += 1
                        
                        # Legacy backwards compatibility
                        if tag_name == "nsfw":
                            forensics["grand_nsfw"] += 1
                            if msg_chan_id == parent_id:
                                forensics["parent_nsfw"] += 1
                            elif msg_chan_id in forensics["thread_stats"]:
                                forensics["thread_stats"][msg_chan_id]["nsfw"] += 1
                
                # Temporal Tracking: Capture newest post timestamp
                if msg_id_int > forensics["narrative_max_id"]:
                    forensics["narrative_max_id"] = msg_id_int
                    forensics["narrative_max_ts"] = msg.get("timestamp")

            # C. Thread High-Water Mark Update
            if msg_chan_id in forensics["thread_stats"]:
                curr_abs = forensics["thread_stats"][msg_chan_id]["abs_last_id"]
                if not curr_abs or msg_id_int > int(curr_abs):
                    forensics["thread_stats"][msg_chan_id]["abs_last_id"] = str(msg_id_int)

        return forensics

    def apply_forensics_to_registry(self, log_entry, forensics, api_id_stamp=None):
        """Registry Smash: Finalizes the metadata state for deployment."""
        tags_config = FORGE_CONFIG.content_tags
        
        # Initialize tags list in log entry safely
        log_tags = log_entry.get("tags")
        if not isinstance(log_tags, list):
            log_tags = []
            log_entry["tags"] = log_tags
            
        log_tag_stats = log_entry.get("tagStats")
        if not isinstance(log_tag_stats, dict):
            log_tag_stats = {}
            log_entry["tagStats"] = log_tag_stats
        
        tags_keywords = {
            tag_name: [w.lower() for w in (tag_info.get("keywords") or [])]
            for tag_name, tag_info in tags_config.items()
        }
        
        for tag_name, tag_info in tags_config.items():
            threshold = tag_info.get("threshold", 0.90)
            
            p_total = forensics["parent_total"]
            p_tag_count = forensics["tag_stats"][tag_name]["parent_count"]
            parent_pct = p_tag_count / p_total if p_total > 0 else 0
            
            # Force active if tag emoji or keywords are in the channel title
            title_text = (log_entry.get("title") or "").lower()
            emoji_val = tag_info.get("emoji")
            keywords = tags_keywords.get(tag_name, [])
            
            title_has_tag = False
            if emoji_val and emoji_val.lower() in title_text:
                title_has_tag = True
            elif keywords and any(word in title_text for word in keywords):
                title_has_tag = True
                
            is_active = (parent_pct >= threshold) or title_has_tag
            
            log_tag_stats[tag_name] = {
                "count": forensics["tag_stats"][tag_name]["grand_count"],
                "active": is_active,
                "emoji": tag_info.get("emoji", "")
            }
            
            if is_active:
                if tag_name not in log_tags:
                    log_tags.append(tag_name)
            else:
                if tag_name in log_tags:
                    log_tags.remove(tag_name)
            
            # Legacy backwards compatibility updates
            if tag_name == "nsfw":
                log_entry["nsfwCount"] = forensics["tag_stats"]["nsfw"]["grand_count"]
                if is_active:
                    log_entry["isNSFW"] = True
                else:
                    log_entry.pop("isNSFW", None)
            
        log_entry["messageCount"] = forensics["grand_total"]
        if forensics["narrative_max_ts"]:
            log_entry["lastMessageTimestamp"] = forensics["narrative_max_ts"]

        # 2. Sync Stamping (Loop Breaker)
        final_sync_id = api_id_stamp or str(forensics["abs_max_id"])
        if final_sync_id and final_sync_id != "0":
            log_entry["last_synced_id"] = final_sync_id

        # 3. Thread Synchronization & Auto-Stabilization
        for t in log_entry.get("threads", []):
            t_id = str(t.get("threadID"))
            if t_id in forensics["thread_stats"]:
                stats = forensics["thread_stats"][t_id]
                t["messageCount"] = stats["total"]
                t_tags = t.get("tags")
                if not isinstance(t_tags, list):
                    t_tags = []
                    t["tags"] = t_tags
                    
                t_tag_stats = t.get("tagStats")
                if not isinstance(t_tag_stats, dict):
                    t_tag_stats = {}
                    t["tagStats"] = t_tag_stats
                
                for tag_name, tag_info in tags_config.items():
                    threshold = tag_info.get("threshold", 0.90)
                    t_total = stats["total"]
                    t_tag_count = stats["tag_counts"].get(tag_name, 0)
                    thread_pct = t_tag_count / t_total if t_total > 0 else 0
                    
                    # Force active if tag emoji or keywords are in the thread title
                    t_title_text = (t.get("displayName") or "").lower()
                    emoji_val = tag_info.get("emoji")
                    keywords = tags_keywords.get(tag_name, [])
                    
                    t_title_has_tag = False
                    if emoji_val and emoji_val.lower() in t_title_text:
                        t_title_has_tag = True
                    elif keywords and any(word in t_title_text for word in keywords):
                        t_title_has_tag = True
                        
                    is_active = (thread_pct >= threshold) or t_title_has_tag
                    
                    t_tag_stats[tag_name] = {
                        "count": t_tag_count,
                        "active": is_active,
                        "emoji": tag_info.get("emoji", "")
                    }
                    
                    if is_active:
                        if tag_name not in t_tags:
                            t_tags.append(tag_name)
                    else:
                        if tag_name in t_tags:
                            t_tags.remove(tag_name)
                    
                    # Legacy thread level updates
                    if tag_name == "nsfw":
                        t["nsfwCount"] = t_tag_count
                        if is_active:
                            t["isNSFW"] = True
                        else:
                            t.pop("isNSFW", None)
                
                if stats["abs_last_id"]:
                    t["last_synced_id"] = stats["abs_last_id"]

    def _check_channel_activity(self, channel_id, last_id, token):
        """Internal helper to check a specific ID for new narrative content."""
        headers = {"Authorization": f"Bot {token}"}
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=10"
        
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                return False, None
            
            messages = resp.json()
            if not messages: return False, None

            # Find the newest NARRATIVE Snowflake (Forge API Mapping)
            latest_ic_id = None
            for msg in messages:
                if msg.get("type") in NARRATIVE_TYPES:
                    latest_ic_id = str(msg.get("id"))
                    break
            
            if not latest_ic_id: return False, None

            has_new = False
            if not last_id:
                has_new = True
            else:
                try:
                    has_new = int(latest_ic_id) > int(last_id)
                except ValueError:
                    has_new = latest_ic_id != last_id
                
            return has_new, latest_ic_id
        except Exception:
            return False, None

    def should_sync(self, entry, token, force_all=False):
        """
        Wide-Search API Check: Evaluates parent and child threads before mining.
        Ensures side-scenes trigger a sync even if the main chapter is idle.
        """
        if not entry.get("isActive", True): return False, None
        if entry.get("syncStatus") == "legacy": return False, None

        last_id = str(entry.get("last_synced_id", ""))
        parent_id = entry.get("channelID") or entry.get("threadID")
        camp_name = entry.get("title", parent_id)
        
        # 1. Primary Check: The Parent Channel
        has_new_content, latest_id = self._check_channel_activity(parent_id, last_id, token)
        
        # 2. Wide-Search: Check active child threads if parent was quiet
        if not has_new_content:
            for thread in entry.get("threads", []):
                if thread.get("isActive", True):
                    t_has_new, t_latest_id = self._check_channel_activity(thread["threadID"], last_id, token)
                    if t_has_new:
                        has_new_content = True
                        latest_id = t_latest_id
                        break 

        # Forge Wake-Up Logic
        if has_new_content and entry.get("syncStatus") == "stable":
            print(f"      [Wake-Up] Activity detected in stable chapter: {camp_name}")
            entry["syncStatus"] = "active"

        if force_all: return True, latest_id
        if entry.get("syncStatus") == "stable" and not has_new_content: 
            return False, latest_id

        return has_new_content, latest_id

    def finalize_run(self, is_dry_run=False):
        """Saves registry, synchronizes brain to GitHub, and dispatches Forge reports."""
        save_registry(self.registry)

        if not is_dry_run:
            website_repo = os.getenv("WEBSITE_REPO")
            website_branch = os.getenv("WEBSITE_BRANCH", "main")
            if website_repo:
                print(f"\n🚀 Syncing Forge Brain to: {website_repo}")
                git_sync.sync_website_registry(website_repo, self.registry, branch=website_branch)
        
        # Dispatch Heartbeat Email
        if not self.report_log:
            self.update_report("Forge", "STATUS", "All narratives synchronized/No new activity.")

        send_update_email(self.report_log, is_dry_run)