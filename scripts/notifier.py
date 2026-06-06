# ChronicleForge/scripts/notifier.py
import subprocess
import os
from datetime import datetime
from .config import FORGE_CONFIG

def send_update_email(report_data, is_dry_run=False):
    """
    Forge Dispatch: Sends a formatted campaign report.
    Pillar 4: System Agnostic. Uses 'mail' command with a local log fallback.
    """
    if not report_data:
        print("      [Notifier] No activity recorded. Skipping report.")
        return

    recipient = FORGE_CONFIG.email_recipient or "admin@example.com"
    
    # 1. Environment & Mode Labels
    prefix = "[DRY RUN]" if is_dry_run else "[SYNC]"
    stat_label = "Potential" if is_dry_run else "New"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    subject = f"⚒️ {prefix} Forge Report - {timestamp}"
    
    # 2. Build the Header
    body = "CHRONICLE FORGE: REFINERY DISPATCH\n"
    body += "========================================\n"
    body += f"Run Time: {timestamp}\n"
    body += f"Mode:     {'SIMULATION (No Pushes)' if is_dry_run else 'LIVE PRODUCTION'}\n"
    body += f"Node:     {'GITHUB CLOUD' if FORGE_CONFIG.is_cloud else 'LOCAL NODE'}\n"
    body += "========================================\n\n"

    # 3. Campaign Iteration
    for camp_name, updates in report_data.items():
        # Calculate deltas for this run
        camp_added = sum(u.get('added', 0) for u in updates)
        
        # Aggregate tag deltas across all updates in this campaign
        tags_config = FORGE_CONFIG.content_tags
        camp_tags_deltas = {tag_name: 0 for tag_name in tags_config}
        
        for u in updates:
            u_tags = u.get('nsfw_count')
            if isinstance(u_tags, dict):
                for tag_name, val in u_tags.items():
                    camp_tags_deltas[tag_name] = camp_tags_deltas.get(tag_name, 0) + val
            elif isinstance(u_tags, (int, float)) and u_tags > 0:
                camp_tags_deltas["nsfw"] = camp_tags_deltas.get("nsfw", 0) + int(u_tags)
                
        # Build active tags string: E.g., " (🔞 1 NSFW, ⚔️ 2 Action)"
        active_tags_parts = []
        for tag_name, val in camp_tags_deltas.items():
            if val > 0:
                emoji = tags_config.get(tag_name, {}).get("emoji", "")
                label = tag_name.upper()
                active_tags_parts.append(f"{emoji} {val} {label}")
                
        tags_part = f" ({', '.join(active_tags_parts)})" if active_tags_parts else ""
        
        body += f"📂 CAMPAIGN: {camp_name}\n"
        body += f"📊 {stat_label} STATS: {camp_added} Posts{tags_part}\n"
        body += "----------------------------------------\n"
        
        # Sort: Discoveries/Errors first
        sorted_updates = sorted(updates, key=lambda x: x['action'], reverse=True)
        
        for item in sorted_updates:
            action = item['action']
            title = item['title']
            count = item.get('count', 0)      # Grand Total (Cumulative)
            u_tags = item.get('nsfw_count', 0)  # Tag deltas (new)
            added = item.get('added', 0)      # Delta (Run-specific)
            
            # Resolve tag deltas
            tag_deltas = {}
            if isinstance(u_tags, dict):
                tag_deltas = u_tags
            elif isinstance(u_tags, (int, float)) and u_tags > 0:
                tag_deltas = {"nsfw": int(u_tags)}
            
            if any(key in action for key in ["DISCOVERY", "MISSING", "LOCK", "WARNING"]):
                body += f"  {action} {title}\n"
            else:
                # Format Chapter Narrative details
                body += f"  - [{action}] {title}\n"
                
                total_label = "Total Posts" if "ooc" in title.lower() else "Total Narrative"
                if added > 0:
                    tag_parts = []
                    for tag_name, val in tag_deltas.items():
                        if val > 0:
                            emoji = tags_config.get(tag_name, {}).get("emoji", "")
                            label = tag_name.upper()
                            tag_parts.append(f"{emoji} {val} {label}")
                    tags_str = f" ({', '.join(tag_parts)})" if tag_parts else ""
                    
                    body += f"    ✨ {stat_label}: {added} posts{tags_str}\n"
                    body += f"    📚 {total_label}: {count}\n"
                elif count > 0:
                    # Logic for forced audits with no new posts
                    body += f"    📚 {total_label}: {count}\n"
        
        body += "\n"

    body += "----------------------------------------\n"
    body += "🔚 End of Forge Cycle\n"
    body += f"Architecture: {'System Agnostic .NET' if FORGE_CONFIG.is_cloud else 'Dockerized DCE'}\n"

    # 4. Delivery Phase
    is_gha = os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CODESPACES") == "true"
    
    if is_gha:
        print("      [Notifier] Running in Cloud Environment. Saving report to local log.")
        log_file = os.path.join(os.getcwd(), "forge_report_latest.log")
        with open(log_file, "w", encoding='utf-8') as f:
            f.write(f"SUBJECT: {subject}\n\n{body}")
        print(f"      [Notifier] Report saved to: {log_file}")
    else:
        try:
            # Attempt Linux 'mail' command with a strict timeout to prevent hangs
            process = subprocess.Popen(
                ['mail', '-s', subject, recipient],
                stdin=subprocess.PIPE, 
                text=True,
                encoding='utf-8'
            )
            try:
                process.communicate(input=body, timeout=10)
                print(f"      [Notifier] Report dispatched to {recipient}")
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise RuntimeError("Mail command timed out after 10 seconds")
        except Exception as e:
            print(f"      [Notifier] Mail command unavailable or timed out ({e}). Saving to local log.")
            # Fallback: Save to file for manual review in Codespace/Pi
            log_file = os.path.join(os.getcwd(), "forge_report_latest.log")
            with open(log_file, "w", encoding='utf-8') as f:
                f.write(f"SUBJECT: {subject}\n\n{body}")
            print(f"      [Notifier] Report saved to: {log_file}")