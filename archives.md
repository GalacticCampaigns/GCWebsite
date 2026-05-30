---
layout: default
title: Archives
---

<style>
    .archive-list { margin-top: 30px; }

    .archive-item {
        transition: transform 0.2s ease, border-color 0.2s ease;
        margin-bottom: 15px;
        padding: 20px;
        background: var(--secondary-bg);
        border: 1px solid var(--gh-border);
        border-radius: 6px;
        position: relative;
    }

    .archive-item:hover {
        transform: translateX(8px);
        border-color: var(--accent-blue);
        background: rgba(97, 218, 251, 0.05);
    }

    .archive-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 10px;
        margin-bottom: 8px;
    }

    .archive-link {
        font-size: 1.2rem;
        font-weight: bold;
        color: var(--accent-blue);
        letter-spacing: 0.5px;
        text-decoration: none;
        text-transform: uppercase;
    }

    .archive-meta {
        font-family: monospace;
        font-size: 0.85rem;
        color: var(--text-muted);
    }

    .archive-preview {
        font-size: 0.9rem;
        color: var(--gh-text);
        opacity: 0.8;
        line-height: 1.4;
        display: block;
        margin-top: 10px;
        border-top: 1px solid rgba(255, 255, 255, 0.1);
        padding-top: 10px;
        font-style: italic;
    }

    .signal-tag { color: var(--sw-yellow); font-weight: bold; }
    
    .status-badge {
        font-size: 0.6rem;
        padding: 2px 6px;
        border-radius: 3px;
        text-transform: uppercase;
        margin-left: 10px;
        vertical-align: middle;
    }
    .status-active { background: #238636; color: white; }
    .status-dropped { background: #da3633; color: white; }
</style>

<div class="archive-container">
    <h1 id="archive-title" style="color: var(--accent-blue); border-bottom: 2px solid var(--accent-blue); padding-bottom: 10px;">
        📡 Chapter Archives
    </h1>
    <p id="archive-status" style="font-style: italic; color: var(--text-muted);">
        Accessing encrypted campaign logs from the holonet...
    </p>

    <div id="archive-list-target" class="archive-list">
        <div class="archive-item">
            <p class="timestamp">Initializing secure uplink...</p>
        </div>
    </div>
</div>

<script>
document.addEventListener("DOMContentLoaded", async function() {
    const registry = await getRegistry();
    const target = document.getElementById('archive-list-target');
    const titleEl = document.getElementById('archive-title');
    const statusEl = document.getElementById('archive-status');
    
    const { slug } = getUrlContext();

    if (!slug || !registry || !registry.campaigns[slug]) {
        target.innerHTML = `
            <div class="archive-item" style="border-style: dashed; text-align: center; padding: 40px;">
                <p style="color: var(--sw-yellow);"><strong>NO FREQUENCY SELECTED</strong></p>
                <p>Please select a campaign from the selector above to view its specific archives.</p>
            </div>`;
        statusEl.textContent = "Standby for input...";
        return;
    }

    const campaign = registry.campaigns[slug];
    titleEl.textContent = `📡 ${campaign.name} Archives`;
    statusEl.textContent = `Displaying all transmissions for ${campaign.name}...`;

    // 1. FILTER: Only include active logs
    // 2. SORT: Order them by the 'order' key
    const sorted = campaign.logs
        .filter(log => log.isActive !== false && log.visible !== false)
        .sort((a, b) => {
        // 1. First, sort by Order (Descending)
        if (b.order !== a.order) {
            return b.order - a.order;
        }
        // 2. If Order is identical, sort by Title (Alphabetical)
        return a.title.localeCompare(b.title);
        });

    target.innerHTML = ""; 

    sorted.forEach(log => {
        const dateStr = log.lastMessageTimestamp ? new Date(log.lastMessageTimestamp).toLocaleDateString(undefined, {
            year: 'numeric', month: 'long', day: 'numeric'
        }) : 'Unknown';

        const viewerBase = log.fileName.endsWith('.json') ? `${window.site_baseurl}/logs` : `${window.site_baseurl}/entry`;
        const item = document.createElement('div');
        item.className = "archive-item";
        
        // Removed statusBadge logic (ACTIVE/DROPPED)

        // Check for NSFW flag
        const nsfwClass = log.isNSFW ? 'nsfw-blur' : '';
        const nsfwBadge = log.isNSFW ? '<span class="nsfw-badge">NSFW</span>' : '';

        item.innerHTML = `
            <div class="archive-header">
                <a href="${viewerBase}?c=${slug}#${log.channelID}" class="archive-link">
                    ${nsfwBadge}${log.title}
                </a>
                <div class="archive-meta">TRANSMITTED: ${dateStr}</div>
            </div>
            <div class="archive-meta">
                SIGNAL STRENGTH: <span class="signal-tag">${log.messageCount} POSTS</span>
            </div>
            ${log.preview ? `<div class="archive-preview ${nsfwClass}">${log.preview}</div>` : ''}
        `;
        target.appendChild(item);
    });
    
});
</script>