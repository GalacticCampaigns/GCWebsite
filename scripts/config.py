# ChronicleForge/scripts/config.py

import os
import json
from dotenv import load_dotenv
from .utils import PROJECT_ROOT

class ConfigManager:
    def __init__(self):
        # 1. Load Secrets (Pillar 4)
        # Prioritizes System Env Vars (Cloud), falls back to .env (Local)
        load_dotenv()
        
        self.discord_token = os.getenv("DISCORD_TOKEN")
        self.website_repo = os.getenv("WEBSITE_REPO")
        self.website_branch = os.getenv("WEBSITE_BRANCH", "main")
        self.registry_url = os.getenv("REGISTRY_URL")
        self.email_recipient = os.getenv("EMAIL_RECIPIENT")

        # 2. Load Application Logic
        config_path = os.path.join(PROJECT_ROOT, "refinery-config.json")
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                self.app_config = json.load(f)
        else:
            print(f"      [Config] ⚠️ Warning: refinery-config.json not found. Using defaults.")
            self.app_config = {}

    def get(self, section, key, default=None):
        """Helper to safely retrieve nested settings."""
        return self.app_config.get(section, {}).get(key, default)

    @property
    def content_tags(self):
        """Returns configured content tags from refinery-config.json, with nsfw fallback."""
        if hasattr(self, '_content_tags_override'):
            return self._content_tags_override
        tags = self.get("forensics", "tags")
        if not tags:
            nsfw_keywords = self.get("forensics", "nsfw_keywords") or ["nsfw", "🔞", "underage", "18+"]
            nsfw_threshold = self.get("forensics", "nsfw_threshold") or 0.90
            tags = {
                "nsfw": {
                    "keywords": nsfw_keywords,
                    "emoji": "🔞",
                    "threshold": nsfw_threshold
                }
            }
        return tags

    @content_tags.setter
    def content_tags(self, value):
        self._content_tags_override = value

    @property
    def is_cloud(self):
        """Environment Detection for Pillar 4 portability."""
        return os.getenv("CODESPACES") == "true"

    def get_git_alias(self, repo_owner):
        """Resolves SSH aliases for Local mode or defaults for Cloud."""
        if self.is_cloud:
            return "github.com"
        aliases = self.app_config.get("git_aliases", {})
        return aliases.get(repo_owner, "github.com")

# Global singleton for easy access across scripts
FORGE_CONFIG = ConfigManager()