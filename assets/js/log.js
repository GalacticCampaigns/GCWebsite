// assets/js/log.js

// --- 1. GLOBAL STATE ---
let fullData = [];
window.channelMap = {}; 
let mainChannelId = null;

// --- 2. INITIALIZATION ---
async function init() {
    try {
        if (!window.GC_STATE || !window.GC_STATE.isReady) {
            await updateGlobalNav(); 
        }

        registry = window.GC_STATE.registry;
        activeCampaign = window.GC_STATE.currentCampaign;
        campaignSlug = window.GC_STATE.campaignSlug;
        remoteBase = window.GC_STATE.remoteBase;

        const { slug, channelId, messageId } = getUrlContext();
        
        if (!activeCampaign) {
            throw new Error("No active campaign selected. Please return to the Hub.");
        }

        const backBtn = document.getElementById('back-to-archives');
        if (backBtn) {
            backBtn.href = `${window.site_baseurl}/archives?c=${campaignSlug}`;
        }

        const listTarget = document.getElementById('chapter-list-dropdown');
        if (listTarget) {
            listTarget.innerHTML = "";
            activeCampaign.logs.forEach(item => {
                const div = document.createElement('div');
                div.className = "chapter-option";
                div.textContent = item.title;
                div.onclick = (e) => {
                    e.stopPropagation();
                    toggleChapterList();
                    loadChapter(item.channelID);
                };
                listTarget.appendChild(div);
            });
        }

        const rawHash = window.location.hash.substring(1);
        let chanId = null;
        let msgId = null;
        let autoFilter = 'all';

        if (rawHash) {
            const hashParts = rawHash.split(':');
            const primaryId = hashParts[0];
            msgId = hashParts[1] || null;

            let targetChapter = activeCampaign.logs.find(l => l.channelID === primaryId);
            
            if (targetChapter) {
                chanId = primaryId;
                autoFilter = 'all';
            } else {
                const parentLog = activeCampaign.logs.find(l => 
                    l.threads && l.threads.some(t => t.threadID === primaryId)
                );
                
                if (parentLog) {
                    targetChapter = parentLog;
                    chanId = parentLog.channelID;
                    autoFilter = primaryId; 
                    if (!msgId) msgId = primaryId; 
                } else {
                    const crossRef = resolveInternalLinkData(primaryId);
                    if (crossRef.found && crossRef.slug !== campaignSlug) {
                        window.location.href = `${window.site_baseurl}/logs?c=${crossRef.slug}#${rawHash}`;
                        return;
                    }
                }
            }

            if (targetChapter) {
                await loadChapter(chanId, msgId, autoFilter);
                return;
            }
        }

        if (activeCampaign.logs.length > 0) {
            await loadChapter(activeCampaign.logs[0].channelID);
        } else {
            throw new Error("This campaign contains no log entries.");
        }

    } catch (e) {
        console.error("Init Error:", e);
        const output = document.getElementById('viewer-output');
        if (output) {
            output.innerHTML = `<div style="padding: 20px; border: 1px dashed var(--sw-yellow); text-align: center;"><b style="color:var(--sw-yellow)">SYSTEM ERROR:</b><br>${e.message}<br><br><a href="${window.site_baseurl}/" style="color: var(--text-muted)">Return to Hub</a></div>`;
        }
    }
}

// Listen for global NSFW toggle to re-render attachments
document.addEventListener('NSFWStateChanged', () => {
    if (!fullData || fullData.length === 0) return;
    const currentHash = window.location.hash.substring(1).split(':')[0] || 'all';
    renderFeed(currentHash);
});

// Update HUD visibility on scroll
window.addEventListener('scroll', () => {
    if (window.GC_STATE.hasJumped) {
        if (Math.abs(window.scrollY - window.GC_STATE.lastScrollPos) < 100) {
            window.GC_STATE.hasJumped = false;
            updateHUDVisibility();
        }
    }
});

// --- 3. DATA LOADING ---
async function loadChapter(channelID, targetMsg = null, autoFilter = null) {
    const logEntry = activeCampaign.logs.find(l => l.channelID === channelID);
    if (!logEntry) return;

    const jsonUrl = `${remoteBase}${activeCampaign.paths.json}${logEntry.fileName}`;
    const output = document.getElementById('viewer-output');
    output.innerHTML = "<em>Decrypting Data Stream...</em>";

    try {
        const response = await fetch(jsonUrl);
        const data = await response.json();
        fullData = Array.isArray(data) ? data : (data.messages || []);
        fullData.sort((a, b) => a.timestamp.localeCompare(b.timestamp));

        updateBreadcrumb(logEntry.title);

        // --- FIX: Trust the registry ID as the Main Frequency ---
        mainChannelId = channelID; 
        window.GC_STATE.currentMainChannelId = mainChannelId;

        // Map Names: Start with the Parent, then the Registry, then Message Meta
        window.channelMap = { [mainChannelId]: logEntry.title.toUpperCase() }; 

        if (logEntry.threads) {
            logEntry.threads.forEach(t => {
                window.channelMap[t.threadID] = t.displayName.toUpperCase();
            });
        }

        fullData.forEach(m => {
            // Capture any thread names that might be in the JSON but not the registry
            if (m.thread && m.thread.id && !window.channelMap[m.thread.id]) {
                window.channelMap[m.thread.id] = m.thread.name.toUpperCase();
            }
            // Handle Type 18 (Thread Creation) where content IS the name
            else if (m.type === 18 && m.content && !window.channelMap[m.id]) {
                window.channelMap[m.id] = m.content.toUpperCase();
            }
        });

        buildFrequencyBar();
        renderFeed(autoFilter || 'all');

        if (targetMsg) {
            requestAnimationFrame(() => { setTimeout(() => jumpToMessage(targetMsg), 500); });
        } else {
            window.scrollTo(0, 0);
        }
    } catch (err) {
        output.innerHTML = `<b style="color:red">DATA CORRUPTION: ${err.message}</b>`;
    }
}

/**
 * Core rendering engine for the log viewer.
 * Manages frequency shifts, timeline filtering, and URL state.
 */
function renderFeed(filterId) {
    const output = document.getElementById('viewer-output');
    if (!output) return;
    
    // Clear the stage
    output.innerHTML = "";

    // Establish context from the registry/state
    const logEntry = activeCampaign.logs.find(l => l.channelID === window.GC_STATE.currentMainChannelId) || activeCampaign.logs[0];
    const mainID = window.GC_STATE.currentMainChannelId;
    
    // UI Labeling
    const displayTitle = (filterId === 'all') ? "Combined Feed" : (window.channelMap[filterId] || "PRIMARY FREQUENCY");
    updateBreadcrumb(logEntry.title, filterId !== 'all' ? displayTitle : null);

    // --- 1. FREQUENCY TRACKING INITIALIZATION ---
    // We initialize with the mainChannelId to suppress a redundant banner 
    // at the very top of the primary narrative channel.
    let lastRenderedChannelId = mainID;

    // --- 2. TIMELINE FILTERING ---
    // 0: Default, 18: Thread Created, 19: Reply, 21: Thread Starter
    const validTypes = [0, 18, 19, 21];
    const filteredTimeline = fullData.filter(m => validTypes.includes(m.type));

    // --- 3. URL HASH SYNCHRONIZATION ---
    const { messageId } = getUrlContext();
    const newHash = messageId ? `${filterId}:${messageId}` : filterId;
    if (window.location.hash !== `#${newHash}`) {
        history.replaceState(null, null, `?c=${campaignSlug}#${newHash}`);
    }

    // --- 4. RENDER LOOP ---
    filteredTimeline.forEach(msg => {
        // A. Resolve the "Actual Frequency" for this line
        // Type 21/18 are 'Bridge' messages that exist in the parent but belong to the thread context.
        let actualChannel = msg.channel_id;
        
        if (msg.type === 21 && msg.thread && msg.thread.id) {
            actualChannel = msg.thread.id;
        } else if (msg.type === 18) {
            actualChannel = msg.id; // In Type 18, the Message ID is the Thread ID
        }
        
        // B. Determine Visibility
        let shouldShowContent = false;
        let shouldShowTransition = false;

        if (filterId === 'all') {
            shouldShowContent = true;
            shouldShowTransition = (actualChannel !== lastRenderedChannelId);
        } else if (filterId === mainID) {
            // "Primary Only" View
            if (actualChannel === mainID) {
                shouldShowContent = true;
            } else {
                // Show a transition block to indicate a thread branched off here
                shouldShowTransition = (actualChannel !== lastRenderedChannelId);
            }
        } else {
            // "Specific Frequency" View
            if (actualChannel === filterId) {
                shouldShowContent = true;
            } else if (actualChannel === mainID) {
                // Show a transition block to indicate we returned to the parent channel
                shouldShowTransition = (actualChannel !== lastRenderedChannelId);
            }
        }

        // C. Render Frequency Shift Banner
        if (shouldShowTransition && lastRenderedChannelId !== null) {
            const shiftName = window.channelMap[actualChannel] || "PRIMARY FREQUENCY";
            const transition = document.createElement('div');
            transition.className = 'channel-transition';
            transition.innerHTML = `📡 FREQUENCY SHIFT >> ${shiftName}`;
            
            // Interaction: Switch context and jump to the message that triggered the shift
            transition.onclick = () => {
                renderFeed(actualChannel);
                requestAnimationFrame(() => {
                    setTimeout(() => jumpToMessage(msg.id), 150);
                });
            };
            output.appendChild(transition);
            lastRenderedChannelId = actualChannel;
        }

        // D. Render Message Object
        if (shouldShowContent) {
            const group = renderMessageObject(msg, logEntry);
            output.appendChild(group);
            // Update tracker so we don't spam banners for consecutive messages in the same thread
            lastRenderedChannelId = actualChannel;
        }
    });

    // Finalize: Trigger lazy loader for avatars
    silentLoadAvatars();
}

/**
 * Enhanced Object Renderer to handle System/Thread messages.
 */
function renderMessageObject(msg, logEntry) {
    const isParentNSFW = logEntry.isNSFW || (logEntry.tags && logEntry.tags.includes('nsfw'));
    let isThreadNSFW = false;
    if (logEntry.threads && msg.channel_id !== logEntry.channelID) {
        const thread = logEntry.threads.find(t => t.threadID === msg.channel_id);
        if (thread) {
            isThreadNSFW = thread.isNSFW || (thread.tags && thread.tags.includes('nsfw'));
        }
    }
    const isChannelNSFW = isParentNSFW || isThreadNSFW;
    const isPostNSFW = detectNSFW(msg);
    const isCurrentMsgNSFW = isChannelNSFW || isPostNSFW;
    
    const isCurrentlyBlurred = isCurrentMsgNSFW && !window.GC_STATE.nsfwEnabled;
    const nsfwClass = isCurrentlyBlurred ? 'nsfw-blur' : (isCurrentMsgNSFW ? 'nsfw-blur off' : '');
    
    const group = document.createElement('div');
    group.className = 'message-group';
    group.id = `msg-${msg.id}`;

    // Handle System Message Styling (Type 18)
    if (msg.type === 18) {
        group.classList.add('system-message');
    }

    const avatarUrl = msg.author.avatar 
        ? `${remoteBase}${activeCampaign.paths.avatars}${msg.author.id}/${msg.author.avatar}.png` 
        : `${remoteBase}${activeCampaign.paths.avatars}default.png`;

    // Actual Channel ID for the Link Icon
    const linkChannel = (msg.type === 21 && msg.thread) ? msg.thread.id : msg.channel_id;

    group.innerHTML = `
        <div class="avatar-container">
            <img src="" data-src="${avatarUrl}" class="avatar lazy-load">
        </div>
        <div class="msg-body">
            <div class="msg-header">
                <span class="username">${msg.userName || msg.author.username}</span>
                ${isCurrentMsgNSFW ? `<span class="nsfw-badge" onclick="handleNSFWClick()">NSFW</span>` : ''}
                <span class="timestamp">${new Date(msg.timestamp).toLocaleString()}</span>
                <a href="javascript:void(0)" class="copy-link-icon" onclick="copyMsgLink(event, '${msg.id}', '${linkChannel}')">🔗</a>
            </div>
            <div class="msg-content ${nsfwClass}">${parseMarkdown(msg.content)}</div>
            ${renderAttachments(msg, logEntry)} 
            ${renderEmbeds(msg, logEntry)}
        </div>
    `;
    return group;
}

function parseMarkdown(text) {
    if (!text) return "";
    let html = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const emojiBase = `${remoteBase}${activeCampaign.paths.emoji}`;
    const tripleTick = "\x60\x60\x60";
    const codeBlockRegex = new RegExp(tripleTick + '(?:[a-z]+)?\\n?([\\s\\S]+?)\\n?' + tripleTick, 'g');
    html = html.replace(codeBlockRegex, '<pre><code>$1</code></pre>');
    html = html.split('\n').map(line => line.startsWith('&gt; ') ? `<blockquote>${line.substring(5)}</blockquote>` : line).join('\n');
    html = html.replace(/^# (.*$)/gm, '<h1>$1</h1>').replace(/^## (.*$)/gm, '<h2>$1</h2>').replace(/^### (.*$)/gm, '<h3>$1</h3>').replace(/^#### (.*$)/gm, '<h4>$1</h4>').replace(/^##### (.*$)/gm, '<h5>$1</h5>').replace(/^###### (.*$)/gm, '<h6>$1</h6>').replace(/^-# (.*$)/gm, '<small class="subtext">$1</small>');
    
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, label, rawUrl) => {
        let cleanUrl = rawUrl.trim().replace(/^&lt;/, "").replace(/&gt;$/, "").replace(/&amp;/g, "&");
        return `<a href="${cleanUrl}" target="_blank" class="external-link">${label}</a>`;
    });

    html = html.replace(/(?:&lt;#(\d+)&gt;|https?:\/\/discord\.com\/channels\/\d+\/(\d+)(?:\/(\d+))?)/g, (match, mentionId, urlChanId, urlMsgId) => {
        const id = mentionId || urlChanId;
        const res = resolveInternalLinkData(id);
        if (res.found) {
            const targetHash = (res.filterId !== 'all') ? res.filterId : res.parentId;
            const jumpId = urlMsgId ? `:${urlMsgId}` : "";
            const href = `${window.site_baseurl}/logs?c=${res.slug}#${targetHash}${jumpId}`;
            const breadcrumb = `${res.isExternal ? res.campaignName + ' > ' : ''}${res.locationName}`;
            return `<a href="${href}" class="channel-link"># ${breadcrumb}${urlMsgId ? ' ✉️' : ''}</a>`;
        }
        return match;
    });

    const imgRegex = /(https?:\/\/[^\s<]+?\.(?:png|jpg|jpeg|gif|webp)[^\s<]*)/gi;
    html = html.replace(imgRegex, (url) => `<div class="attachment-item"><a href="${url}" target="_blank"><img src="${url}" class="log-image" loading="lazy"></a></div>`);
    
    // Vault Format Fix: !name!_id.png
    html = html.replace(/&lt;a?(:.*?:)(\d+)&gt;/g, (m, name, id) => `<img src="${emojiBase}!${name.replace(/:/g, '')}!_${id}.png" class="emoji" title="${name.replace(/:/g, '')}">`);
    
    return html.replace(/\*\*\*(.*?)\*\*\*/g, '<strong><i>$1</i></strong>').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\*(.*?)\*/g, '<i>$1</i>').replace(/`(.*?)`/g, '<code>$1</code>').replace(/\|\|(.*?)\|\|/g, '<span class="spoiler" onclick="this.classList.toggle(\'revealed\')">$1</span>').replace(/\n/g, '<br>').replace(/<\/blockquote><br>/g, '</blockquote>').replace(/<\/pre><br>/g, '</pre>');
}

function resolveInternalLinkData(id) {
    for (let slug in registry.campaigns) {
        const camp = registry.campaigns[slug];
        const chapter = camp.logs.find(l => l.channelID === id);
        if (chapter) return { found: true, slug, parentId: id, filterId: 'all', locationName: chapter.title, campaignName: camp.name, isExternal: (slug !== campaignSlug) };
        const logWithThread = camp.logs.find(l => l.threads && l.threads.some(t => t.threadID === id));
        if (logWithThread) {
            const thread = logWithThread.threads.find(t => t.threadID === id);
            return { found: true, slug, parentId: logWithThread.channelID, filterId: id, locationName: `${logWithThread.title} > ${thread.displayName}`, campaignName: camp.name, isExternal: (slug !== campaignSlug) };
        }
    }
    return { found: false };
}

function updateBreadcrumb(chapterTitle, threadTitle = null) {
    const chapEl = document.getElementById('active-chapter-name');
    const threadEl = document.getElementById('thread-indicator');
    if (!chapEl) return;
    let displayChapter = chapterTitle;
    let limit = 100;
    if (window.innerWidth < 600) limit = 22;
    else if (window.innerWidth < 950) limit = 35;
    if (displayChapter.length > limit) displayChapter = displayChapter.substring(0, limit - 3) + "...";
    chapEl.innerHTML = `${displayChapter.toUpperCase()} <span style="font-size: 0.7em; opacity: 0.5; margin-left: 5px;">▼</span>`;
    if (threadEl) {
        if (threadTitle) {
            let displayThread = threadTitle;
            if (window.innerWidth < 600 && displayThread.length > 20) displayThread = displayThread.substring(0, 17) + "...";
            threadEl.style.display = 'flex';
            threadEl.innerHTML = `<span class="nav-arrow">❯</span><span>${displayThread.toUpperCase()}</span>`;
        } else {
            threadEl.style.display = 'none';
        }
    }
}

function toggleChapterList() {
    const dropdown = document.getElementById('chapter-list-dropdown');
    if (dropdown) dropdown.style.display = dropdown.style.display === 'block' ? 'none' : 'block';
}

function buildFrequencyBar() {
    const target = document.getElementById('freq-buttons-target');
    const toggleBtn = document.getElementById('thread-toggle');
    const uniqueChannels = [...new Set(fullData.map(m => m.channel_id))].filter(id => id !== mainChannelId);
    if (uniqueChannels.length === 0) {
        if (toggleBtn) toggleBtn.style.display = 'none';
        return; 
    }
    if (toggleBtn) toggleBtn.style.display = 'block';
    let html = `<div class="comms-status">📡 Encrypted Frequencies:</div><button class="freq-btn" onclick="renderFeed('all')">COMBINED</button><button class="freq-btn" onclick="renderFeed('${mainChannelId}')">PRIMARY ONLY</button>`;
    uniqueChannels.forEach(id => { 
        html += `<button class="freq-btn" onclick="renderFeed('${id}')">${window.channelMap[id] || "SUB-CHANNEL"}</button>`; 
    });
    if (target) target.innerHTML = html;
}

function toggleFrequencies() {
    const bar = document.getElementById('frequency-selector');
    if (bar) bar.style.display = (bar.style.display === 'none' || bar.style.display === '') ? 'block' : 'none';
}

function renderAttachments(msg, logRef) {
    if (!msg.attachments || msg.attachments.length === 0 || !logRef) return "";
    const folder = logRef.fileName.replace('.json', '');
    const mediaReg = window.GC_STATE.mediaRegistry || [];
    const isParentNSFW = logRef.isNSFW || (logRef.tags && logRef.tags.includes('nsfw'));
    let isThreadNSFW = false;
    if (logRef.threads && msg.channel_id !== logRef.channelID) {
        const thread = logRef.threads.find(t => t.threadID === msg.channel_id);
        if (thread) {
            isThreadNSFW = thread.isNSFW || (thread.tags && thread.tags.includes('nsfw'));
        }
    }
    const isPostNSFW = detectNSFW(msg) || isParentNSFW || isThreadNSFW;
    let html = '<div class="msg-attachments">';
    msg.attachments.forEach(att => {
        const registryMatchPath = `${folder}/${att.filename}`;
        const src = `${window.GC_STATE.remoteBase}${window.GC_STATE.currentCampaign.paths.media}${folder}/${att.filename}`;
        const isFileNSFW = mediaReg.some(entry => entry.endsWith(registryMatchPath)) || isPostNSFW;
        const isImage = (att.content_type && att.content_type.startsWith('image/')) || /\.(jpg|jpeg|png|gif|webp)$/i.test(att.filename);
        if (isImage) {
            if (isFileNSFW && !window.GC_STATE.nsfwEnabled) {
                const warning = (window.GC_STATE.contentWarnings && window.GC_STATE.contentWarnings[registryMatchPath]) || "RESTRICTED DATA";
                html += `<div class="media-shield"><div class="shield-overlay"><span class="shield-icon">⚠</span><div class="shield-info"><span class="shield-label">ENCRYPTION ACTIVE</span><span class="shield-text">${warning.toUpperCase()}</span></div><button class="decrypt-btn" onclick="handleNSFWClick()">DECRYPT</button></div></div>`;
            } else {
                html += `<div class="attachment-item"><a href="${src}" target="_blank"><img src="${src}" class="log-image" loading="lazy"></a></div>`;
            }
        } else {
            html += `<div class="attachment-item"><a href="${src}" target="_blank" class="file-link">📄 ${att.filename}</a></div>`;
        }
    });
    return html + '</div>';
}

function renderEmbeds(msg, logRef) {
    if (!msg.embeds || msg.embeds.length === 0) return "";
    const folder = logRef.fileName.replace('.json', '');
    let html = '<div class="msg-embeds">';
    msg.embeds.forEach(embed => {
        if (embed.thumbnail && embed.thumbnail.url) {
            let src = embed.thumbnail.url;
            if (!src.startsWith('http')) src = `${remoteBase}${activeCampaign.paths.media}${folder}/${src}`;
            html += `<div class="embed-item"><a href="${embed.url || src}" target="_blank"><img src="${src}" class="log-image embed-img" loading="lazy"></a></div>`;
        }
    });
    return html + '</div>';
}

function jumpToMessage(msgId) {
    const target = document.getElementById(msgId.startsWith('msg-') ? msgId : `msg-${msgId}`);
    if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        target.classList.add('highlight-flash');
        setTimeout(() => target.classList.remove('highlight-flash'), 3000);
    }
}

function silentLoadAvatars() {
    document.querySelectorAll('.lazy-load').forEach(img => {
        const src = img.getAttribute('data-src');
        const probe = new Image();
        probe.src = src;
        probe.onload = () => { img.src = src; img.classList.add('loaded'); };
        probe.onerror = () => { img.src = `${remoteBase}${activeCampaign.paths.avatars}default.png`; };
    });
}

function copyMsgLink(event, msgId, actualChannel) {
    event.preventDefault();
    const url = `${window.location.origin}${window.site_baseurl}/logs?c=${window.GC_STATE.campaignSlug}#${actualChannel}:${msgId}`;
    navigator.clipboard.writeText(url).then(() => {
        const icon = event.target;
        const original = icon.innerText;
        icon.innerText = "COPIED";
        icon.style.color = "var(--sw-yellow)";
        setTimeout(() => { icon.innerText = original; icon.style.color = ""; }, 1500);
    });
}

document.addEventListener("DOMContentLoaded", init);
