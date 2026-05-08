# ChronicleForge/scripts/miner.py

import os
import json
import time
import re
import requests
from .utils import (
    pad_timestamp, 
    to_snowflake, 
    extract_pure_hash, 
    sanitize_resource_name,
    resolve_vault_path
)

from .config import FORGE_CONFIG

class Miner:
    def __init__(self, token, dry_run=False):
        self.dry_run = dry_run
        # Standardize token format for System Agnostic API calls
        clean_token = token.replace("Bot ", "").strip()
        self.headers = {
            "Authorization": f"Bot {clean_token}",
            "Content-Type": "application/json"
        }
        api_version = FORGE_CONFIG.get("discord", "api_version", "v10")
        self.base_url = f"https://discord.com/api/{api_version}"
        
    def _api_get(self, endpoint, params=None):
        """Hardened GET with Smart-Sleep Rate Limiting and Timeout protection."""
        url = f"{self.base_url}/{endpoint}"
        try:
            resp = requests.get(url, headers=self.headers, params=params, timeout=15)
            if resp.status_code == 429: # Rate Limited
                retry_after = resp.json().get("retry_after", 1)
                print(f"      [Miner] ⏳ Rate limited. Resuming in {retry_after}s...")
                time.sleep(retry_after)
                return self._api_get(endpoint, params)
            
            if resp.status_code != 200:
                detail = resp.json().get('message', 'Access Denied')
                return {"error": f"API {resp.status_code}: {detail}"}
            
            return resp.json() if resp.text else []
        except Exception as e:
            return {"error": f"Connection Failed: {str(e)}"}

    def download_asset(self, url, dest_path):
        """Pillar 3: Total Mirroring. Skip if exists, otherwise Deep Freeze to Vault."""
        if os.path.exists(dest_path):
            return True
        try:
            resp = requests.get(url, stream=True, timeout=10)
            if resp.status_code == 200:
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with open(dest_path, 'wb') as f:
                    for chunk in resp.iter_content(1024):
                        f.write(chunk)
                return True
        except Exception:
            return False
        return False

    def fetch_history(self, channel_id, last_id=None, limit=100, direction="after"):
        """Recursive pagination for high-fidelity extraction."""
        messages = []
        params = {"limit": limit}
        if last_id and str(last_id).strip():
            params[direction] = last_id
        while True:
            batch = self._api_get(f"channels/{channel_id}/messages", params)
            if isinstance(batch, dict) and "error" in batch: return batch 
            if not isinstance(batch, list) or not batch: break
            messages.extend(batch)
            batch_ids = [int(m["id"]) for m in batch]
            params[direction] = str(max(batch_ids)) if direction == "after" else str(min(batch_ids))
            if len(batch) < limit: break
        return sorted(messages, key=lambda x: x["id"])

    def _get_base_path(self, repo_folder, camp_config):
        data_path = camp_config.get("dataPath", "./").strip()
        return resolve_vault_path(repo_folder, data_path)

    def generate_channel_meta(self, repo_folder, channel_id, camp_config):
        base = self._get_base_path(repo_folder, camp_config)
        rel_json = camp_config.get("paths", {}).get("json", "json/")
        meta_dir = os.path.join(base, rel_json, "meta")
        meta_file = os.path.join(meta_dir, f"{channel_id}.json")

        live_channel = self._api_get(f"channels/{channel_id}")
        if isinstance(live_channel, dict) and "error" not in live_channel:
            meta_data = {
                "id": to_snowflake(live_channel.get("id")),
                "name": live_channel.get("name"),
                "type": live_channel.get("type", 0),
                "is_age_restricted": live_channel.get("nsfw", False),
                "parent_id": to_snowflake(live_channel.get("parent_id")),
                "thread_metadata": live_channel.get("thread_metadata"),
                "last_mined_at": pad_timestamp(time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
            }
            os.makedirs(meta_dir, exist_ok=True)
            with open(meta_file, 'w', encoding='utf-8') as f:
                json.dump(meta_data, f, indent=2, ensure_ascii=False)
            return True
        return False

    def map_to_gold(self, msg, repo_folder, log_entry, camp_config):
        """The 'Gold' Standard Pass: CDN Mirroring and Key Restoration."""
        author = msg.get("author", {}) or {"id": "0", "username": "system"}
        msg_id, channel_id = to_snowflake(msg.get("id")), to_snowflake(msg.get("channel_id"))
        base = self._get_base_path(repo_folder, camp_config)
        paths = camp_config.get("paths", {})
        
        rel_emoji = paths.get("emoji", "emojis/")
        rel_avatars = paths.get("avatars", "avatars/")
        rel_media = paths.get("media", "media/")
        chapter_slug = log_entry.get("fileName", "").replace(".json", "")

        # Avatar Mirroring (Suffix Stripping logic)
        u_id, a_hash = to_snowflake(author.get("id")), extract_pure_hash(author.get("avatar"))
        if a_hash:
            dest = os.path.join(base, rel_avatars, u_id, f"{a_hash}.png")
            self.download_asset(f"https://cdn.discordapp.com/avatars/{u_id}/{a_hash}.png", dest)

        # Content Transformations (Emoji reconstruction)
        self.process_custom_emojis(msg.get("content", ""), base, rel_emoji)

        return {
            "id": msg_id,
            "type": int(msg.get("type", 0)),
            "content": msg.get("content", ""),
            "channel_id": channel_id, 
            "author": {
                "id": u_id,
                "username": author.get("username", "unknown"),
                "avatar": a_hash,
                "bot": author.get("bot", False)
            },
            "attachments": self.process_attachments(msg.get("attachments", []), base, rel_media, chapter_slug),
            "embeds": self.process_embeds(msg.get("embeds", []), base, rel_media, chapter_slug),
            "reactions": self.process_reactions(msg.get("reactions", []), base, rel_emoji),
            "timestamp": pad_timestamp(msg.get("timestamp")),
            "edited_timestamp": pad_timestamp(msg.get("edited_timestamp")) if msg.get("edited_timestamp") else None,
            "flags": msg.get("flags", 0),
            "thread": self._get_thread_obj(channel_id) if (msg_id == channel_id or int(msg.get("type")) == 21) else None,
            "position": 0,
            "userName": author.get("username", "unknown")
        }

    def process_embeds(self, embeds, base, rel_media, chapter_slug):
        """Restored Pillar 3: Mirrors embed thumbnails to ensure total decoupling."""
        refined = []
        for e in embeds:
            obj = {k: v for k, v in e.items() if k != "thumbnail"}
            thumb = e.get("thumbnail")
            if thumb and thumb.get("url"):
                url = thumb["url"]
                # Create a hash-based name for the thumbnail to prevent collisions
                t_hash = extract_pure_hash(url) or str(int(time.time()))
                ext = url.split(".")[-1].split("?")[0] or "png"
                file_name = f"thumb_{t_hash}.{ext}"
                dest = os.path.join(base, rel_media, chapter_slug, file_name)
                
                if self.download_asset(url, dest):
                    obj["thumbnail"] = {
                        "url": f"{rel_media}{chapter_slug}/{file_name}",
                        "proxy_url": f"{rel_media}{chapter_slug}/{file_name}"
                    }
            refined.append(obj)
        return refined

    def _get_thread_obj(self, channel_id):
        live = self._api_get(f"channels/{channel_id}")
        if isinstance(live, dict) and "error" not in live:
            return {"id": channel_id, "name": live.get("name"), "type": live.get("type", 11)}
        return None

    def process_custom_emojis(self, content, base, rel_emoji):
        matches = re.findall(r'<(a?):(\w+):(\d+)>', content)
        for is_animated, name, e_id in matches:
            ext = "gif" if is_animated else "png"
            file_name = f"!{name}!_{e_id}.{ext}"
            dest = os.path.join(base, rel_emoji, file_name)
            self.download_asset(f"https://cdn.discordapp.com/emojis/{e_id}.{ext}", dest)

    def process_attachments(self, attachments, base, rel_media, chapter_slug):
        refined = []
        for a in attachments:
            clean_name = sanitize_resource_name(a.get("filename"))
            dest = os.path.join(base, rel_media, chapter_slug, clean_name)
            if self.download_asset(a.get("url"), dest):
                refined.append({
                    "id": to_snowflake(a.get("id")), 
                    "filename": clean_name, 
                    "url": f"{rel_media}{chapter_slug}/{clean_name}"
                })
        return refined

    def process_reactions(self, reactions, base, rel_emoji):
        refined = []
        for r in reactions:
            emoji = r.get("emoji", {})
            e_id = to_snowflake(emoji.get("id"))
            if e_id and e_id != "0":
                ext = "gif" if emoji.get("animated") else "png"
                name = emoji.get("name", "unknown")
                file_name = f"!{name}!_{e_id}.{ext}"
                dest = os.path.join(base, rel_emoji, file_name)
                self.download_asset(f"https://cdn.discordapp.com/emojis/{e_id}.{ext}", dest)
                refined.append({"emoji": {"id": e_id, "name": name, "local": file_name}, "count": r.get("count", 0)})
            else:
                refined.append({"emoji": {"id": None, "name": emoji.get("name")}, "count": r.get("count", 0)})
        return refined

    def sync_chapter_family(self, repo_folder, log_entry, camp_config, full_audit=False):
        """Unified Family Sync: Manages Private Thread metadata and Parent/Thread merging."""
        p_id = str(log_entry.get("channelID"))
        active_threads = [t for t in log_entry.get("threads", []) if t.get("isActive", True)]
        all_messages = []
        
        # 1. Fetch Parent (Incremental or Audit)
        self.generate_channel_meta(repo_folder, p_id, camp_config)
        parent_batch = self.fetch_history(p_id, last_id=None if full_audit else log_entry.get("last_synced_id"))
        if isinstance(parent_batch, list):
            for m in parent_batch:
                all_messages.append(self.map_to_gold(m, repo_folder, log_entry, camp_config))

        # 2. Fetch Active Threads (Meta Recovery included)
        for thread in active_threads:
            t_id = str(thread["threadID"])
            # Generate local meta file as backup
            self.generate_channel_meta(repo_folder, t_id, camp_config)
            
            thread_batch = self.fetch_history(t_id, last_id=None if full_audit else thread.get("last_synced_id"))
            if isinstance(thread_batch, list) and thread_batch:
                for m in thread_batch:
                    mapped = self.map_to_gold(m, repo_folder, log_entry, camp_config)
                    # Force meta-injection for Private Threads missing Type 21
                    if not mapped.get("thread") and m["id"] == thread_batch[0]["id"]:
                        mapped["thread"] = self._get_thread_obj(t_id)
                    all_messages.append(mapped)
        
        if all_messages:
            self.save_to_vault(repo_folder, log_entry, all_messages, camp_config)
            return True
        return False

    def save_to_vault(self, repo_folder, log_entry, new_msgs, camp_config):
        """Restored: Performs Surgical Merge and Flattened JSON export."""
        base = self._get_base_path(repo_folder, camp_config)
        rel_json = camp_config.get("paths", {}).get("json", "json/")
        dest_file = os.path.join(base, rel_json, log_entry["fileName"])
        
        os.makedirs(os.path.dirname(dest_file), exist_ok=True)
        data = []
        if os.path.exists(dest_file):
            with open(dest_file, 'r', encoding='utf-8') as f:
                try: data = json.load(f)
                except: data = []
        
        db = {m["id"]: m for m in data}
        for m in new_msgs:
            if m["id"] in db:
                db[m["id"]].update(m) # Surgical update
            else:
                db[m["id"]] = m
        
        final = sorted(db.values(), key=lambda x: x["timestamp"])
        for i, m in enumerate(final): m["position"] = i
        
        # Flattened Output for Git-friendly diffs
        with open(dest_file, 'w', encoding='utf-8') as f:
            f.write("[\n")
            for i, msg in enumerate(final):
                line = json.dumps(msg, ensure_ascii=False)
                comma = "," if i < len(final) - 1 else ""
                f.write(f"  {line}{comma}\n")
            f.write("]")