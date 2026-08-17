        const API_PREFIX = "/api/gateway";
        let activeTab = 'dashboard';
        let loadedQueueItems = [];
        let loadedNativeCrons = [];
        let pendingCronDeleteId = null;
        let pollingInterval = null;
        let reviewFactoryToken = "";

        // ═════════════════════════════════════════════════════════════════
        // CANONICAL PRISMATIC DESIGN SYSTEM & THEME REFERENCE
        // Single source of truth for icons, surfaces, badges, and tokens
        // ═════════════════════════════════════════════════════════════════
        const PRISMATIC_THEME = {
            icons: {
                file: function(ext = "") {
                    const clean = String(ext || "").toLowerCase().replace(/^\./, "");
                    if (["py", "js", "ts", "jsx", "tsx", "sh", "json", "html", "css", "ps1", "c", "cpp", "go", "rs", "java"].includes(clean)) {
                        return `<svg class="w-4 h-4 text-sky-500 dark:text-sky-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/></svg>`;
                    }
                    if (["md", "txt", "log", "rst", "doc", "pdf"].includes(clean)) {
                        return `<svg class="w-4 h-4 text-indigo-500 dark:text-indigo-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>`;
                    }
                    if (["env", "yml", "yaml", "toml", "ini", "conf", "config"].includes(clean)) {
                        return `<svg class="w-4 h-4 text-emerald-500 dark:text-emerald-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>`;
                    }
                    if (["png", "jpg", "jpeg", "gif", "svg", "ico", "webp"].includes(clean)) {
                        return `<svg class="w-4 h-4 text-purple-500 dark:text-purple-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>`;
                    }
                    return `<svg class="w-4 h-4 text-slate-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"/></svg>`;
                },
                folder: function(isOpen = false) {
                    return isOpen
                        ? `<svg class="w-4 h-4 text-amber-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M5 19a2 2 0 01-2-2V7a2 2 0 012-2h4l2 2h4a2 2 0 012 2v1M5 19h14a2 2 0 002-2v-5a2 2 0 00-2-2H9a2 2 0 00-2 2v5a2 2 0 01-2 2z"/></svg>`
                        : `<svg class="w-4 h-4 text-amber-500/90 dark:text-amber-400/90 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/></svg>`;
                },
                lock: `<svg class="w-4 h-4 text-cyan-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"/></svg>`,
                unlock: `<svg class="w-4 h-4 text-emerald-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 11V7a4 4 0 118 0m-4 8v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2z"/></svg>`,
                shield: `<svg class="w-4 h-4 text-amber-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>`,
                heartbeat: `<svg class="w-3.5 h-3.5 text-emerald-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4.318 6.318a4.5 4.5 0 000 6.364L12 20.364l7.682-7.682a4.5 4.5 0 00-6.364-6.364L12 7.636l-1.318-1.318a4.5 4.5 0 00-6.364 0z"/></svg>`,
                warning: `<svg class="w-3.5 h-3.5 text-amber-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>`,
                externalLink: `<svg class="w-3 h-3 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>`,
                linear: `<svg class="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" viewBox="0 0 24 24" fill="currentColor"><path d="M3.52 1.48L1.48 3.52l18.96 18.96 2.04-2.04L3.52 1.48zM1.48 10.52L10.52 1.48l1.44 1.44-9.04 9.04-1.44-1.44zM13.48 22.52l9.04-9.04-1.44-1.44-9.04 9.04 1.44 1.44z"/></svg>`,
                github: `<svg class="w-3.5 h-3.5 text-slate-300 flex-shrink-0" viewBox="0 0 24 24" fill="currentColor"><path fill-rule="evenodd" clip-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"/></svg>`,
                kanban: `<svg class="w-3.5 h-3.5 text-cyan-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2"/></svg>`,
                audit: `<svg class="w-3.5 h-3.5 text-indigo-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"/></svg>`,
                ttl: `<svg class="w-3.5 h-3.5 text-cyan-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>`,
                inspect: `<svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>`,
                trash: `<svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>`,
                refresh: `<svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>`,
                robot: `<svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/></svg>`,
                agent: `<svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>`,
                branch: `<svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M7 7h.01M7 3a4 4 0 00-4 4v10a4 4 0 004 4h10a4 4 0 004-4V7a4 4 0 00-4-4H7z"/></svg>`,
            },
            badge: function(type, label) {
                const key = String(type || "").toUpperCase();
                if (key.includes("MUTATION")) {
                    return `<span class="px-2 py-0.5 rounded text-[9px] font-mono font-bold uppercase bg-rose-950/60 text-rose-300 border border-rose-800/60">${escapeHtml(label || "Exclusive Mutation")}</span>`;
                }
                if (key.includes("REFACTOR")) {
                    return `<span class="px-2 py-0.5 rounded text-[9px] font-mono font-bold uppercase bg-cyan-950/60 text-cyan-300 border border-cyan-800/60">${escapeHtml(label || "Safe Refactor")}</span>`;
                }
                if (key.includes("READ")) {
                    return `<span class="px-2 py-0.5 rounded text-[9px] font-mono font-bold uppercase bg-indigo-950/60 text-indigo-300 border border-indigo-800/60">${escapeHtml(label || "Reentrant Read")}</span>`;
                }
                return `<span class="px-2 py-0.5 rounded text-[9px] font-mono font-bold uppercase bg-slate-800 text-slate-300 border border-slate-700">${escapeHtml(label || key)}</span>`;
            }
        };

        let agentStatusCache = { agents: [], status_counts: {}, evidence: {}, source: "not-loaded" };

        function escapeHtml(value) {
            return String(value ?? "").replace(/[&<>'"]/g, (ch) => ({
                "&": "&amp;",
                "<": "&lt;",
                ">": "&gt;",
                "'": "&#39;",
                '"': "&quot;",
            }[ch]));
        }

        function agentStatusTone(status) {
            const key = String(status || "unknown").toLowerCase().replace(/-/g, "_");
            const tones = {
                active: { dot: "bg-emerald-500 status-pulse", chip: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" },
                idle: { dot: "bg-slate-500", chip: "bg-slate-800 text-slate-400 border-slate-700/50" },
                queue_starved: { dot: "bg-amber-500", chip: "bg-amber-500/10 text-amber-400 border-amber-500/20" },
                awaiting_user_feedback: { dot: "bg-cyan-500", chip: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20" },
                completed_recently: { dot: "bg-indigo-500", chip: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20" },
                errored: { dot: "bg-rose-500", chip: "bg-rose-500/10 text-rose-400 border-rose-500/20" },
                churning: { dot: "bg-orange-500", chip: "bg-orange-500/10 text-orange-400 border-orange-500/20" },
                launch_failing: { dot: "bg-rose-500", chip: "bg-rose-500/10 text-rose-400 border-rose-500/20" },
            };
            return tones[key] || { dot: "bg-slate-500", chip: "bg-slate-800 text-slate-400 border-slate-700/50" };
        }

        function agentStatusLabel(status) {
            return String(status || "unknown").replace(/_/g, " ").toUpperCase();
        }

        function renderAgentGovernanceStatus(payload) {
            const container = document.getElementById("agent-governance-status");
            if (!container) return;
            const agents = payload?.agents || [];
            if (!agents.length) {
                container.innerHTML = `<div class="text-slate-500 italic">No Kai/Fred governance packets found. No synthetic fallback rendered.</div>`;
                return;
            }
            container.innerHTML = agents.map(agent => {
                const blockedEffects = Object.entries(agent.side_effect_policy || {})
                    .filter(([, allowed]) => !allowed)
                    .map(([name]) => name)
                    .join(", ") || "none";
                const links = (agent.proof_links || []).slice(0, 3).map(link => {
                    const href = String(link.href || "");
                    const safeHref = href.startsWith("http") || href.startsWith("/") ? href : `file://${href}`;
                    return `<a class="text-cyan-400 hover:text-cyan-300 underline decoration-cyan-500/30" href="${escapeHtml(safeHref)}" target="_blank" rel="noreferrer">${escapeHtml(link.label || 'proof')}</a>`;
                }).join(" · ") || `<span class="text-slate-500">No proof link recorded</span>`;
                const approvalGateCount = (agent.approval_gates || []).length;
                const auditEventCount = (agent.audit_events || []).length;
                const durability = agent.durability_status?.state || "unknown";
                const portability = agent.portability_readiness?.state || "unknown";
                return `
                    <div class="rounded-xl border border-slate-800 bg-slate-950/60 p-3 space-y-2" data-agent-governance-row="${escapeHtml(agent.agent)}">
                        <div class="flex items-center justify-between gap-2">
                            <div class="font-bold text-slate-200 uppercase">${escapeHtml(agent.name || agent.agent)}</div>
                            <span class="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase border border-cyan-500/20 text-cyan-300 bg-cyan-500/10">${escapeHtml(agent.lane_status)}</span>
                        </div>
                        <div class="grid grid-cols-1 gap-1 font-mono text-[10px] text-slate-400">
                            <div><span class="text-slate-500 uppercase">Current:</span> ${escapeHtml(agent.task_detail?.current_task || agent.current_task)}</div>
                            <div><span class="text-slate-500 uppercase">Last:</span> ${escapeHtml(agent.task_detail?.last_task || agent.last_task)}</div>
                            <div><span class="text-slate-500 uppercase">Audit:</span> ${escapeHtml(agent.audit_result)} · events ${escapeHtml(auditEventCount)}</div>
                            <div><span class="text-slate-500 uppercase">Proof:</span> ${links} · ${escapeHtml(agent.proof_result)} · ${escapeHtml(agent.proof_marker)}</div>
                            <div><span class="text-slate-500 uppercase">Approval Gates:</span> ${escapeHtml(approvalGateCount)} · blocked real effects: ${escapeHtml(blockedEffects)}</div>
                            <div><span class="text-slate-500 uppercase">Durability:</span> ${escapeHtml(durability)} · <span class="text-slate-500 uppercase">Portability:</span> ${escapeHtml(portability)}</div>
                            <div class="text-[9px] text-amber-300/80">${escapeHtml(agent.interim_source_label || payload.source || 'interim-source')}</div>
                        </div>
                    </div>
                `;
            }).join("");
        }

        async function loadAgentGovernanceStatus() {
            const container = document.getElementById("agent-governance-status");
            if (!container) return;
            try {
                const res = await fetch("/api/gateway/agents/governance-status");
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                renderAgentGovernanceStatus(await res.json());
            } catch (err) {
                container.innerHTML = `<div class="text-rose-400 italic">Agent governance status unavailable: ${escapeHtml(err.message || err)}</div>`;
            }
        }

        // Theme Management
        function toggleTheme() {
            const isLight = document.body.classList.toggle("light-mode");
            localStorage.setItem("theme", isLight ? "light" : "dark");
            updateThemeIcon(isLight);
        }
        function updateThemeIcon(isLight) {
            const icon = document.getElementById("theme-icon");
            if (isLight) {
                icon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"></path>';
            } else {
                icon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364-6.364l-.707.707M6.343 17.657l-.707.707m0-12.728l.707.707m12.728 12.728l.707.707M12 8a4 4 0 100 8 4 4 0 000-8z"></path>';
            }
        }
        function applyTheme() {
            const urlParams = new URLSearchParams(window.location.search);
            const queryTheme = urlParams.get("theme");
            if (queryTheme === "light" || queryTheme === "dark") {
                localStorage.setItem("theme", queryTheme);
            }
            const saved = localStorage.getItem("theme");
            if (saved === "light") {
                document.body.classList.add("light-mode");
                updateThemeIcon(true);
            } else {
                document.body.classList.remove("light-mode");
                updateThemeIcon(false);
            }
        }

        // Tab Switching
        function switchTab(tab, event = null, updateHistory = true) {
            if (event && event.preventDefault) {
                event.preventDefault();
            }
            activeTab = tab;
            
            if (updateHistory && window.history && window.history.pushState) {
                const targetPath = tab === "dashboard" ? "/" : `/${tab}`;
                const search = window.location.search || "";
                if (window.location.pathname !== targetPath) {
                    window.history.pushState({ tab }, "", targetPath + search);
                }
            }

            // Toggle Tab Buttons
            const tabs = ['dashboard', 'telemetry', 'merge', 'review-factory', 'workspaces', 'skills', 'signals', 'swarmproof', 'pwp', 'plugins', 'crons', 'quota', 'foundation', 'settings'];
            tabs.forEach(t => {
                const btn = document.getElementById(`tab-btn-${t}`);
                const sec = document.getElementById(`section-${t}`);
                
                if (t === tab) {
                    if (btn) btn.className = "px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-wider border transition-all duration-200 bg-indigo-600/10 text-indigo-400 border-indigo-500/20 hover:bg-indigo-600/20";
                    if (sec) sec.classList.remove("hidden");
                } else {
                    if (btn) btn.className = "px-4 py-2 rounded-lg text-xs font-bold uppercase tracking-wider border transition-all duration-200 bg-transparent text-slate-400 border-transparent hover:bg-slate-800/50 hover:text-slate-200";
                    if (sec) sec.classList.add("hidden");
                }
            });
            
            fetchData();
            if (tab === 'crons') {
                loadNativeCrons();
            } else if (tab === 'settings') {
                fetchSettingsData();
            } else if (tab === 'review-factory') {
                loadReviewFactory();
            } else if (tab === 'skills') {
                renderSkillsView();
            } else if (tab === 'workspaces') {
                renderWorkspacesView();
            } else if (tab === 'swarmproof') {
                renderSwarmProofView();
            }
        }

        async function fetchSettingsData() {
            try {
                await loadSettingsCredentials();
            } catch (err) {
                console.error("fetchSettingsData error:", err);
            }
        }

        async function saveSettingsCredentials() {
            const keys = ["GOOGLE_SA_JSON", "GA4_ACCOUNT_ID", "GTM_ACCOUNT_ID", "GSC_VERIFICATION_TOKEN", "CLOUDFLARE_API_TOKEN", "VERCEL_TOKEN", "GITHUB_TOKEN", "LINEAR_API_KEY"];
            const payload = {};
            keys.forEach(k => {
                const el = document.getElementById(`setting-${k}`);
                if (el && el.value.trim()) {
                    payload[k] = el.value.trim();
                }
            });
            try {
                const res = await fetch("/api/settings/credentials", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                showToast("Settings & credentials saved successfully!");
                await fetchSettingsData();
            } catch (err) {
                showToast(`Save failed: ${err.message}`, true);
            }
            if (tab === 'review-factory') {
                loadReviewFactory();
            }
        }

        function reviewFactoryHeaders() {
            return reviewFactoryToken
                ? { "Authorization": `Bearer ${reviewFactoryToken}` }
                : {};
        }

        function setReviewFactoryStatus(message, tone = "slate") {
            const statusEl = document.getElementById("rf-queue-status");
            if (!statusEl) return;
            statusEl.textContent = message;
            const tones = {
                slate: "text-slate-400",
                amber: "text-amber-300",
                emerald: "text-emerald-300",
                rose: "text-rose-300",
            };
            statusEl.className = `text-xs font-mono ${tones[tone] || tones.slate}`;
        }

        function reviewFactoryStateCount(byState, names) {
            return names.reduce((total, name) => total + Number(byState[name] || 0), 0);
        }

        function renderReviewFactoryQueue(queue) {
            const byState = queue.by_state || {};
            const values = {
                "stat-rf-queued": reviewFactoryStateCount(byState, ["queued"]),
                "stat-rf-verifying": reviewFactoryStateCount(byState, ["verifying", "claimed"]),
                "stat-rf-review-ready": reviewFactoryStateCount(byState, ["review_ready", "merge_ready"]),
                "stat-rf-merged": reviewFactoryStateCount(byState, ["merged"]),
            };
            Object.entries(values).forEach(([id, value]) => {
                const element = document.getElementById(id);
                if (element) element.textContent = String(value);
            });
        }

        function renderReviewFactoryJobs(jobs) {
            const body = document.getElementById("rf-jobs-tbody");
            if (!body) return;
            if (!jobs.length) {
                body.innerHTML = '<tr><td colspan="6" class="py-6 text-center text-slate-500 italic">No review factory jobs queued.</td></tr>';
                return;
            }
            body.innerHTML = jobs.map(job => {
                const id = escapeHtml(job.review_job_id || "unknown");
                const commit = escapeHtml((job.candidate_commit || "—").slice(0, 12));
                return `<tr tabindex="0" data-rf-job-id="${id}" class="cursor-pointer border-b border-slate-800/40 hover:bg-slate-900/60 focus:bg-slate-900/60 focus:outline-none">
                    <td class="py-2.5 px-3 font-mono text-cyan-300 whitespace-nowrap">${id}</td>
                    <td class="py-2.5 px-3 text-slate-200 whitespace-nowrap">${escapeHtml(job.task_id || "—")}</td>
                    <td class="py-2.5 px-3 text-slate-300">${escapeHtml(job.risk_tier ?? "—")}</td>
                    <td class="py-2.5 px-3 text-slate-300 whitespace-nowrap">${escapeHtml(job.state || "unknown")}</td>
                    <td class="py-2.5 px-3 text-slate-300 whitespace-nowrap">${escapeHtml(job.witnesses || "0/0")}</td>
                    <td class="py-2.5 px-3 font-mono text-slate-400 whitespace-nowrap">${commit}</td>
                </tr>`;
            }).join("");
            body.querySelectorAll("[data-rf-job-id]").forEach(row => {
                const open = () => showRFJob(row.dataset.rfJobId || "");
                row.addEventListener("click", open);
                row.addEventListener("keydown", event => {
                    if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        open();
                    }
                });
            });
        }

        async function loadReviewFactory() {
            setReviewFactoryStatus("Loading live data…", "slate");
            try {
                const headers = reviewFactoryHeaders();
                const [queueResponse, jobsResponse] = await Promise.all([
                    fetch("/api/review-factory/queue", { headers }),
                    fetch("/api/review-factory/jobs?limit=50", { headers }),
                ]);
                if (!queueResponse.ok || !jobsResponse.ok) {
                    const status = queueResponse.ok ? jobsResponse.status : queueResponse.status;
                    throw new Error(status === 401 ? "authentication_rejected" : `http_${status}`);
                }
                const [queue, jobs] = await Promise.all([
                    queueResponse.json(),
                    jobsResponse.json(),
                ]);
                renderReviewFactoryQueue(queue);
                renderReviewFactoryJobs(jobs.jobs || []);
                const count = Number(jobs.count || 0);
                const authSuffix = reviewFactoryToken ? " (Authenticated)" : "";
                setReviewFactoryStatus(`Live · ${count} jobs${authSuffix}`, "emerald");
            } catch (error) {
                const message = String(error?.message || error);
                setReviewFactoryStatus(
                    message === "authentication_rejected" ? "Authentication rejected" : "Live data unavailable",
                    "rose",
                );
                renderReviewFactoryJobs([]);
            }
        }

        async function connectReviewFactory() {
            const input = document.getElementById("rf-auth-token");
            const supplied = input ? input.value.trim() : "";
            if (supplied) reviewFactoryToken = supplied;
            if (input) input.value = "";
            await loadReviewFactory();
        }

        async function showRFJob(jobId) {
            if (!reviewFactoryToken || !jobId) return;
            const modal = document.getElementById("rf-job-modal");
            const title = document.getElementById("rf-modal-title");
            const body = document.getElementById("rf-modal-body");
            if (!modal || !title || !body) return;
            modal.classList.remove("hidden");
            title.textContent = `Review Job ${jobId}`;
            body.textContent = "Loading authenticated job detail…";
            try {
                const response = await fetch(
                    `/api/review-factory/job/${encodeURIComponent(jobId)}`,
                    { headers: reviewFactoryHeaders() },
                );
                if (!response.ok) throw new Error(`http_${response.status}`);
                const jobData = await response.json();
                body.innerHTML = `
                    <div class="space-y-4">
                        <div class="flex items-center justify-between gap-2 border-b border-slate-800 pb-3">
                            <span class="text-xs font-bold text-indigo-400 uppercase">State: ${escapeHtml(jobData.state || 'unknown')}</span>
                            <div class="flex gap-2">
                                <button onclick="approveRFJobMerge('${encodeURIComponent(jobId)}')" class="bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-3 py-1.5 rounded transition text-xs">Approve &amp; Merge</button>
                                <button onclick="rejectRFJobRepair('${encodeURIComponent(jobId)}')" class="bg-rose-600 hover:bg-rose-500 text-white font-bold px-3 py-1.5 rounded transition text-xs">Request Repair</button>
                            </div>
                        </div>
                        <pre class="whitespace-pre-wrap break-words text-[11px] bg-slate-950 p-3 rounded-lg border border-slate-900">${escapeHtml(JSON.stringify(jobData, null, 2))}</pre>
                    </div>
                `;
            } catch (error) {
                body.textContent = `Job detail unavailable: ${String(error?.message || error)}`;
            }
        }

        async function approveRFJobMerge(encodedJobId) {
            const jobId = decodeURIComponent(encodedJobId);
            try {
                const response = await fetch(`/api/review-factory/job/${encodeURIComponent(jobId)}/authorize`, {
                    method: 'POST',
                    headers: reviewFactoryHeaders(),
                    body: JSON.stringify({ actor: 'operator' })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                showToast(`Authorized merge for job ${jobId}`);
                closeRFModal();
                await loadReviewFactory();
            } catch (err) {
                showToast(`Approve merge failed: ${err.message || err}`, true);
            }
        }

        async function rejectRFJobRepair(encodedJobId) {
            const jobId = decodeURIComponent(encodedJobId);
            try {
                const response = await fetch(`/api/review-factory/job/${encodeURIComponent(jobId)}/reject`, {
                    method: 'POST',
                    headers: reviewFactoryHeaders(),
                    body: JSON.stringify({ reason: 'Rejected from dashboard' })
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                showToast(`Rejected job ${jobId}; auto-repair queued.`);
                closeRFModal();
                await loadReviewFactory();
            } catch (err) {
                showToast(`Reject job failed: ${err.message || err}`, true);
            }
        }

        function closeRFModal() {
            const modal = document.getElementById("rf-job-modal");
            if (modal) modal.classList.add("hidden");
        }

        // Toasts
        function showToast(text, isError = false) {
            const toast = document.getElementById("toast");
            const toastText = document.getElementById("toast-text");
            toastText.textContent = text;
            if (isError) {
                toast.className = "fixed bottom-5 right-5 bg-rose-950 border border-rose-800 text-rose-200 px-4 py-3 rounded-lg shadow-xl text-xs font-semibold duration-300 pointer-events-none flex items-center space-x-2";
            } else {
                toast.className = "fixed bottom-5 right-5 bg-slate-900 border border-slate-800 text-slate-200 px-4 py-3 rounded-lg shadow-xl text-xs font-semibold duration-300 pointer-events-none flex items-center space-x-2";
            }
            toast.classList.remove("translate-y-20", "opacity-0");
            setTimeout(() => {
                toast.classList.add("translate-y-20", "opacity-0");
            }, 3000);
        }

        // Date Helpers
        function formatDate(val) {
            if (!val) return "N/A";
            if (typeof val === 'number') {
                return new Date(val * 1000).toLocaleString();
            }
            try {
                return new Date(val).toLocaleString();
            } catch (e) {
                return val;
            }
        }

        function formatQuotaPct(value) {
            const num = Number(value);
            if (value === null || value === undefined || Number.isNaN(num)) {
                return "—";
            }
            return `${num.toFixed(1)}%`;
        }

        function quotaStatusLabel(item) {
            if (item.exhausted) return "EXHAUSTED";
            const num = Number(item.remaining_pct);
            if (item.remaining_pct === null || item.remaining_pct === undefined || Number.isNaN(num)) {
                return "SYNCING";
            }
            if (num <= 1) return "BLOCKED";
            return "ACTIVE";
        }

        function formatAge(seconds) {
            const n = Number(seconds);
            if (!Number.isFinite(n) || n < 0) return "Freshness: unknown";
            if (n < 60) return `Freshness: ${Math.round(n)}s ago`;
            const mins = Math.floor(n / 60);
            if (mins < 60) return `Freshness: ${mins}m ago`;
            const hours = Math.floor(mins / 60);
            return `Freshness: ${hours}h ${mins % 60}m ago`;
        }

        function renderRecoverySurface(payload = {}) {
            const serviceName = payload.service_name || "prismatic-consumer.service";
            const systemdAvailable = payload.systemd_available !== false;
            const systemdActive = Boolean(payload.systemd_active);
            const heartbeat = payload.heartbeat || {};
            const pool = payload.pool_stats || {};
            const taxonomy = Array.isArray(payload.failure_taxonomy) ? payload.failure_taxonomy : [];
            const serviceState = !systemdAvailable ? "UNAVAILABLE" : (systemdActive ? "ACTIVE" : "OFFLINE");
            const heartbeatState = heartbeat.available === false ? "unavailable" : (heartbeat.exists ? heartbeat.status || "present" : "missing");
            const poolAvailable = pool.available !== false;
            const liveCount = poolAvailable ? (pool.live_count ?? "—") : "—";
            const dlqCount = poolAvailable ? (pool.total_skipped_dlq ?? "—") : "—";

            const setText = (id, value) => {
                const el = document.getElementById(id);
                if (el) el.textContent = value;
            };

            setText("recovery-service", serviceName);
            setText("recovery-service-state", serviceState);
            setText("recovery-heartbeat", heartbeatState);
            setText("recovery-heartbeat-path", heartbeat.available === false ? "No durable heartbeat producer configured" : `Heartbeat ${heartbeat.status || "present"}; age ${heartbeat.age_seconds ?? "unknown"}s`);
            setText("recovery-live-count", liveCount);
            setText("recovery-dlq-count", dlqCount);
            setText("recovery-pool-state", poolAvailable ? `Systemd ${serviceState.toLowerCase()}, heartbeat ${heartbeatState}` : `Systemd ${serviceState.toLowerCase()}; durable pool snapshot unavailable`);
            setText("recovery-taxonomy-count", `${taxonomy.length} classes`);
            setText(
                "recovery-taxonomy-sample",
                taxonomy.length
                    ? taxonomy.slice(0, 3).map(item => item.label || item.code || item.name || "unlabeled").join(" · ")
                    : "No taxonomy labels reported"
            );
        }

        async function refreshRecoveryStatus() {
            const res = await fetch(`${API_PREFIX}/recovery/status`);
            if (!res.ok) {
                showToast("Recovery status refresh failed", true);
                return;
            }
            const payload = await res.json();
            renderRecoverySurface(payload);
            showToast("Recovery status refreshed");
        }

        // Node detail helper
        async function showAgentDetail(id) {
            let agent = (agentStatusCache.agents || []).find(a => a.id === id);
            try {
                const res = await fetch(`/api/gateway/agents/${encodeURIComponent(id)}`);
                if (res.ok) {
                    const detail = await res.json();
                    agent = detail.agent || detail;
                }
            } catch (err) {
                console.warn("agent detail fetch failed", err);
            }
            if (!agent) {
                showToast(`No live agent evidence for ${id}`, true);
                return;
            }

            document.getElementById("agent-detail-placeholder").classList.add("hidden");
            const card = document.getElementById("agent-detail-card");
            card.classList.remove("hidden");

            const tone = agentStatusTone(agent.status);
            const issue = agent.current_issue || "No current issue";
            const activity = agent.last_activity_at ? formatDate(agent.last_activity_at) : "No activity timestamp";
            const source = agent.source || agentStatusCache.source || "live agent status";

            document.getElementById("agent-detail-dot").className = `w-3 h-3 rounded-full ${tone.dot}`;
            document.getElementById("agent-detail-name").textContent = agent.name || id;
            document.getElementById("agent-detail-role").textContent = agent.role || agent.kind || "Agent";
            document.getElementById("agent-detail-task").textContent = issue;
            document.getElementById("agent-detail-seen").textContent = activity;
            document.getElementById("agent-detail-config").textContent = `${source} · queue ${agent.queue_depth ?? 0} · branch ${agent.current_branch || 'unknown'}`;

            const tag = document.getElementById("agent-detail-status-tag");
            tag.textContent = agentStatusLabel(agent.status);
            tag.className = `px-2 py-0.5 rounded text-[9px] uppercase font-bold border ${tone.chip}`;
        }

        // Webhook simulator POST
        async function simulateWebhook(e) {
            e.preventDefault();
            const ticket = document.getElementById("sim-ticket").value.trim();
            const agent = document.getElementById("sim-agent").value;
            const title = document.getElementById("sim-title").value.trim();
            
            // Build Linear Issue Payload matching the server format
            const payload = {
                action: "update",
                type: "Issue",
                data: {
                    id: `sim-${Date.now()}`,
                    title: title,
                    identifier: ticket,
                    labels: [{ name: `agent:${agent}` }]
                }
            };
            
            try {
                const r = await fetch("/webhooks/linear", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (r.ok) {
                    showToast("Test event successfully queued!");
                    addLocalSignal("Linear", `Inbound webhook simulated for ticket ${ticket}`);
                    fetchData();
                } else {
                    showToast("Failed to queue simulated event", true);
                }
            } catch (err) {
                showToast("Network error", true);
            }
        }

        let activeSignalsAgent = 'all';
        let latestSignalsPayload = { items: [], by_agent: {}, counts: {} };

        function setSignalsAgent(agent) {
            activeSignalsAgent = agent || 'all';
            document.querySelectorAll('.signals-agent-tab').forEach(btn => {
                const active = btn.dataset.agentTab === activeSignalsAgent;
                btn.className = `signals-agent-tab px-3 py-1.5 rounded-lg text-xs font-bold uppercase border transition ${active ? 'bg-indigo-600/20 text-indigo-300 border-indigo-500/30' : 'bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-200'}`;
            });
            renderSignalPanes(latestSignalsPayload);
        }

        function addLocalSignal(source, text, severity) {
            const container = document.getElementById("signals-log-box") || document.getElementById("signals-console-stream");
            if (!container) return;
            const time = new Date().toLocaleTimeString();
            const div = document.createElement("div");
            div.className = "text-slate-300 border-b border-slate-900/60 pb-1.5 mb-1.5 flex flex-wrap items-baseline gap-1.5 signal-log-item";
            const sev = String(severity || "info").toLowerCase();
            const sevColor = sev.includes("error") ? "text-rose-400" : sev.includes("warn") ? "text-amber-400" : sev.includes("lease") ? "text-cyan-400" : "text-indigo-400";
            div.innerHTML = `<span class="text-slate-500 font-bold flex-shrink-0 text-[10px]">[${time}]</span> <span class="${sevColor} font-semibold flex-shrink-0 px-1.5 py-0.2 rounded bg-slate-900 border border-slate-800">[${escapeHtml(source)}]</span> <span class="text-slate-300 break-all">${escapeHtml(text)}</span>`;
            
            // Remove initial placeholder text if present
            if (container.children.length === 1 && container.children[0].classList.contains("italic")) {
                container.innerHTML = "";
            }
            container.appendChild(div);
            if (typeof signalStreamPaused === "undefined" || !signalStreamPaused) {
                const scrollParent = document.getElementById("signals-console-stream") || container;
                scrollParent.scrollTop = scrollParent.scrollHeight;
            }
            const countEl = document.getElementById("signals-stream-count");
            if (countEl) {
                countEl.textContent = container.children.length;
            }
        }

        // Badges
        function getAgentBadge(name) {
            if (!name) return '<span class="text-xs text-slate-600">None</span>';
            const clean = name.toLowerCase();
            const colors = {
                agy: "bg-amber-500/10 text-amber-400 border-amber-500/20",
                jules: "bg-orange-500/10 text-orange-400 border-orange-500/20",
                fred: "bg-sky-500/10 text-sky-400 border-sky-500/20",
                ned: "bg-purple-500/10 text-purple-400 border-purple-500/20",
                kai: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
                codex: "bg-rose-500/10 text-rose-400 border-rose-500/20"
            };
            const cls = colors[clean] || "bg-slate-800 text-slate-300 border-slate-700/50";
            return '<span class="px-2 py-0.5 rounded text-[10px] font-bold border capitalize ' + cls + '">' + clean + '</span>';
        }

        function getStatusBadge(status) {
            const clean = (status || "").toLowerCase();
            if (clean === "completed" || clean === "success") {
                return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Success</span>';
            }
            if (clean === "failed" || clean === "error") {
                return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-500/10 text-rose-400 border border-rose-500/20">Failed</span>';
            }
            if (clean === "processing") {
                return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-orange-500/10 text-orange-400 border border-orange-500/20 status-pulse">Processing</span>';
            }
            return '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/10 text-amber-400 border border-amber-500/20">Pending</span>';
        }

        // Webhook queue details modal
        function openModal(id) {
            const item = loadedQueueItems.find(x => x.id === id);
            if (!item) return;
            
            document.getElementById("modal-id-badge").textContent = `ID: ${item.id}`;
            document.getElementById("modal-agent").innerHTML = getAgentBadge(item.agent_name);
            document.getElementById("modal-action").textContent = item.action || "update";
            document.getElementById("modal-status").innerHTML = getStatusBadge(item.dispatch_status);
            document.getElementById("modal-queued").textContent = formatDate(item.queued_at);
            
            try {
                const parsed = JSON.parse(item.payload);
                document.getElementById("modal-payload").textContent = JSON.stringify(parsed, null, 2);
            } catch (e) {
                document.getElementById("modal-payload").textContent = item.payload;
            }
            
            document.getElementById("modal-container").classList.remove("hidden");
        }

        function closeModal() {
            document.getElementById("modal-container").classList.add("hidden");
        }

        function cronBadge(value) {
            const v = String(value || "unknown");
            const classes = {
                active: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20",
                paused: "bg-amber-500/10 text-amber-300 border-amber-500/20",
                deactivated: "bg-slate-700/40 text-slate-300 border-slate-600/40",
                deleted: "bg-rose-500/10 text-rose-300 border-rose-500/20",
                queued: "bg-indigo-500/10 text-indigo-300 border-indigo-500/20",
                out_of_queue: "bg-slate-800 text-slate-400 border-slate-700"
            };
            return `<span class="px-2 py-0.5 rounded text-[10px] uppercase font-bold border ${classes[v] || classes.out_of_queue}">${v.replace('_', ' ')}</span>`;
        }

        let loadedPluginGovernance = null;
        let loadedPluginJobs = null;
        let loadedPluginArtifacts = null;
        let loadedPluginAuditEvents = null;

        async function loadPluginGovernance() {
            try {
                const [govRes, jobsRes, artifactsRes, auditRes] = await Promise.all([
                    fetch('/api/plugins/governance'),
                    fetch('/api/plugins/jobs'),
                    fetch('/api/plugins/artifacts'),
                    fetch('/api/plugins/audit-events?limit=50')
                ]);
                if (!govRes.ok) throw new Error(`governance HTTP ${govRes.status}`);
                if (!jobsRes.ok) throw new Error(`jobs HTTP ${jobsRes.status}`);
                if (!artifactsRes.ok) throw new Error(`artifacts HTTP ${artifactsRes.status}`);
                if (!auditRes.ok) throw new Error(`audit HTTP ${auditRes.status}`);
                loadedPluginGovernance = await govRes.json();
                loadedPluginJobs = await jobsRes.json();
                loadedPluginArtifacts = await artifactsRes.json();
                loadedPluginAuditEvents = await auditRes.json();
                renderPluginDashboardChrome(null);
                renderPluginGovernance();
                renderPluginPolicy();
                renderPluginJobs();
                renderPluginArtifacts();
                renderPluginAuditEvents();
                renderPluginJobTimeline();
                renderPluginArtifactInventory();
                renderPluginApprovalControls();
                renderPluginDetailDrawer();
            } catch (err) {
                renderPluginDashboardChrome(err);
                const summary = document.getElementById('plugin-governance-summary');
                if (summary) summary.innerHTML = `<div class="col-span-full text-rose-300 bg-rose-950/30 border border-rose-900/60 rounded-xl p-3">Plugin governance refresh failed: ${err.message}</div>`;
            }
        }

        function pluginBadge(label, color = 'slate') {
            const palette = {
                emerald: 'bg-emerald-950/40 text-emerald-300 border-emerald-800/60',
                amber: 'bg-amber-950/40 text-amber-300 border-amber-800/60',
                rose: 'bg-rose-950/40 text-rose-300 border-rose-800/60',
                indigo: 'bg-indigo-950/40 text-indigo-300 border-indigo-800/60',
                slate: 'bg-slate-900/80 text-slate-300 border-slate-700/70'
            };
            return `<span class="inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-semibold ${palette[color] || palette.slate}">${label}</span>`;
        }

        function pluginDashboardCounts() {
            const governance = (loadedPluginGovernance && loadedPluginGovernance.plugins) || [];
            const jobs = (loadedPluginJobs && loadedPluginJobs.jobs) || [];
            const artifacts = (loadedPluginArtifacts && loadedPluginArtifacts.artifacts) || [];
            return {
                plugins: governance.length,
                jobs: jobs.length,
                artifacts: artifacts.length,
                approvals: jobs.filter(job => job.status === 'needs_approval' || job.approval_state === 'pending').length + artifacts.filter(artifact => artifact.approval_state === 'pending').length,
                blockers: governance.filter(plugin => (plugin.production_blockers || []).length).length + jobs.filter(job => job.status === 'failed').length + artifacts.filter(artifact => artifact.approval_state === 'rejected').length
            };
        }

        function copyDashboardCommand(command) {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(command);
            }
            renderPluginDetailDrawer({
                title: 'Copied CLI command',
                body: `<code class="block mt-2 rounded bg-slate-900 p-2 text-emerald-200 overflow-x-auto">${command}</code>`
            });
        }

        function renderPluginDashboardChrome(error) {
            const counts = pluginDashboardCounts();
            const health = document.getElementById('plugin-dashboard-health-cards');
            if (health) {
                health.innerHTML = [
                    ['Plugins', counts.plugins, 'Manifest catalog loaded', counts.plugins ? 'emerald' : 'amber'],
                    ['Jobs', counts.jobs, 'Durable audit trail', counts.jobs ? 'indigo' : 'slate'],
                    ['Artifacts', counts.artifacts, 'Provenance inventory', counts.artifacts ? 'indigo' : 'slate'],
                    ['Needs attention', counts.approvals + counts.blockers, 'Approvals and blockers', counts.approvals + counts.blockers ? 'amber' : 'emerald'],
                ].map(([label, value, help, color]) => `<div class="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                    <div class="flex items-center justify-between gap-2"><span class="text-slate-500 uppercase tracking-wider font-bold">${label}</span>${pluginBadge(color === 'emerald' ? 'healthy' : color === 'amber' ? 'review' : 'ready', color)}</div>
                    <div class="mt-2 text-2xl font-black text-slate-100">${value}</div>
                    <div class="mt-1 text-slate-500">${help}</div>
                </div>`).join('');
            }
            const firstRun = document.getElementById('plugin-first-run-empty-state');
            if (firstRun) firstRun.classList.toggle('hidden', counts.plugins + counts.jobs + counts.artifacts > 0);
            const errorEl = document.getElementById('plugin-error-explanation');
            if (errorEl) {
                errorEl.classList.toggle('hidden', !error);
                errorEl.innerHTML = error ? `<div class="font-bold uppercase tracking-wider">Dashboard refresh error</div><p class="mt-1 text-red-200">${error.message || error}. Check that the Gateway is running, then copy the smoke command below.</p><button onclick="copyDashboardCommand('python scripts/release_smoke.py')" class="copy-cli-command mt-2 rounded border border-red-400/30 px-2 py-1 text-red-100">Copy release smoke</button>` : '';
            }
            const hints = document.getElementById('plugin-onboarding-hints');
            if (hints) {
                const cards = [
                    ['Start local', 'Run the public smoke before sharing the dashboard.', 'python scripts/public_launch_smoke.py', 'docs/public-onboarding.md'],
                    ['Prove release path', 'Validate package metadata, plugins, API, and dashboard markers.', 'python scripts/release_smoke.py', 'docs/release-process.md'],
                    ['Govern plugins', 'Preview policy before starting risky jobs or exporting artifacts.', 'python scripts/plugin_architecture catalog', 'docs/prismatic-plugin-architecture.md'],
                ];
                hints.innerHTML = cards.map(([title, body, command, doc]) => `<div class="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
                    <div class="font-bold text-slate-200">${title}</div><p class="mt-1 text-slate-500">${body}</p>
                    <div class="mt-3 flex flex-wrap gap-2"><button onclick="copyDashboardCommand('${command}')" class="copy-cli-command rounded border border-indigo-500/30 px-2 py-1 text-indigo-200">Copy CLI</button><a class="dashboard-doc-link rounded border border-slate-700 px-2 py-1 text-slate-300 hover:text-white" href="/${doc}" target="_blank">Docs</a></div>
                </div>`).join('');
            }
            const docs = document.getElementById('plugin-doc-links');
            if (docs) {
                docs.innerHTML = ['docs/public-onboarding.md', 'docs/prismatic-plugin-architecture.md', 'docs/release-process.md', 'docs/public-security-readiness.md'].map(doc => `<a class="dashboard-doc-link rounded-full border border-slate-700 px-3 py-1 text-slate-300 hover:border-indigo-500/50 hover:text-indigo-200" href="/${doc}" target="_blank">${doc.replace('docs/', '')}</a>`).join('');
            }
        }

        function renderPluginDetailDrawer(selection = null) {
            const drawer = document.getElementById('plugin-detail-drawer');
            if (!drawer) return;
            if (selection && selection.title) {
                drawer.innerHTML = `<div class="text-slate-500 uppercase tracking-wider font-bold">${selection.title}</div><div class="mt-2 text-slate-300">${selection.body || ''}</div>`;
                return;
            }
            const plugin = ((loadedPluginGovernance && loadedPluginGovernance.plugins) || [])[0];
            const job = ((loadedPluginJobs && loadedPluginJobs.jobs) || [])[0];
            const artifact = ((loadedPluginArtifacts && loadedPluginArtifacts.artifacts) || [])[0];
            drawer.innerHTML = `<div class="flex items-start justify-between gap-3"><div><div class="text-slate-500 uppercase tracking-wider font-bold">Plugin detail drawer</div><div class="mt-1 text-lg font-black text-slate-100">${(plugin && plugin.name) || 'No plugin selected'}</div></div>${plugin ? pluginBadge(plugin.readiness_state || 'unknown', plugin.readiness_state === 'ready' ? 'emerald' : 'amber') : pluginBadge('first run', 'amber')}</div>
                <div class="mt-4 space-y-3">
                    <div><div class="text-slate-500 uppercase tracking-wider font-bold">Latest job</div><div class="mt-1 text-slate-300">${job ? `${job.job_id || job.id} · ${job.status}` : 'No job timeline yet.'}</div></div>
                    <div><div class="text-slate-500 uppercase tracking-wider font-bold">Latest artifact</div><div class="mt-1 text-slate-300">${artifact ? `${artifact.artifact_id || artifact.id} · ${artifact.publish_state || artifact.approval_state}` : 'No artifact inventory yet.'}</div></div>
                    <button onclick="copyDashboardCommand('python scripts/release_smoke.py')" class="copy-cli-command rounded-lg border border-indigo-500/30 bg-indigo-600/10 px-3 py-2 text-xs font-semibold text-indigo-200 hover:bg-indigo-600/20">Copy release smoke</button>
                </div>`;
        }

        function renderPluginJobTimeline() {
            const el = document.getElementById('plugin-job-timeline');
            if (!el) return;
            const jobs = ((loadedPluginJobs && loadedPluginJobs.jobs) || []).slice(0, 5);
            if (!jobs.length) {
                el.innerHTML = `<div class="text-slate-500 uppercase tracking-wider font-bold">Job timeline</div><p class="mt-2 text-slate-500">No plugin jobs yet. Create one through the API or run a plugin smoke to populate the durable audit trail.</p>`;
                return;
            }
            el.innerHTML = `<div class="flex items-center justify-between"><div class="text-slate-500 uppercase tracking-wider font-bold">Job timeline</div><button onclick="copyDashboardCommand('curl -s http://127.0.0.1:9000/api/plugins/jobs')" class="copy-cli-command text-indigo-300 hover:text-indigo-200">Copy API</button></div><ol class="mt-3 space-y-2">${jobs.map(job => `<li class="border-l border-indigo-500/30 pl-3"><button onclick="renderPluginDetailDrawer({title: 'Job ${job.job_id || job.id}', body: 'Status: ${job.status || 'unknown'}<br>Approval: ${job.approval_state || 'unknown'}'})" class="text-left text-slate-200 hover:text-indigo-200">${job.action || job.kind || 'plugin job'} · ${pluginBadge(job.status || 'unknown', job.status === 'completed' ? 'emerald' : job.status === 'failed' ? 'rose' : 'amber')}</button><div class="text-slate-500">${formatDate(job.updated_at || job.created_at)}</div></li>`).join('')}</ol>`;
        }

        function renderPluginArtifactInventory() {
            const el = document.getElementById('plugin-artifact-inventory');
            if (!el) return;
            const artifacts = ((loadedPluginArtifacts && loadedPluginArtifacts.artifacts) || []);
            if (!artifacts.length) {
                el.innerHTML = `<div class="p-3"><div class="text-slate-500 uppercase tracking-wider font-bold">Artifact inventory</div><p class="mt-2 text-slate-500">No artifacts registered yet. Approved plugin outputs will appear here with provenance, publish state, and export history.</p></div>`;
                return;
            }
            el.innerHTML = `<div class="p-3 border-b border-slate-800 flex items-center justify-between"><div class="text-slate-500 uppercase tracking-wider font-bold">Artifact inventory</div><button onclick="copyDashboardCommand('curl -s http://127.0.0.1:9000/api/plugins/artifacts')" class="copy-cli-command text-indigo-300 hover:text-indigo-200">Copy API</button></div><div class="divide-y divide-slate-800/60">${artifacts.slice(0, 6).map(artifact => `<button onclick="renderPluginDetailDrawer({title: 'Artifact ${artifact.artifact_id || artifact.id}', body: 'Type: ${artifact.artifact_type || 'unknown'}<br>Publish: ${artifact.publish_state || 'unknown'}<br>Approval: ${artifact.approval_state || 'unknown'}'})" class="block w-full text-left p-3 hover:bg-slate-900/50"><div class="font-semibold text-slate-200">${artifact.artifact_type || 'artifact'} · ${artifact.artifact_id || artifact.id}</div><div class="mt-1 text-slate-500">${artifact.plugin_name || 'unknown plugin'} · ${artifact.publish_state || artifact.approval_state || 'unreviewed'}</div></button>`).join('')}</div>`;
        }

        function renderPluginApprovalControls() {
            const el = document.getElementById('plugin-approval-controls');
            if (!el) return;
            const jobs = ((loadedPluginJobs && loadedPluginJobs.jobs) || []).filter(job => job.status === 'needs_approval' || job.approval_state === 'pending');
            const artifacts = ((loadedPluginArtifacts && loadedPluginArtifacts.artifacts) || []).filter(artifact => artifact.approval_state === 'pending');
            if (!jobs.length && !artifacts.length) {
                el.innerHTML = `<div class="text-slate-500 uppercase tracking-wider font-bold">Approval controls</div><p class="mt-2 text-slate-500">No pending approvals. Policy-gated jobs and artifacts will show approve/reject actions here.</p>`;
                return;
            }
            const jobButtons = jobs.slice(0, 3).map(job => `<div class="rounded-lg border border-amber-500/20 p-2"><div class="font-semibold text-amber-200">Job ${job.job_id || job.id}</div><div class="mt-2 flex gap-2"><button onclick="fetch('/api/plugins/jobs/${job.job_id || job.id}/approve', {method: 'POST'}).then(loadPluginGovernance)" class="rounded border border-emerald-500/30 px-2 py-1 text-emerald-200">Approve</button><button onclick="fetch('/api/plugins/jobs/${job.job_id || job.id}/reject', {method: 'POST'}).then(loadPluginGovernance)" class="rounded border border-rose-500/30 px-2 py-1 text-rose-200">Reject</button></div></div>`).join('');
            const artifactButtons = artifacts.slice(0, 3).map(artifact => `<div class="rounded-lg border border-amber-500/20 p-2"><div class="font-semibold text-amber-200">Artifact ${artifact.artifact_id || artifact.id}</div><div class="mt-2 flex gap-2"><button onclick="fetch('/api/plugins/artifacts/${artifact.artifact_id || artifact.id}/approve', {method: 'POST'}).then(loadPluginGovernance)" class="rounded border border-emerald-500/30 px-2 py-1 text-emerald-200">Approve</button><button onclick="fetch('/api/plugins/artifacts/${artifact.artifact_id || artifact.id}/reject', {method: 'POST'}).then(loadPluginGovernance)" class="rounded border border-rose-500/30 px-2 py-1 text-rose-200">Reject</button></div></div>`).join('');
            el.innerHTML = `<div class="text-slate-500 uppercase tracking-wider font-bold">Approval controls</div><div class="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2">${jobButtons}${artifactButtons}</div>`;
        }

        function renderPluginPolicy() {
            const jobs = (loadedPluginJobs && loadedPluginJobs.jobs) || [];
            const artifacts = (loadedPluginArtifacts && loadedPluginArtifacts.artifacts) || [];
            const jobPolicyBlocked = jobs.filter(job => (job.policy_result || {}).decision === 'block' || job.status === 'failed').length;
            const jobApprovalRequired = jobs.filter(job => job.status === 'needs_approval' || job.approval_state === 'pending').length;
            const artifactBlocked = artifacts.filter(artifact => artifact.approval_state === 'rejected' || artifact.publish_state === 'rejected' || ((artifact.policy_result || {}).decision === 'block')).length;
            const artifactApprovalRequired = artifacts.filter(artifact => artifact.approval_state === 'pending').length;
            const lastJob = jobs.find(job => job.policy_result);
            const lastArtifact = artifacts.find(artifact => artifact.policy_result);
            const lastPolicy = (lastJob && lastJob.policy_result) || (lastArtifact && lastArtifact.policy_result) || null;
            const summaryEl = document.getElementById('plugin-policy-summary');
            const decisionEl = document.getElementById('plugin-policy-decision');
            if (!summaryEl || !decisionEl) return;
            summaryEl.innerHTML = [
                ['Blocked jobs', jobPolicyBlocked, jobPolicyBlocked ? 'rose' : 'emerald'],
                ['Blocked artifacts', artifactBlocked, artifactBlocked ? 'rose' : 'emerald'],
                ['Job approvals', jobApprovalRequired, jobApprovalRequired ? 'amber' : 'emerald'],
                ['Artifact approvals', artifactApprovalRequired, artifactApprovalRequired ? 'amber' : 'emerald'],
                ['Policy API', '/api/plugins/policy/preview', 'indigo'],
            ].map(([label, value, color]) => `<div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3"><div class="text-slate-500 uppercase tracking-wider font-bold">${label}</div><div class="mt-1 text-slate-100 font-mono text-lg">${value}</div><div class="mt-2">${pluginBadge('Policy Enforcement', color)}</div></div>`).join('');
            const blockedReason = lastPolicy ? ((lastPolicy.blockers || [lastPolicy.reason || 'no blockers']).join('; ')) : 'No durable policy decisions yet. Use POST /api/plugins/policy/preview, job start, or artifact export to evaluate policy.';
            decisionEl.innerHTML = `<div class="flex items-center justify-between gap-3"><div><div class="uppercase tracking-wider text-slate-500 font-bold">Policy Enforcement</div><div class="mt-1 text-slate-300">Last policy decision: <span class="font-mono">${lastPolicy ? lastPolicy.decision : 'none'}</span></div><div class="mt-1 text-slate-500 blocked_reason">blocked_reason: ${blockedReason}</div></div>${pluginBadge(lastPolicy ? `policy: ${lastPolicy.decision}` : 'policy: idle', lastPolicy && lastPolicy.decision === 'block' ? 'rose' : lastPolicy && lastPolicy.decision === 'needs_approval' ? 'amber' : 'emerald')}</div>`;
        }

        function renderPluginAuditEvents() {
            const payload = loadedPluginAuditEvents || {};
            const summary = payload.summary || {};
            const events = payload.events || [];
            const el = document.getElementById('plugin-audit-events');
            if (!el) return;
            if (!events.length) {
                el.innerHTML = `<div class="p-3"><div class="flex items-center justify-between"><div class="text-slate-500 uppercase tracking-wider font-bold">Audit events</div><button onclick="copyDashboardCommand('curl -s http://127.0.0.1:9000/api/plugins/audit-events')" class="copy-cli-command text-indigo-300 hover:text-indigo-200">Copy API</button></div><p class="mt-2 text-slate-500">No plugin audit events yet. Create/start a job or register an artifact to populate the audit stream.</p></div>`;
                return;
            }
            const rows = events.slice(0, 8).map(event => `<tr class="border-t border-slate-800/70 hover:bg-slate-900/50"><td class="px-3 py-2 font-mono text-slate-400">${event.created_at || '—'}</td><td class="px-3 py-2 text-slate-200">${event.event_type || 'event'}</td><td class="px-3 py-2 text-slate-300">${event.plugin_name || '—'}</td><td class="px-3 py-2 text-slate-400">${event.audit_source || event.source || '—'}</td><td class="px-3 py-2 text-slate-500">${event.job_id || event.artifact_id || '—'}</td></tr>`).join('');
            el.innerHTML = `<div class="p-3 border-b border-slate-800 flex items-center justify-between"><div><div class="text-slate-500 uppercase tracking-wider font-bold">Audit events</div><div class="mt-1 text-slate-400">${summary.event_count || events.length} recent events · ${summary.job_event_count || 0} job · ${summary.artifact_event_count || 0} artifact</div></div><button onclick="copyDashboardCommand('curl -s http://127.0.0.1:9000/api/plugins/audit-events')" class="copy-cli-command text-indigo-300 hover:text-indigo-200">Copy API</button></div><table class="w-full text-left text-xs"><thead class="text-slate-500 uppercase"><tr><th class="px-3 py-2">Time</th><th class="px-3 py-2">Event</th><th class="px-3 py-2">Plugin</th><th class="px-3 py-2">Source</th><th class="px-3 py-2">Object</th></tr></thead><tbody>${rows}</tbody></table>`;
        }

        function renderPluginArtifacts() {
            const payload = loadedPluginArtifacts || {};
            const summary = payload.summary || {};
            const summaryEl = document.getElementById('plugin-artifact-summary');
            const tableEl = document.getElementById('plugin-artifacts-table');
            if (!summaryEl || !tableEl) return;
            const approval = summary.by_approval_state || {};
            const publish = summary.by_publish_state || {};
            summaryEl.innerHTML = [
                ['Artifacts', summary.artifact_count || 0, 'emerald'],
                ['Approved', approval.approved || 0, 'emerald'],
                ['Pending', approval.pending || 0, 'amber'],
                ['Publish ready', publish.publish_ready || 0, 'indigo'],
                ['Rejected', approval.rejected || 0, 'rose'],
            ].map(([label, value, color]) => `<div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3"><div class="text-slate-500 uppercase tracking-wider font-bold">${label}</div><div class="mt-1 text-slate-100 font-mono text-lg">${value}</div><div class="mt-2">${pluginBadge(label, color)}</div></div>`).join('');
            const rows = (payload.artifacts || []).slice(0, 12).map(artifact => `
                <tr class="border-t border-slate-800/80">
                    <td class="px-3 py-2 font-mono text-[11px] text-slate-300">${artifact.artifact_id}</td>
                    <td class="px-3 py-2 text-slate-300">${artifact.plugin_name || ''}</td>
                    <td class="px-3 py-2 text-slate-400">${artifact.artifact_type || ''}</td>
                    <td class="px-3 py-2">${pluginBadge(`approval: ${artifact.approval_state || 'unknown'}`, artifact.approval_state === 'approved' ? 'emerald' : artifact.approval_state === 'rejected' ? 'rose' : 'amber')}</td>
                    <td class="px-3 py-2">${pluginBadge(`publish: ${artifact.publish_state || 'unknown'}`, artifact.publish_state === 'publish_ready' ? 'indigo' : artifact.publish_state === 'rejected' ? 'rose' : 'slate')}</td>
                    <td class="px-3 py-2 font-mono text-[11px] text-slate-500">${artifact.sha256 ? artifact.sha256.slice(0, 12) : 'external/no-hash'}</td>
                    <td class="px-3 py-2 text-slate-500">${artifact.job_id || ''}</td>
                </tr>`).join('');
            tableEl.innerHTML = `<div class="px-3 py-2 border-b border-slate-800 text-xs font-bold uppercase tracking-wider text-slate-400">Universal Plugin Artifacts / Provenance Registry</div>
                <table class="min-w-full text-xs"><thead class="text-slate-500"><tr><th class="px-3 py-2 text-left">Artifact</th><th class="px-3 py-2 text-left">Plugin</th><th class="px-3 py-2 text-left">Type</th><th class="px-3 py-2 text-left">Approval</th><th class="px-3 py-2 text-left">Publish</th><th class="px-3 py-2 text-left">SHA256</th><th class="px-3 py-2 text-left">Job</th></tr></thead><tbody>${rows || '<tr><td colspan="7" class="px-3 py-4 text-slate-500">No plugin artifacts registered yet.</td></tr>'}</tbody></table>`;
        }

        function renderPluginJobs() {
            const payload = loadedPluginJobs || {};
            const summary = payload.summary || {};
            const summaryEl = document.getElementById('plugin-job-summary');
            const tableEl = document.getElementById('plugin-jobs-table');
            if (!summaryEl || !tableEl) return;
            summaryEl.innerHTML = [
                ['Jobs', summary.job_count || 0, 'indigo'],
                ['Events', summary.event_count || 0, 'slate'],
                ['Artifacts', summary.artifact_count || 0, 'emerald'],
                ['Needs approval', (summary.by_status || {}).needs_approval || 0, 'amber'],
                ['Failed', (summary.by_status || {}).failed || 0, 'rose'],
            ].map(([label, value, color]) => `<div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3"><div class="text-slate-500 uppercase tracking-wider font-bold">${label}</div><div class="mt-1 text-slate-100 font-mono text-lg">${value}</div><div class="mt-2">${pluginBadge(label, color)}</div></div>`).join('');
            const rows = (payload.jobs || []).slice(0, 12).map(job => `
                <tr class="border-t border-slate-800/80">
                    <td class="px-3 py-2 font-mono text-[11px] text-slate-300">${job.job_id}</td>
                    <td class="px-3 py-2 text-slate-300">${job.plugin_name}</td>
                    <td class="px-3 py-2 text-slate-400">${job.action}</td>
                    <td class="px-3 py-2">${pluginBadge(job.status || 'unknown', job.status === 'failed' || job.status === 'rejected' ? 'rose' : job.status === 'needs_approval' ? 'amber' : 'emerald')}</td>
                    <td class="px-3 py-2">${pluginBadge(`approval: ${job.approval_state || 'unknown'}`, job.approval_state === 'pending' ? 'amber' : job.approval_state === 'rejected' ? 'rose' : 'slate')}</td>
                    <td class="px-3 py-2 text-slate-500">${job.updated_at || ''}</td>
                </tr>`).join('');
            tableEl.innerHTML = `<div class="px-3 py-2 border-b border-slate-800 text-xs font-bold uppercase tracking-wider text-slate-400">Durable Plugin Jobs / Audit Trail</div>
                <table class="min-w-full text-xs"><thead class="text-slate-500"><tr><th class="px-3 py-2 text-left">Job</th><th class="px-3 py-2 text-left">Plugin</th><th class="px-3 py-2 text-left">Action</th><th class="px-3 py-2 text-left">Status</th><th class="px-3 py-2 text-left">Approval</th><th class="px-3 py-2 text-left">Updated</th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="px-3 py-4 text-slate-500">No plugin jobs recorded yet.</td></tr>'}</tbody></table>`;
        }

        function renderPluginGovernance() {
            const payload = loadedPluginGovernance || {};
            const summary = document.getElementById('plugin-governance-summary');
            const cards = document.getElementById('plugin-governance-cards');
            if (!summary || !cards) return;
            const s = payload.summary || {};
            summary.innerHTML = [
                ['Ready', s.ready || 0, 'emerald'],
                ['Warnings', s.warning || 0, 'amber'],
                ['Blocked', s.blocked || 0, 'rose'],
                ['High risk', s.high_risk || 0, 'rose'],
                ['Needs approval', s.requires_approval || 0, 'indigo'],
            ].map(([label, value, color]) => `<div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3"><div class="text-slate-500 uppercase tracking-wider font-bold">${label}</div><div class="mt-1 text-slate-100 font-mono text-lg">${value}</div><div class="mt-2">${pluginBadge(label, color)}</div></div>`).join('');
            cards.innerHTML = (payload.plugins || []).map(plugin => {
                const gov = plugin.governance || {};
                const blockers = gov.production_blockers || [];
                const coverage = gov.surface_coverage || {};
                const stateColor = gov.readiness_state === 'ready' ? 'emerald' : (gov.readiness_state === 'blocked' ? 'rose' : 'amber');
                const endpointLinks = (plugin.endpoints || []).slice(0, 4).map(ep => pluginBadge(`${ep.method || 'GET'} ${ep.path || ''}`, 'slate')).join('');
                return `<div class="bg-slate-950/70 border border-slate-800 rounded-xl p-4 text-xs space-y-3">
                    <div class="flex items-start justify-between gap-3">
                        <div><div class="text-slate-100 font-bold">${plugin.name}</div><div class="text-slate-500 mt-1">${plugin.plugin_type} · ${(plugin.categories || []).join(', ') || 'uncategorized'}</div></div>
                        <div class="flex gap-1 flex-wrap justify-end">${pluginBadge(gov.readiness_state || 'unknown', stateColor)}${pluginBadge(`risk: ${gov.risk_level || 'unknown'}`, gov.risk_level === 'high' || gov.risk_level === 'critical' ? 'rose' : 'indigo')}</div>
                    </div>
                    <div class="grid grid-cols-3 md:grid-cols-6 gap-2 text-center">
                        ${[['Tools', coverage.tools], ['MCP', coverage.mcp_servers], ['API', coverage.api_routes], ['Artifacts', coverage.artifact_types], ['Dashboard', coverage.dashboard_surfaces], ['Approvals', (gov.approval_gates || []).length]].map(([label, value]) => `<div class="bg-slate-900/70 border border-slate-800 rounded-lg p-2"><div class="text-slate-500 uppercase text-[9px]">${label}</div><div class="text-slate-200 font-mono">${value || 0}</div></div>`).join('')}
                    </div>
                    <div class="flex flex-wrap gap-1">${(gov.approval_gates || []).map(g => pluginBadge(g, 'indigo')).join('') || pluginBadge('no approval gates declared', 'amber')}</div>
                    <div class="flex flex-wrap gap-1">${endpointLinks || pluginBadge('no endpoints declared', 'amber')}</div>
                    <div class="space-y-1">${blockers.length ? blockers.map(b => `<div class="rounded-lg border ${b.severity === 'blocking' ? 'border-rose-900/70 bg-rose-950/30 text-rose-200' : 'border-amber-900/70 bg-amber-950/30 text-amber-200'} p-2"><span class="font-bold uppercase">${b.severity}</span>: ${b.message}</div>`).join('') : '<div class="rounded-lg border border-emerald-900/70 bg-emerald-950/30 text-emerald-200 p-2">No production blockers detected.</div>'}</div>
                </div>`;
            }).join('') || '<div class="text-slate-500 text-xs">No plugin manifests discovered.</div>';
        }

        let loadedPWPStatus = null;

        async function loadPWPStatus() {
            try {
                const res = await fetch('/api/pwp/status');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                loadedPWPStatus = await res.json();
                renderPWPStatus();
            } catch (err) {
                const summary = document.getElementById('pwp-summary');
                if (summary) summary.innerHTML = `<div class="col-span-full text-rose-300 bg-rose-950/30 border border-rose-900/60 rounded-xl p-3">PWP status refresh failed: ${err.message}</div>`;
            }
        }

        function pwpBadge(text, color = 'slate') {
            const classes = {
                slate: 'bg-slate-900/80 text-slate-300 border-slate-700',
                emerald: 'bg-emerald-950/40 text-emerald-300 border-emerald-800/70',
                amber: 'bg-amber-950/40 text-amber-300 border-amber-800/70',
                rose: 'bg-rose-950/40 text-rose-300 border-rose-800/70',
                indigo: 'bg-indigo-950/40 text-indigo-300 border-indigo-800/70'
            };
            return `<span class="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold ${classes[color] || classes.slate}">${text}</span>`;
        }

        function renderPWPStatus() {
            const payload = loadedPWPStatus || {};
            const summary = document.getElementById('pwp-summary');
            const blockers = document.getElementById('pwp-blockers');
            const caps = document.getElementById('pwp-capabilities');
            const connects = document.getElementById('pwp-connect-points');
            const disconnects = document.getElementById('pwp-disconnect-points');
            const tools = document.getElementById('pwp-tools');
            const lifecycleHistory = document.getElementById('pwp-lifecycle-history');
            const lifecycleDetail = document.getElementById('pwp-lifecycle-detail');
            if (!summary || !blockers || !caps || !connects || !disconnects || !tools || !lifecycleHistory || !lifecycleDetail) return;
            const hardBlockers = (payload.production_blockers || []).filter(b => b.severity === 'blocking').length;
            const warnings = (payload.production_blockers || []).filter(b => b.severity !== 'blocking').length;
            const statusColor = payload.connected ? 'emerald' : (hardBlockers ? 'rose' : 'amber');
            summary.innerHTML = [
                ['State', payload.state || 'unknown', statusColor],
                ['Capabilities', String((payload.capabilities || []).length), 'indigo'],
                ['Tools', String((payload.tool_names || []).length), 'slate'],
                ['Blockers', `${hardBlockers} hard / ${warnings} warn`, hardBlockers ? 'rose' : (warnings ? 'amber' : 'emerald')],
            ].map(([label, value, color]) => `<div class="bg-slate-950/70 border border-slate-800 rounded-xl p-3"><div class="text-slate-500 uppercase tracking-wider font-bold">${label}</div><div class="mt-1 text-slate-100 font-mono">${value}</div><div class="mt-2">${pwpBadge(payload.status || value, color)}</div></div>`).join('');
            blockers.innerHTML = (payload.production_blockers || []).length
                ? (payload.production_blockers || []).map(b => `<div class="rounded-xl border ${b.severity === 'blocking' ? 'border-rose-900/70 bg-rose-950/30 text-rose-200' : 'border-amber-900/70 bg-amber-950/30 text-amber-200'} p-3 text-xs"><span class="font-bold uppercase tracking-wider">${b.system || 'governance'}</span>: ${b.message}</div>`).join('')
                : '<div class="rounded-xl border border-emerald-900/70 bg-emerald-950/30 text-emerald-200 p-3 text-xs">No PWP production blockers detected.</div>';
            caps.innerHTML = (payload.capabilities || []).map(cap => `<div class="bg-slate-950/70 border border-slate-800 rounded-xl p-4 text-xs space-y-3"><div><div class="text-slate-100 font-bold">${cap.label}</div><div class="text-slate-500 mt-1">${cap.category}</div></div><p class="text-slate-300 leading-relaxed">${cap.description}</p><div class="flex flex-wrap gap-1">${(cap.governance || []).slice(0,4).map(g => pwpBadge(g, 'indigo')).join('')}</div></div>`).join('');
            connects.innerHTML = (payload.connect_points || []).map(item => `<li>${item}</li>`).join('');
            disconnects.innerHTML = (payload.disconnect_points || []).map(item => `<li>${item}</li>`).join('');
            tools.innerHTML = [...(payload.tool_names || []), ...(payload.workflows || [])].slice(0, 32).map(item => pwpBadge(item, 'slate')).join('');
            renderPWPLifecycleHistory(payload.lifecycle_summary || {}, lifecycleHistory, lifecycleDetail);
        }

        function renderPWPLifecycleHistory(lifecycle, historyEl, detailEl) {
            const jobs = ((lifecycle.history || {}).jobs || []);
            const artifacts = ((lifecycle.history || {}).artifacts || []);
            const latestArtifact = lifecycle.latest_artifact || artifacts[0] || null;
            if (!jobs.length && !artifacts.length) {
                historyEl.innerHTML = '<div class="p-4 text-slate-500">No PWP lifecycle runs yet. Click Run Demo to create a credential-free job, artifact, approval, publish/export history, and safe disconnect proof.</div>';
                detailEl.innerHTML = '<div class="text-slate-500 uppercase tracking-wider font-bold">Lifecycle detail</div><p class="mt-2 text-slate-400">PWP is the canonical reference plugin. The demo writes to the universal plugin job and artifact registries.</p>';
                return;
            }
            historyEl.innerHTML = jobs.slice(0, 8).map(job => `<button onclick="showPWPLifecycleDetail('${job.job_id}')" class="block w-full text-left p-4 hover:bg-slate-900/60 transition"><div class="flex flex-wrap items-center justify-between gap-2"><div class="font-semibold text-slate-200">${job.action || 'pwp job'} · ${job.job_id}</div>${pwpBadge(job.status || 'unknown', job.status === 'completed' ? 'emerald' : job.status === 'failed' ? 'rose' : 'amber')}</div><div class="mt-1 text-slate-500">Artifacts: ${(job.artifact_ids || []).length} · approval: ${job.approval_state || 'unknown'} · ${formatDate(job.updated_at || job.created_at)}</div></button>`).join('');
            detailEl.innerHTML = `<div class="text-slate-500 uppercase tracking-wider font-bold">Reference lifecycle proof</div><div class="mt-3 grid grid-cols-2 gap-2 text-xs"><div class="rounded-lg border border-slate-800 p-2"><div class="text-slate-500">Jobs</div><div class="text-lg font-black text-slate-100">${lifecycle.jobs_total || jobs.length}</div></div><div class="rounded-lg border border-slate-800 p-2"><div class="text-slate-500">Artifacts</div><div class="text-lg font-black text-slate-100">${lifecycle.artifacts_total || artifacts.length}</div></div></div><div class="mt-4 space-y-2 text-slate-300"><div>${pwpBadge('connect', 'emerald')} ${pwpBadge('job registry', 'indigo')} ${pwpBadge('provenance', 'indigo')}</div><div>${pwpBadge('approval before publish', 'amber')} ${pwpBadge('safe disconnect', 'emerald')}</div></div><div class="mt-4 text-slate-500">Latest artifact: ${latestArtifact ? `${latestArtifact.artifact_id} · ${latestArtifact.publish_state}` : 'none'}</div>`;
        }

        function showPWPLifecycleDetail(jobId) {
            const detailEl = document.getElementById('pwp-lifecycle-detail');
            const lifecycle = (loadedPWPStatus || {}).lifecycle_summary || {};
            const jobs = ((lifecycle.history || {}).jobs || []);
            const artifacts = ((lifecycle.history || {}).artifacts || []);
            const job = jobs.find(item => item.job_id === jobId);
            if (!detailEl || !job) return;
            const linkedArtifacts = artifacts.filter(item => (job.artifact_ids || []).includes(item.artifact_id));
            detailEl.innerHTML = `<div class="text-slate-500 uppercase tracking-wider font-bold">${job.job_id}</div><div class="mt-2 text-lg font-black text-slate-100">${job.action}</div><div class="mt-3 flex flex-wrap gap-2">${pwpBadge(job.status || 'unknown', job.status === 'completed' ? 'emerald' : 'amber')}${pwpBadge(job.approval_state || 'unknown', 'indigo')}</div><div class="mt-4 text-slate-400">Events: ${(job.events || []).length} · Artifacts: ${(job.artifact_ids || []).length}</div><div class="mt-4 space-y-2">${linkedArtifacts.map(artifact => `<div class="rounded-lg border border-slate-800 p-2"><div class="text-slate-200">${artifact.artifact_id}</div><div class="text-slate-500">${artifact.artifact_type} · ${artifact.approval_state} · ${artifact.publish_state}</div></div>`).join('') || '<div class="text-slate-500">No linked artifacts.</div>'}</div>`;
        }

        async function pwpAction(action) {
            const options = { method: 'POST' };
            if (action === 'lifecycle-demo') {
                options.headers = { 'Content-Type': 'application/json' };
                options.body = JSON.stringify({ actor: 'pwp-dashboard', disconnect_after: true });
            }
            const res = await fetch(`/api/pwp/${action}`, options);
            const payload = await res.json();
            if (action === 'lifecycle-demo') {
                await loadPWPStatus();
            } else {
                loadedPWPStatus = payload;
                renderPWPStatus();
            }
            showToast(`PWP ${action}: ${payload.ok === false ? 'failed' : (payload.status || payload.state || 'ok')}`, !res.ok);
        }

        async function loadNativeCrons() {
            const table = document.getElementById("native-crons-table");
            if (!table) return;
            table.innerHTML = '<tr><td colspan="6" class="p-4 text-slate-500">Loading native cron registry…</td></tr>';
            try {
                const res = await fetch('/native-crons?include_deleted=false');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                loadedNativeCrons = await res.json();
                renderNativeCrons();
            } catch (err) {
                table.innerHTML = `<tr><td colspan="6" class="p-4 text-rose-300">Failed to load native crons: ${err.message}</td></tr>`;
            }
        }

        function renderNativeCrons() {
            const table = document.getElementById("native-crons-table");
            const summary = document.getElementById("native-crons-summary");
            if (!table || !summary) return;
            const counts = loadedNativeCrons.reduce((acc, cron) => {
                acc[cron.state] = (acc[cron.state] || 0) + 1;
                return acc;
            }, {});
            summary.innerHTML = ['active', 'paused', 'deactivated', 'deleted'].map(key => `
                <div class="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
                    <div class="text-slate-500 uppercase tracking-wider font-bold">${key}</div>
                    <div class="text-xl font-bold text-slate-200 mt-1">${counts[key] || 0}</div>
                </div>
            `).join('');
            if (!loadedNativeCrons.length) {
                table.innerHTML = '<tr><td colspan="6" class="p-4 text-slate-500">No native crons registered.</td></tr>';
                return;
            }
            table.innerHTML = loadedNativeCrons.map(cron => {
                const last = cron.last_run_at ? `${formatDate(cron.last_run_at)}<br><span class="text-slate-500">${cron.last_status || 'unknown'}</span>` : '<span class="text-slate-500">Never</span>';
                const pauseResume = cron.state === 'paused'
                    ? `<button onclick="nativeCronAction('${cron.id}', 'resume')" class="text-emerald-300 hover:text-emerald-200">Resume</button>`
                    : `<button onclick="nativeCronAction('${cron.id}', 'pause')" class="text-amber-300 hover:text-amber-200">Pause</button>`;
                const activateDeactivate = cron.state === 'deactivated'
                    ? `<button onclick="nativeCronAction('${cron.id}', 'activate')" class="text-indigo-300 hover:text-indigo-200">Activate</button>`
                    : `<button onclick="nativeCronAction('${cron.id}', 'deactivate')" class="text-slate-300 hover:text-slate-100">Deactivate</button>`;
                return `
                    <tr class="hover:bg-slate-900/40">
                        <td class="p-3 align-top"><div class="font-bold text-slate-200">${cron.name}</div><div class="text-slate-500 mt-1">${cron.description || cron.id}</div><div class="text-slate-600 font-mono mt-1">${cron.id}</div></td>
                        <td class="p-3 align-top font-mono text-slate-300">${cron.schedule}</td>
                        <td class="p-3 align-top">${cronBadge(cron.state)}</td>
                        <td class="p-3 align-top">${cronBadge(cron.queue_state)}</td>
                        <td class="p-3 align-top text-slate-400">${last}</td>
                        <td class="p-3 align-top"><div class="flex flex-wrap gap-2 text-xs font-bold">${pauseResume}${activateDeactivate}<button onclick="nativeCronAction('${cron.id}', 'run')" class="text-indigo-300 hover:text-indigo-200">Run</button><button onclick="nativeCronAction('${cron.id}', 'recover')" class="text-cyan-300 hover:text-cyan-200">Recover</button><button onclick="openCronDeleteModal('${cron.id}')" class="text-rose-300 hover:text-rose-200">Delete</button></div></td>
                    </tr>
                `;
            }).join('');
        }

        async function nativeCronAction(cronId, action) {
            const res = await fetch(`/native-crons/${encodeURIComponent(cronId)}/action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action })
            });
            const payload = await res.json();
            if (!res.ok || payload.success === false) {
                showToast(payload.error || `Cron ${action} failed`, true);
                return;
            }
            showToast(`Cron ${action} complete`);
            await loadNativeCrons();
        }

        function openCronDeleteModal(cronId) {
            pendingCronDeleteId = cronId;
            const cron = loadedNativeCrons.find(item => item.id === cronId);
            document.getElementById("cron-delete-target").textContent = cron ? `${cron.name} (${cron.id})` : cronId;
            document.getElementById("cron-delete-modal").classList.remove("hidden");
        }

        function closeCronDeleteModal() {
            pendingCronDeleteId = null;
            document.getElementById("cron-delete-modal").classList.add("hidden");
        }

        async function confirmCronDeactivateFromModal() {
            if (!pendingCronDeleteId) return;
            const cronId = pendingCronDeleteId;
            closeCronDeleteModal();
            await nativeCronAction(cronId, 'deactivate');
        }

        async function confirmCronDeleteFromModal() {
            if (!pendingCronDeleteId) return;
            const cronId = pendingCronDeleteId;
            closeCronDeleteModal();
            await nativeCronAction(cronId, 'delete');
        }

        // Control Hooks
        async function controlDispatcher(action) {
            const loader = document.getElementById("dispatcher-loader");
            loader.classList.remove("hidden");
            try {
                const r = await fetch(`${API_PREFIX}/dispatcher/${action}`, { method: "POST" });
                if (r.ok) {
                    showToast(`Dispatcher command ${action}ed`);
                    setTimeout(fetchData, 1000);
                } else {
                    showToast("Control failed", true);
                }
            } catch (e) {
                showToast("Network error", true);
            } finally {
                loader.classList.add("hidden");
            }
        }

        async function retryTask(taskId) {
            try {
                const r = await fetch(`${API_PREFIX}/webhooks/queue/retry/${taskId}`, { method: "POST" });
                if (r.ok) {
                    showToast("Task reset to pending");
                    fetchData();
                } else {
                    showToast("Failed to retry task", true);
                }
            } catch (e) {
                showToast("Network error", true);
            }
        }

        async function purgeQueue() {
            if (!confirm("Are you sure you want to purge completed/failed tasks from the queue?")) return;
            try {
                const r = await fetch(`${API_PREFIX}/webhooks/queue/purge`, { method: "POST" });
                if (r.ok) {
                    showToast("Queue history purged");
                    fetchData();
                } else {
                    showToast("Failed to purge queue", true);
                }
            } catch (e) {
                showToast("Network error", true);
            }
        }

        // Searching / Filtering
        function filterQueue() {
            const query = document.getElementById("search-input").value.toLowerCase().trim();
            const tbody = document.getElementById("queue-tbody");
            
            const filtered = loadedQueueItems.filter(item => {
                const id = (item.identifier || "").toLowerCase();
                const agent = (item.agent_name || "").toLowerCase();
                const action = (item.action || "").toLowerCase();
                const status = (item.dispatch_status || "").toLowerCase();
                return id.includes(query) || agent.includes(query) || action.includes(query) || status.includes(query);
            });
            
            if (filtered.length === 0) {
                tbody.innerHTML = `<tr><td colspan="6" class="py-8 text-center text-slate-500 italic">No matching events</td></tr>`;
            } else {
                tbody.innerHTML = filtered.map(item => `
                    <tr onclick="openModal(${item.id})" class="border-b border-slate-800/50 hover:bg-slate-900/20 transition duration-150 cursor-pointer">
                        <td class="py-3 px-3 font-semibold text-slate-200">${item.identifier || "N/A"}</td>
                        <td class="py-3 px-3">${getAgentBadge(item.agent_name)}</td>
                        <td class="py-3 px-3 text-slate-400 capitalize text-xs">${item.action || "update"}</td>
                        <td class="py-3 px-3">${getStatusBadge(item.dispatch_status)}</td>
                        <td class="py-3 px-3 text-slate-500 text-xs">${formatDate(item.queued_at)}</td>
                        <td class="py-3 px-3 text-right">
                            <button onclick="event.stopPropagation(); retryTask(${item.id})" class="text-xs text-indigo-400 hover:text-indigo-300 font-semibold px-2 py-1 rounded bg-indigo-500/5 hover:bg-indigo-500/10 border border-indigo-500/10 transition">
                                Retry
                            </button>
                        </td>
                    </tr>
                `).join("");
            }
        }

        // Sparklines drawing helper
        function drawSparkline(values) {
            const max = Math.max(...values, 1.0);
            const height = 24;
            const width = 48;
            const step = width / (values.length - 1 || 1);
            
            const points = values.map((val, idx) => {
                const x = idx * step;
                const y = height - (val / max) * height;
                return x + "," + y;
            }).join(" ");
            
            return '<svg width="' + width + '" height="' + height + '"><polyline fill="none" stroke="#6366f1" stroke-width="1.5" points="' + points + '" /></svg>';
        }

        // Quotas poll hook
        async function pollQuota() {
            try {
                const r = await fetch(`${API_PREFIX}/quota/poll`, { method: "POST" });
                if (r.ok) {
                    showToast("Quota sync initiated");
                    setTimeout(fetchData, 2000);
                } else {
                    showToast("Sync failed", true);
                }
            } catch (err) {
                showToast("Network error", true);
            }
        }

        async function fetchFoundationData() {
            try {
                const res = await fetch(`${API_PREFIX}/foundation/peer_review`);
                if (res.ok) {
                    const data = await res.json();
                    document.getElementById("stat-foundation-jules").textContent = data.jules_count;
                    document.getElementById("stat-foundation-ned").textContent = data.ned_count;
                    document.getElementById("stat-foundation-agy").textContent = data.agy_count;
                    document.getElementById("stat-foundation-reviewer").textContent = data.current_agy_reviewer;
                    
                    // Update progress bar
                    const pct = Math.min(100, (data.jules_count / data.jules_limit) * 100);
                    document.getElementById("progress-foundation-jules").style.width = `${pct}%`;
                }
            } catch (err) {
                console.error("Error fetching foundation peer review data", err);
            }
        }

        async function triggerFoundationAction(action) {
            const consoleEl = document.getElementById("foundation-console");
            consoleEl.textContent = `Running ${action}... Please wait...\n`;
            try {
                const res = await fetch(`${API_PREFIX}/foundation/control/${action}`, { method: "POST" });
                if (res.ok) {
                    const data = await res.json();
                    if (data.status === "ok") {
                        showToast(`Action ${action} succeeded`);
                        consoleEl.textContent += `[SUCCESS] ${data.message}\n\nSTDOUT:\n${data.stdout || ''}\n\nSTDERR:\n${data.stderr || ''}`;
                        fetchData();
                    } else {
                        showToast(data.message || "Action failed", true);
                        consoleEl.textContent += `[ERROR] ${data.message}\n`;
                    }
                } else {
                    showToast(`Server returned error status`, true);
                    consoleEl.textContent += `[HTTP ERROR] Status: ${res.status}\n`;
                }
            } catch (err) {
                showToast("Network connection error", true);
                consoleEl.textContent += `[CONNECTION ERROR] ${err}\n`;
            }
        }

        function clearFoundationConsole() {
            document.getElementById("foundation-console").textContent = "Console cleared.\n";
        }

        async function loadCanonicalAgyActivity() {
            const summary = document.getElementById('agy-activity-summary');
            const runsEl = document.getElementById('agy-activity-runs');
            if (!summary || !runsEl) return;
            try {
                const res = await fetch('/api/gateway/agy/activity?limit=20');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const payload = await res.json();
                if (payload.status !== 'ok') {
                    summary.textContent = payload.reason || 'Canonical AGY activity is not configured.';
                    runsEl.innerHTML = '';
                    return;
                }
                const runs = payload.runs || [];
                const counts = payload.activity_counts || {};
                summary.textContent = `${runs.length} retained run(s) · working ${counts.working || 0} · quiet ${counts.quiet || 0} · suspect ${counts.suspect || 0} · no automatic runtime kill`;
                if (!runs.length) {
                    runsEl.innerHTML = '<div class="text-xs text-slate-500 italic">No canonical AGY run receipts yet.</div>';
                    return;
                }
                const tones = {
                    working: 'border-emerald-500/30 bg-emerald-500/5 text-emerald-200',
                    quiet: 'border-amber-500/30 bg-amber-500/5 text-amber-200',
                    suspect: 'border-rose-500/30 bg-rose-500/5 text-rose-200',
                    terminal: 'border-slate-700 bg-slate-900/60 text-slate-300',
                    starting: 'border-cyan-500/30 bg-cyan-500/5 text-cyan-200'
                };
                runsEl.innerHTML = runs.map(run => {
                    const activity = run.activity || {};
                    const metrics = activity.metrics || {};
                    const classification = activity.classification || 'unknown';
                    const tone = tones[classification] || tones.terminal;
                    const seen = activity.receipt_age_seconds == null ? 'no receipt' : `${Math.round(activity.receipt_age_seconds)}s ago`;
                    return `<div class="rounded-lg border p-3 ${tone}" data-agy-run="${escapeHtml(run.run_id)}">
                        <div class="flex items-center justify-between gap-2">
                            <span class="font-mono text-xs font-bold">${escapeHtml(run.run_id)}</span>
                            <span class="uppercase text-[10px] font-bold">${escapeHtml(classification)}</span>
                        </div>
                        <div class="mt-2 grid grid-cols-2 md:grid-cols-4 gap-2 text-[10px] text-slate-400">
                            <span>state ${escapeHtml(run.state)}</span>
                            <span>seen ${escapeHtml(seen)}</span>
                            <span>processes ${escapeHtml(metrics.process_count ?? 0)}</span>
                            <span>CPU ticks ${escapeHtml(metrics.cpu_ticks ?? 0)}</span>
                            <span>writes ${escapeHtml(metrics.write_bytes ?? 0)} B</span>
                            <span>artifacts ${escapeHtml(metrics.artifact_file_count ?? 0)}</span>
                            <span>quiet ${escapeHtml(Math.round(activity.quiet_seconds || 0))}s</span>
                            <span>verification ${escapeHtml(run.verification_status || 'pending')}</span>
                        </div>
                    </div>`;
                }).join('');
            } catch (err) {
                summary.textContent = `AGY activity unavailable: ${err.message}`;
                runsEl.innerHTML = '';
            }
        }

        // Fetch API States
        async function fetchData() {
            // Global telemetry poll
            await fetchTelemetryData();
            
            if (activeTab === 'dashboard') {
                renderDashboardSummary();
                await fetchQuotaSummary();
                await fetchCompletedWorkGate();
                await fetchPromotionDecisionLedger();
                await fetchOperatorActionApproval();
                await fetchApprovedActionExecutor();
                await fetchFinalActionAuthorization();
                await fetchQuarantinedExecutionAdapter();
                await fetchSandboxedExecutionCanary();
                await fetchRealExecutorArmingGate();
                await fetchRawAgentOutputQueue();
                await fetchMergeBacklog();
                await fetchOvernightGuard();
                await fetchMorningBriefing();
            } else if (activeTab === 'merge') {
                await fetchMergeData();
            } else if (activeTab === 'foundation') {
                await fetchFoundationData();
            } else if (activeTab === 'workspaces') {
                await renderWorkspacesView();
            } else if (activeTab === 'skills') {
                await renderSkillsView();
            } else if (activeTab === 'signals') {
                await renderSignalsView();
            } else if (activeTab === 'quota') {
                await fetchBudgetCaps();
                await fetchJulesCapacity();
                await fetchQuotaData();
            }
        }

        async function fetchTelemetryData() {
            try {
                // 1. Stats
                const statsRes = await fetch(`${API_PREFIX}/webhooks/stats`);
                if (statsRes.ok) {
                    const stats = await statsRes.json();
                    document.getElementById("stat-received").textContent = stats.received || 0;
                    document.getElementById("stat-auth-failed").textContent = stats.auth_failed || 0;
                    document.getElementById("stat-latency").textContent = stats.average_dispatch_latency_seconds ? stats.average_dispatch_latency_seconds.toFixed(1) : "0.0";
                    
                    const sparklineDiv = document.getElementById("latency-sparkline");
                    if (stats.recent_latencies && stats.recent_latencies.length >= 2) {
                        sparklineDiv.innerHTML = drawSparkline(stats.recent_latencies);
                    } else {
                        sparklineDiv.innerHTML = `<span class="text-[10px] text-slate-500 italic">No trend</span>`;
                    }
                    
                    document.getElementById("stat-pending").textContent = stats.queue_depths?.pending || 0;
                    document.getElementById("stat-processing").textContent = stats.queue_depths?.processing || 0;
                    document.getElementById("stat-completed").textContent = stats.queue_depths?.completed || 0;
                    document.getElementById("stat-failed").textContent = stats.queue_depths?.failed || 0;
                }

                // 1b. Failure taxonomy
                const taxonomyRes = await fetch(`${API_PREFIX}/recovery/status`);
                if (taxonomyRes.ok) {
                    const payload = await taxonomyRes.json();
                    const grid = document.getElementById("failure-taxonomy-grid");
                    const taxonomy = payload.failure_taxonomy || [];
                    grid.innerHTML = taxonomy.map(item => `
                        <article class="glass-panel p-3 rounded-xl border border-slate-800/70">
                            <div class="flex items-center justify-between gap-3">
                                <div>
                                    <div class="text-[10px] uppercase tracking-wider text-slate-500">${item.code}</div>
                                    <h3 class="text-sm font-semibold text-slate-100 mt-1">${item.name}</h3>
                                </div>
                                <span class="px-2 py-0.5 rounded text-[10px] font-bold border border-cyan-500/20 bg-cyan-500/10 text-cyan-400">${item.layer}</span>
                            </div>
                            <p class="text-xs text-slate-400 mt-2">${item.example}</p>
                            <div class="mt-3 flex flex-wrap gap-1">
                                ${item.signals.map(sig => `<span class="px-2 py-0.5 rounded-full text-[10px] bg-slate-800 text-slate-300 border border-slate-700">${sig}</span>`).join("")}
                            </div>
                        </article>
                    `).join("");

                    renderRecoverySurface(payload);

                    const pool = payload.pool_stats || {};
                    const heartbeat = payload.heartbeat || {};
                    const poolAvailable = pool.available !== false;
                    document.getElementById("stat-pool-live").textContent = poolAvailable ? (pool.live_count ?? "—") : "—";
                    document.getElementById("stat-pool-dlq").textContent = poolAvailable ? (pool.total_skipped_dlq ?? "—") : "—";
                    const recoveryHealthy = Boolean(payload.systemd_active) && heartbeat.available === true && heartbeat.fresh === true && poolAvailable && (pool.live_count ?? 0) === 0;
                    const runtimeLabel = payload.systemd_available === false ? "systemd unavailable" : (payload.systemd_active ? "service active" : "service offline");
                    const heartbeatLabel = heartbeat.available === false ? "heartbeat unavailable" : `heartbeat ${heartbeat.status || "unknown"}`;
                    const poolLabel = poolAvailable ? `${pool.live_count ?? "—"} live / ${pool.total_skipped_dlq ?? "—"} dlq` : "pool snapshot unavailable";
                    setSummaryBadge(
                        "summary-recovery-badge",
                        "summary-recovery-meta",
                        recoveryHealthy ? "green" : "amber",
                        recoveryHealthy ? "HEALTHY" : "CHECK",
                        `${payload.service_name || 'prismatic-consumer.service'} · ${runtimeLabel} · ${heartbeatLabel} · ${poolLabel}`
                    );
                }

                // 2. Queue
                const queueStatusRes = await fetch(`${API_PREFIX}/webhooks/queue/status`);
                if (queueStatusRes.ok) {
                    const queueStatus = await queueStatusRes.json();
                    const summary = document.getElementById("queue-contract-summary");
                    if (summary) {
                        summary.textContent = `source = ${queueStatus.source || 'linear_webhook_queue.db'} · marker = ${queueStatus.marker || '—'} · latest event: ${queueStatus.latest_event_identifier || '—'} / ${queueStatus.latest_event_status || '—'} · last drain: ${queueStatus.last_drain_result || 'never_run'}`;
                    }
                }
                const queueRes = await fetch(`${API_PREFIX}/webhooks/queue`);
                if (queueRes.ok) {
                    const queue = await queueRes.json();
                    loadedQueueItems = queue.items || [];
                    document.getElementById("queue-total-badge").textContent = `${queue.total} total`;
                    filterQueue();
                }

                // 3. Dispatcher Status
                const dispRes = await fetch(`${API_PREFIX}/dispatcher/status`);
                if (dispRes.ok) {
                    const disp = await dispRes.json();
                    const badge = document.getElementById("disp-status-badge");
                    const dot = document.getElementById("disp-status-dot");
                    const text = document.getElementById("disp-status-text");

                    const linearLimit = disp.linear_rate_limit || {};
                    const pollingBudget = disp.polling_budget || {};
                    if (linearLimit.cooldown_active) {
                        badge.className = "flex items-center space-x-1.5 bg-amber-500/10 px-2.5 py-1 rounded-full text-xs font-bold text-amber-400 border border-amber-500/20";
                        dot.className = "w-2 h-2 rounded-full bg-amber-500";
                        text.textContent = "LINEAR COOLDOWN";
                        setSummaryBadge("summary-dispatcher-badge", "summary-dispatcher-meta", "amber", "LINEAR COOLDOWN", `${linearLimit.reason || 'Rate-limit circuit open'} · reset ${linearLimit.cooldown_until || linearLimit.reset_at || 'unknown'}`);
                    } else if (disp.status === "active") {
                        badge.className = "flex items-center space-x-1.5 bg-emerald-500/10 px-2.5 py-1 rounded-full text-xs font-bold text-emerald-400 border border-emerald-500/20";
                        dot.className = "w-2 h-2 rounded-full bg-emerald-500 status-pulse";
                        text.textContent = "ACTIVE";
                        setSummaryBadge("summary-dispatcher-badge", "summary-dispatcher-meta", "green", "ACTIVE", `Cycle #${disp.cycle_number || 0} · ${disp.active_agents?.length || 0} agent(s) live · Linear ${pollingBudget.last_cycle_calls ?? pollingBudget.calls_used_this_cycle ?? 0}/${pollingBudget.max_calls_per_cycle ?? '—'} · cache ${pollingBudget.cache_hits ?? 0}/${pollingBudget.cache_misses ?? 0}`);
                    } else {
                        badge.className = "flex items-center space-x-1.5 bg-slate-800 px-2.5 py-1 rounded-full text-xs font-bold text-slate-400 border border-slate-700/30";
                        dot.className = "w-2 h-2 rounded-full bg-slate-500";
                        text.textContent = "IDLE";
                        setSummaryBadge("summary-dispatcher-badge", "summary-dispatcher-meta", "slate", "IDLE", `Cycle #${disp.cycle_number || 0} · last seen ${disp.last_cycle_at ? formatDate(disp.last_cycle_at) : "Never"} · Linear ${pollingBudget.last_cycle_calls ?? pollingBudget.calls_used_this_cycle ?? 0}/${pollingBudget.max_calls_per_cycle ?? '—'} · ${pollingBudget.last_skip_reason || 'poll fallback bounded'}`);
                    }

                    document.getElementById("disp-cycle").textContent = disp.cycle_number || 0;
                    document.getElementById("disp-last-active").textContent = disp.last_cycle_at ? formatDate(disp.last_cycle_at) : "Never";

                    const stallAlert = document.getElementById("disp-stall-alert");
                    if (disp.silent_stall && disp.silent_stall.triggered) {
                        stallAlert.textContent = disp.silent_stall.message || "Dispatcher stall alert";
                        stallAlert.classList.remove("hidden");
                    } else {
                        stallAlert.classList.add("hidden");
                    }

                    const agentsDiv = document.getElementById("disp-agents");
                    if (!disp.active_agents || disp.active_agents.length === 0) {
                        agentsDiv.innerHTML = `<span class="text-xs text-slate-600 italic">None active</span>`;
                    } else {
                        agentsDiv.innerHTML = disp.active_agents.map(a => getAgentBadge(a)).join("");
                    }
                }
            } catch (e) {
                console.error("Error fetching telemetry:", e);
            }
        }

        async function renderDashboardSummary() {
            // Live agent status cache for topology node drilldowns.
            try {
                const agentRes = await fetch("/api/gateway/agents/status");
                if (agentRes.ok) {
                    agentStatusCache = await agentRes.json();
                }
            } catch (err) {
                console.error("Error loading live agent status:", err);
                agentStatusCache = { agents: [], status_counts: {}, evidence: {}, source: "agent-status-error" };
            }
            await loadAgentGovernanceStatus();

            // Activity Feed — live operational timeline from EventBus/run/recovery evidence.
            const actFeed = document.getElementById("dashboard-activity");
            try {
                const timelineRes = await fetch("/api/gateway/timeline?limit=12");
                let items = [];
                if (timelineRes.ok) {
                    const payload = await timelineRes.json();
                    items = payload.items || payload.timeline || [];
                }
                if (!items.length) {
                    const eventsRes = await fetch("/events/recent?limit=12");
                    if (eventsRes.ok) {
                        const payload = await eventsRes.json();
                        items = (payload.events || []).map(event => ({
                            timestamp: event.ts,
                            source: event.topic || "EventBus",
                            title: event.payload?.title || event.payload?.identifier || event.topic || "event",
                            severity: event.processed ? "info" : "warning",
                        }));
                    }
                }
                if (!items.length) {
                    actFeed.innerHTML = `<div class="text-slate-500 italic">No live operational timeline events recorded. No synthetic fallback rendered.</div>`;
                } else {
                    actFeed.innerHTML = items.slice(0, 12).map(item => `
                        <div class="activity-item">
                            <div class="activity-text-row">
                                <span class="time">${escapeHtml(item.timestamp ? formatDate(item.timestamp) : '—')}</span>
                                <span class="source-badge">${escapeHtml(item.source || item.kind || 'Timeline')}</span>
                                <span class="title text-slate-200">${escapeHtml(item.title || item.message || 'Timeline event')}</span>
                            </div>
                            <div class="text-[10px] text-slate-500 uppercase font-bold mt-1">${escapeHtml(item.severity || 'info')}</div>
                        </div>
                    `).join("");
                }
            } catch (err) {
                console.error("Error loading operational timeline:", err);
                actFeed.innerHTML = `<div class="text-rose-400 italic">Timeline API unavailable: ${escapeHtml(err.message || err)}</div>`;
            }

            // Workspaces List Summary
            await renderDashboardWorkspacesSummary();
        }

        function setSummaryBadge(badgeId, metaId, tone, badgeText, metaText) {
            const palette = {
                green: {
                    badge: "px-2 py-0.5 rounded-full text-[10px] font-bold border border-emerald-500/20 text-emerald-400 bg-emerald-500/10",
                    meta: "mt-2 text-sm text-emerald-100 font-semibold",
                },
                amber: {
                    badge: "px-2 py-0.5 rounded-full text-[10px] font-bold border border-amber-500/20 text-amber-400 bg-amber-500/10",
                    meta: "mt-2 text-sm text-amber-100 font-semibold",
                },
                red: {
                    badge: "px-2 py-0.5 rounded-full text-[10px] font-bold border border-rose-500/20 text-rose-400 bg-rose-500/10",
                    meta: "mt-2 text-sm text-rose-100 font-semibold",
                },
                cyan: {
                    badge: "px-2 py-0.5 rounded-full text-[10px] font-bold border border-cyan-500/20 text-cyan-400 bg-cyan-500/10",
                    meta: "mt-2 text-sm text-cyan-100 font-semibold",
                },
                slate: {
                    badge: "px-2 py-0.5 rounded-full text-[10px] font-bold border border-slate-700 text-slate-400 bg-slate-800/80",
                    meta: "mt-2 text-sm text-slate-200 font-semibold",
                },
            };
            const badge = document.getElementById(badgeId);
            const meta = document.getElementById(metaId);
            const style = palette[tone] || palette.slate;
            if (badge) {
                badge.className = style.badge;
                badge.textContent = badgeText;
            }
            if (meta) {
                meta.className = style.meta;
                meta.textContent = metaText;
            }
        }

        async function fetchMergeData() {
            const statusBadge = document.getElementById("merge-snapshot-status");
            const statusMeta = document.getElementById("merge-snapshot-meta");
            const pendingTbody = document.getElementById("merge-pending-tbody");
            const historyTbody = document.getElementById("merge-history-tbody");
            try {
                const mergeRes = await fetch("/api/gateway/merge/status");
                if (!mergeRes.ok) throw new Error(`HTTP ${mergeRes.status}`);
                const data = await mergeRes.json();
                const pending = Array.isArray(data.pending) ? data.pending : [];
                const merged = Array.isArray(data.merged) ? data.merged : [];
                const driftLabel = data.drift_detected === true ? " · drift detected" : "";
                if (statusBadge) statusBadge.textContent = `Historical snapshot${driftLabel}`;
                if (statusMeta) {
                    const source = escapeHTML(data.source || "legacy merge state");
                    const snapshotScan = data.last_scan ? formatDate(data.last_scan) : "unknown scan time";
                    statusMeta.innerHTML = `Source: ${source} · Snapshot scan: ${escapeHTML(snapshotScan)} · Not Review Factory authority`;
                }

                // Staleness marker: must be cleared in the success path and set in the
                // failure path so the four Historical cards cannot remain presented as
                // current snapshot metadata after a later fetch fails.
                const pendingStat = document.getElementById("stat-merge-pending");
                const mergedStat = document.getElementById("stat-merge-merged");
                const scanStat = document.getElementById("stat-merge-scan");
                const applyStat = document.getElementById("stat-merge-apply");
                for (const node of [pendingStat, mergedStat, scanStat, applyStat]) {
                    if (node) {
                        node.removeAttribute("data-stale");
                        node.removeAttribute("title");
                    }
                }

                    document.getElementById("stat-merge-pending").textContent = data.pending_count ?? pending.length;
                    document.getElementById("stat-merge-merged").textContent = data.merged_count ?? merged.length;
                    document.getElementById("stat-merge-scan").textContent = data.last_scan ? formatDate(data.last_scan) : "Never";
                    document.getElementById("stat-merge-apply").textContent = data.last_apply ? formatDate(data.last_apply) : "Never";
                    
                    // Render historical sandbox rows. Every legacy value is escaped.
                    if (pending.length === 0) {
                        pendingTbody.innerHTML = `<tr><td colspan="5" class="py-6 text-center text-slate-500 italic">No historical pending sandboxes.</td></tr>`;
                    } else {
                        pendingTbody.innerHTML = pending.map(sb => {
                            const contention = Array.isArray(sb.contention) ? sb.contention.map(escapeHTML) : [];
                            const contentionText = contention.join(", ") || "None";
                            const tier = sb.tier === "" || sb.tier == null ? "—" : `T${escapeHTML(sb.tier)}`;
                            return `
                            <tr class="border-b border-slate-800/50 hover:bg-slate-900/10">
                                <td class="py-3 px-3 font-semibold text-slate-200 font-mono">${escapeHTML(sb.ticket)}</td>
                                <td class="py-3 px-3"><span class="px-2 py-0.5 rounded text-[10px] font-bold border border-slate-500/20 bg-slate-500/10 text-slate-300">${tier}</span></td>
                                <td class="py-3 px-3 font-bold text-amber-400 font-mono">${escapeHTML(sb.confidence)}%</td>
                                <td class="py-3 px-3 text-slate-400">${escapeHTML(sb.modified)} files</td>
                                <td class="py-3 px-3 text-slate-500 font-mono text-[11px] max-w-[200px] truncate" title="${contentionText}">${contentionText}</td>
                            </tr>
                        `}).join("");
                    }
                    
                    // Render historical merge records. Provenance does not establish automation.
                    if (merged.length === 0) {
                        historyTbody.innerHTML = `<tr><td colspan="4" class="py-6 text-center text-slate-500 italic">No merge history.</td></tr>`;
                    } else {
                        historyTbody.innerHTML = merged.map(m => {
                            const commit = String(m.commit || "");
                            const tier = m.tier === "" || m.tier == null ? "—" : `T${escapeHTML(m.tier)}`;
                            return `
                            <tr class="border-b border-slate-800/50 hover:bg-slate-900/10">
                                <td class="py-3 px-3 font-semibold text-slate-200 font-mono">${escapeHTML(m.ticket)}</td>
                                <td class="py-3 px-3"><span class="px-2 py-0.5 rounded text-[10px] font-bold border border-slate-500/20 bg-slate-500/10 text-slate-300">${tier}</span></td>
                                <td class="py-3 px-3 text-emerald-400 font-mono text-xs">${escapeHTML(commit.substring(0, 7) || "—")}</td>
                                <td class="py-3 px-3 text-slate-500 text-xs">${escapeHTML(m.timestamp ? formatDate(m.timestamp) : "Unknown")}</td>
                            </tr>
                        `}).join("");
                    }
            } catch (e) {
                console.error("Error fetching merge details:", e);
                if (statusBadge) statusBadge.textContent = "Historical snapshot unavailable";
                if (statusMeta) statusMeta.textContent = "Legacy data unavailable. Review Factory remains the only canonical workflow.";
                if (pendingTbody) pendingTbody.innerHTML = `<tr><td colspan="5" class="py-6 text-center text-rose-300">Historical snapshot unavailable.</td></tr>`;
                if (historyTbody) historyTbody.innerHTML = `<tr><td colspan="4" class="py-6 text-center text-rose-300">Historical snapshot unavailable.</td></tr>`;

                // The four Historical summary cards must not continue to display
                // their prior poll's numbers/timestamps as if they were current.
                // Clear the values to an explicit dash and flag them stale.
                const statNodes = [
                    ["stat-merge-pending", "Pending merges (unavailable)"],
                    ["stat-merge-merged", "Merged count (unavailable)"],
                    ["stat-merge-scan", "Snapshot scan (unavailable)"],
                    ["stat-merge-apply", "Historical apply (unavailable)"],
                ];
                for (const [id, tooltip] of statNodes) {
                    const node = document.getElementById(id);
                    if (!node) continue;
                    node.textContent = "—";
                    node.setAttribute("data-stale", "true");
                    node.setAttribute("title", tooltip);
                }
            }
        }

        function escapeHTML(value) {
            return String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
        }

        let workspaceTreeState = {
            workspaces: [],
            selectedWorkspaceId: null,
            selectedRelativePath: null,
            initialized: false,
            rendering: false,
            sortBy: localStorage.getItem("ws_sort_by") || "name",
            sortOrder: localStorage.getItem("ws_sort_order") || "asc",
            expandedPaths: new Set(JSON.parse(localStorage.getItem("ws_expanded_paths") || "[]")),
            sidebarWidth: parseInt(localStorage.getItem("ws_sidebar_width") || "380", 10),
        };

        function applySidebarWidth(w) {
            const sidebar = document.getElementById("workspace-tree-sidebar");
            if (!sidebar) return;
            const clamped = Math.max(200, Math.min(800, w));
            sidebar.style.width = `${clamped}px`;
            sidebar.style.minWidth = `${clamped}px`;
            sidebar.style.maxWidth = `${clamped}px`;
            sidebar.style.flex = `0 0 ${clamped}px`;
        }

        function initWorkspaceSplitter() {
            const handle = document.getElementById("workspace-splitter-handle");
            const sidebar = document.getElementById("workspace-tree-sidebar");
            const container = document.getElementById("workspace-splitter-container");
            if (!handle || !sidebar || !container) return;
            
            // Restore saved width
            if (workspaceTreeState.sidebarWidth && window.innerWidth >= 1024) {
                applySidebarWidth(workspaceTreeState.sidebarWidth);
            }

            let dragging = false;

            const onPointerMove = (e) => {
                if (!dragging) return;
                const containerRect = container.getBoundingClientRect();
                const newWidth = Math.max(200, Math.min(containerRect.width - 250, e.clientX - containerRect.left));
                applySidebarWidth(newWidth);
                workspaceTreeState.sidebarWidth = Math.round(newWidth);
            };

            const onPointerUp = (e) => {
                if (!dragging) return;
                dragging = false;
                document.body.style.userSelect = "";
                document.body.style.cursor = "";
                if (handle.hasPointerCapture(e.pointerId)) {
                    handle.releasePointerCapture(e.pointerId);
                }
                localStorage.setItem("ws_sidebar_width", workspaceTreeState.sidebarWidth);
            };

            handle.addEventListener("pointerdown", (e) => {
                dragging = true;
                document.body.style.userSelect = "none";
                document.body.style.cursor = "col-resize";
                handle.setPointerCapture(e.pointerId);
            });

            handle.addEventListener("pointermove", onPointerMove);
            handle.addEventListener("pointerup", onPointerUp);
            handle.addEventListener("pointercancel", onPointerUp);
        }

        async function saveWorkspaceTreeState() {
            const payload = {
                expanded_paths: Array.from(workspaceTreeState.expandedPaths),
                selected_workspace_id: workspaceTreeState.selectedWorkspaceId,
                selected_relative_path: workspaceTreeState.selectedRelativePath,
                sidebar_width: workspaceTreeState.sidebarWidth,
                sort_by: workspaceTreeState.sortBy,
                sort_order: workspaceTreeState.sortOrder
            };
            try {
                localStorage.setItem("ws_sort_by", workspaceTreeState.sortBy);
                localStorage.setItem("ws_sort_order", workspaceTreeState.sortOrder);
                localStorage.setItem("ws_expanded_paths", JSON.stringify(payload.expanded_paths));
                if (workspaceTreeState.selectedWorkspaceId) localStorage.setItem("ws_selected_workspace_id", workspaceTreeState.selectedWorkspaceId);
                if (workspaceTreeState.selectedRelativePath) localStorage.setItem("ws_selected_relative_path", workspaceTreeState.selectedRelativePath);
                
                await fetch("/api/workspace-tree/state", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
            } catch (e) {}
        }

        async function fetchServerWorkspaceTreeState() {
            try {
                const res = await fetch("/api/workspace-tree/state");
                if (res.ok) {
                    const data = await res.json();
                    if (data.ok && data.state && typeof data.state === "object") {
                        const s = data.state;
                        if (Array.isArray(s.expanded_paths)) {
                            s.expanded_paths.forEach(p => workspaceTreeState.expandedPaths.add(p));
                        }
                        if (s.selected_workspace_id) workspaceTreeState.selectedWorkspaceId = s.selected_workspace_id;
                        if (s.selected_relative_path) workspaceTreeState.selectedRelativePath = s.selected_relative_path;
                        if (s.sidebar_width) workspaceTreeState.sidebarWidth = s.sidebar_width;
                        if (s.sort_by) workspaceTreeState.sortBy = s.sort_by;
                        if (s.sort_order) workspaceTreeState.sortOrder = s.sort_order;
                    }
                }
            } catch (e) {}
        }

        async function rehydrateExpandedPaths() {
            let expandedAny = true;
            let iterations = 0;
            while (expandedAny && iterations < 15) {
                expandedAny = false;
                iterations++;
                const pathsToExpand = Array.from(workspaceTreeState.expandedPaths);
                
                // Sort paths by depth (shallowest first)
                pathsToExpand.sort((a, b) => a.split("/").length - b.split("/").length);

                for (const key of pathsToExpand) {
                    const parts = key.split(":");
                    const workspaceId = parts[0];
                    const relPath = parts.slice(1).join(":");
                    const nodeEl = document.querySelector(`.workspace-node[data-workspace-id="${CSS.escape(workspaceId)}"][data-relative-path="${CSS.escape(relPath)}"]`);
                    if (nodeEl) {
                        const button = nodeEl.querySelector(":scope > button[data-workspace-action='expand']");
                        const childrenEl = nodeEl.querySelector(":scope > .workspace-node-children");
                        if (button && childrenEl && (childrenEl.classList.contains("hidden") || childrenEl.dataset.loaded !== "true")) {
                            await toggleWorkspaceDirectory(button);
                            expandedAny = true;
                        }
                    }
                }
            }
        }

        function formatYYMMDD(isoString) {
            if (!isoString) return "";
            try {
                const d = new Date(isoString);
                if (isNaN(d.getTime())) return "";
                const yy = String(d.getFullYear()).slice(-2);
                const mm = String(d.getMonth() + 1).padStart(2, "0");
                const dd = String(d.getDate()).padStart(2, "0");
                const hh = String(d.getHours()).padStart(2, "0");
                const min = String(d.getMinutes()).padStart(2, "0");
                return `${yy}-${mm}-${dd} ${hh}:${min}`;
            } catch (e) {
                return "";
            }
        }

        function formatFullDateTime(isoString) {
            if (!isoString) return "—";
            try {
                const d = new Date(isoString);
                if (isNaN(d.getTime())) return "—";
                return d.toUTCString().replace("GMT", "UTC");
            } catch (e) {
                return "—";
            }
        }

        function toggleWorkspaceSortMenu() {
            const menu = document.getElementById("workspace-sort-menu");
            if (menu) menu.classList.toggle("hidden");
        }

        function updateWorkspaceSortUI() {
            const labelEl = document.getElementById("workspace-sort-label");
            const names = { name: "Name", date: "Date modified", type: "Type", size: "Size" };
            if (labelEl) labelEl.textContent = `Sort: ${names[workspaceTreeState.sortBy] || "Name"}`;
            
            ["name", "date", "type", "size"].forEach(s => {
                const el = document.getElementById(`sort-check-${s}`);
                if (el) el.className = s === workspaceTreeState.sortBy ? "text-slate-200 font-bold" : "hidden text-slate-200 font-bold";
            });
            ["asc", "desc"].forEach(o => {
                const el = document.getElementById(`order-check-${o}`);
                if (el) el.className = o === workspaceTreeState.sortOrder ? "text-slate-200 font-bold" : "hidden text-slate-200 font-bold";
            });
            
            const rootsEl = document.getElementById("workspace-tree-roots");
            if (rootsEl) rootsEl.setAttribute("data-sort-active", workspaceTreeState.sortBy);
        }

        function sortWorkspaceTreeInPlace(sortBy, sortOrder) {
            workspaceTreeState.sortBy = sortBy;
            workspaceTreeState.sortOrder = sortOrder;
            saveWorkspaceTreeState();
            updateWorkspaceSortUI();

            const containers = document.querySelectorAll("#workspace-tree-roots, .workspace-node-children");
            containers.forEach(container => {
                const nodes = Array.from(container.children).filter(el => el.classList && el.classList.contains("workspace-node"));
                if (!nodes.length) return;

                const isDesc = sortOrder === "desc";
                nodes.sort((a, b) => {
                    const isDirA = a.dataset.isDir === "true" ? 1 : 0;
                    const isDirB = b.dataset.isDir === "true" ? 1 : 0;

                    // Folders first
                    if (isDirA !== isDirB) return isDirB - isDirA;

                    let valA = a.dataset[sortBy] || "";
                    let valB = b.dataset[sortBy] || "";

                    if (sortBy === "size") {
                        valA = parseInt(valA || "0", 10);
                        valB = parseInt(valB || "0", 10);
                        return isDesc ? valB - valA : valA - valB;
                    }

                    const cmp = String(valA).localeCompare(String(valB), undefined, { numeric: true, sensitivity: "base" });
                    return isDesc ? -cmp : cmp;
                });

                nodes.forEach(node => container.appendChild(node));
            });
        }

        function setWorkspaceSort(sortBy) {
            const menu = document.getElementById("workspace-sort-menu");
            if (menu) menu.classList.add("hidden");
            sortWorkspaceTreeInPlace(sortBy, workspaceTreeState.sortOrder);
        }

        function setWorkspaceOrder(sortOrder) {
            const menu = document.getElementById("workspace-sort-menu");
            if (menu) menu.classList.add("hidden");
            sortWorkspaceTreeInPlace(workspaceTreeState.sortBy, sortOrder);
        }

        function filterWorkspaceTreeNodes() {
            const input = document.getElementById("workspace-tree-search");
            if (!input) return;
            const query = input.value.trim().toLowerCase();
            const allNodes = document.querySelectorAll(".workspace-node");

            if (!query) {
                allNodes.forEach(node => node.classList.remove("hidden"));
                return;
            }

            allNodes.forEach(node => {
                const name = (node.dataset.name || "").toLowerCase();
                const relPath = (node.dataset.relativePath || "").toLowerCase();
                const match = name.includes(query) || relPath.includes(query);
                if (match) {
                    node.classList.remove("hidden");
                    let parentNode = node.parentElement?.closest(".workspace-node");
                    while (parentNode) {
                        parentNode.classList.remove("hidden");
                        const childBox = parentNode.querySelector(":scope > .workspace-node-children");
                        if (childBox) childBox.classList.remove("hidden");
                        parentNode = parentNode.parentElement?.closest(".workspace-node");
                    }
                } else {
                    node.classList.add("hidden");
                }
            });
        }

        function workspaceNodeIcon(node, isExpanded = false) {
            if (node.type === "directory") {
                const chevron = isExpanded
                    ? `<span class="text-[10px] text-slate-400 font-mono flex-shrink-0 w-3">▾</span>`
                    : `<span class="text-[10px] text-slate-400 font-mono flex-shrink-0 w-3">▸</span>`;
                return `${chevron}${PRISMATIC_THEME.icons.folder(isExpanded)}`;
            }
            const ext = (node.name || "").split(".").pop().toLowerCase();
            return `<span class="w-3 flex-shrink-0"></span>${PRISMATIC_THEME.icons.file(ext)}`;
        }

        function renderWorkspaceNode(workspaceId, node, depth = 0) {
            const relativePath = node.relative_path || "";
            const label = node.name || relativePath || "workspace";
            const isDir = node.type === "directory";
            const previewable = node.previewable === true;
            const children = Array.isArray(node.children) ? node.children : [];
            const indent = Math.min(depth * 10, 50);

            const isSelected = workspaceTreeState.selectedWorkspaceId === workspaceId && workspaceTreeState.selectedRelativePath === relativePath;

            // Dual Light/Dark Mode compatible classes
            const buttonClass = isSelected
                ? "is-selected bg-slate-200 dark:bg-slate-800 text-slate-900 dark:text-slate-100 font-semibold border-l-2 border-slate-500 dark:border-slate-400 shadow-sm"
                : isDir
                    ? "text-slate-700 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-slate-800/40 font-normal"
                    : previewable
                        ? "text-slate-700 dark:text-slate-300 hover:text-slate-900 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-slate-800/40 font-normal"
                        : "text-slate-400 dark:text-slate-500 cursor-not-allowed opacity-50";

            const action = isDir ? "expand" : "preview";
            const disabled = !isDir && !previewable ? "disabled" : "";
            const childHtml = children.length
                ? `<div class="workspace-node-children ml-3 border-l border-slate-300 dark:border-slate-800/80 pl-2.5 space-y-0.5">${children.map(child => renderWorkspaceNode(workspaceId, child, depth + 1)).join("")}</div>`
                : `<div class="workspace-node-children ml-3 border-l border-slate-300 dark:border-slate-800/80 pl-2.5 space-y-0.5 hidden"></div>`;

            // Clean, borderless, bright slate date & size text (No box outlines)
            const shortDate = node.mtime ? formatYYMMDD(node.mtime) : "";
            const compactSize = node.size != null ? (node.size > 1048576 ? `${(node.size/1048576).toFixed(1)}MB` : node.size > 1024 ? `${(node.size/1024).toFixed(1)}KB` : `${node.size}B`) : "";

            let metaBadgeHtml = "";
            if (workspaceTreeState.sortBy === "date" && shortDate) {
                metaBadgeHtml = `<span class="text-[10px] font-mono text-slate-400 dark:text-slate-400 font-medium whitespace-nowrap flex-shrink-0 ml-2">${escapeHTML(shortDate)}</span>`;
            } else if (workspaceTreeState.sortBy === "size" && compactSize) {
                metaBadgeHtml = `<span class="text-[10px] font-mono text-slate-400 dark:text-slate-400 font-medium whitespace-nowrap flex-shrink-0 ml-2">${escapeHTML(compactSize)}</span>`;
            } else if (compactSize || shortDate) {
                const labelStr = compactSize || shortDate;
                metaBadgeHtml = `<span class="text-[10px] font-mono text-slate-400 dark:text-slate-400 font-medium whitespace-nowrap flex-shrink-0 ml-2">${escapeHTML(labelStr)}</span>`;
            }

            return `<div class="workspace-node my-0.5" 
                data-workspace-id="${escapeHTML(workspaceId)}" 
                data-relative-path="${escapeHTML(relativePath)}" 
                data-name="${escapeHTML(label)}"
                data-is-dir="${isDir}"
                data-date="${escapeHTML(node.mtime || "")}"
                data-size="${node.size || 0}"
                data-type="${isDir ? "directory" : "file"}"
                style="margin-left:${indent}px">
                <button type="button" data-workspace-action="${action}" class="w-full text-left px-2 py-1 rounded transition-colors ${buttonClass}" ${disabled}>
                    <span class="flex items-center gap-1.5 min-w-0 truncate">
                        ${workspaceNodeIcon(node, false)}
                        <span class="truncate ml-0.5">${escapeHTML(label)}</span>
                    </span>
                    ${metaBadgeHtml}
                </button>
                ${isDir ? childHtml : ""}
            </div>`;
        }

        async function toggleWorkspaceDirectory(button) {
            const wrapper = button.closest(".workspace-node");
            const workspaceId = wrapper?.dataset.workspaceId || "";
            const relativePath = wrapper?.dataset.relativePath || "";
            const childrenEl = wrapper?.querySelector(":scope > .workspace-node-children");
            if (!childrenEl) return;
            const stateKey = `${workspaceId}:${relativePath}`;
            if (childrenEl.dataset.loaded === "true") {
                childrenEl.classList.toggle("hidden");
                const isHidden = childrenEl.classList.contains("hidden");
                
                const iconBox = button.querySelector("span");
                if (iconBox) {
                    iconBox.outerHTML = `<span class="flex items-center gap-1.5 min-w-0 truncate">${workspaceNodeIcon({ type: "directory" }, !isHidden)}<span class="truncate ml-0.5">${escapeHTML(wrapper.dataset.name || "")}</span></span>`;
                }

                if (isHidden) {
                    button.classList.remove("bg-slate-200", "dark:bg-slate-800/60", "text-slate-900", "dark:text-slate-100", "font-semibold");
                } else {
                    button.classList.add("bg-slate-100", "dark:bg-slate-800/60", "text-slate-900", "dark:text-slate-100", "font-semibold");
                }
                if (isHidden) workspaceTreeState.expandedPaths.delete(stateKey);
                else workspaceTreeState.expandedPaths.add(stateKey);
                void saveWorkspaceTreeState();
                return;
            }
            workspaceTreeState.expandedPaths.add(stateKey);
            void saveWorkspaceTreeState();
            childrenEl.classList.remove("hidden");
            childrenEl.innerHTML = `<div class="px-2 py-1 text-slate-500 italic">Loading…</div>`;
            try {
                const query = new URLSearchParams({
                    workspace_id: workspaceId,
                    path: relativePath,
                    depth: "1",
                    sort_by: workspaceTreeState.sortBy,
                    sort_order: workspaceTreeState.sortOrder,
                });
                const res = await fetch(`/api/workspace-tree/node?${query.toString()}`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const node = data.tree || {};
                const children = Array.isArray(node.children) ? node.children : [];
                childrenEl.dataset.loaded = "true";
                childrenEl.innerHTML = children.length
                    ? children.map(child => renderWorkspaceNode(workspaceId, child, 0)).join("")
                    : `<div class="px-2 py-1 text-slate-500 italic">Empty folder.</div>`;
                
                const iconBox = button.querySelector("span");
                if (iconBox) {
                    iconBox.outerHTML = `<span class="flex items-center gap-1.5 min-w-0 truncate">${workspaceNodeIcon({ type: "directory" }, true)}<span class="truncate ml-0.5">${escapeHTML(wrapper.dataset.name || "")}</span></span>`;
                }
                button.classList.add("bg-slate-100", "dark:bg-slate-800/60", "text-slate-900", "dark:text-slate-100", "font-semibold");
            } catch (err) {
                childrenEl.innerHTML = `<div class="px-2 py-1 text-rose-400">Could not load folder: ${escapeHTML(err.message)}</div>`;
            }
        }

        async function previewWorkspaceFile(workspaceId, relativePath) {
            const nameEl = document.getElementById("workspace-preview-name");
            const previewEl = document.getElementById("workspace-file-preview");
            const legacyLink = document.getElementById("workspace-legacy-link");
            const statsBar = document.getElementById("workspace-preview-stats-bar");
            const mtimeEl = document.getElementById("stat-file-mtime");
            const sizeEl = document.getElementById("stat-file-size");
            const linesEl = document.getElementById("stat-file-lines");
            const scopeEl = document.getElementById("workspace-preview-scope");
            const badgeEl = document.getElementById("workspace-preview-type-badge");

            if (!previewEl || !nameEl) return;
            workspaceTreeState.selectedWorkspaceId = workspaceId;
            workspaceTreeState.selectedRelativePath = relativePath;
            void saveWorkspaceTreeState();

            // Clear previous selection highlights in tree sidebar
            document.querySelectorAll("#workspace-tree-roots button.is-selected").forEach(btn => {
                btn.classList.remove("is-selected", "bg-slate-200", "dark:bg-slate-800", "text-slate-900", "dark:text-slate-100", "font-semibold", "border-l-2", "border-slate-500", "dark:border-slate-400", "shadow-sm");
                btn.classList.add("text-slate-700", "dark:text-slate-300", "font-normal");
            });

            // Highlight selected node button
            const selectedBtn = document.querySelector(`.workspace-node[data-workspace-id="${CSS.escape(workspaceId)}"][data-relative-path="${CSS.escape(relativePath)}"] > button`);
            if (selectedBtn) {
                selectedBtn.classList.remove("text-slate-700", "dark:text-slate-300", "font-normal");
                selectedBtn.classList.add("is-selected", "bg-slate-200", "dark:bg-slate-800", "text-slate-900", "dark:text-slate-100", "font-semibold", "border-l-2", "border-slate-500", "dark:border-slate-400", "shadow-sm");
            }

            nameEl.textContent = relativePath.includes ? (relativePath.split("/").pop() || relativePath) : relativePath;
            if (scopeEl) scopeEl.textContent = relativePath;
            previewEl.textContent = "Loading file preview…";

            const legacyQuery = new URLSearchParams({ workspace_id: workspaceId, path: relativePath });
            if (legacyLink) legacyLink.href = `/workspace-tree?${legacyQuery.toString()}`;
            const url = new URL(window.location.href);
            url.searchParams.delete("file");
            url.searchParams.set("workspace_id", workspaceId);
            url.searchParams.set("path", relativePath);
            url.hash = "workspaces";
            window.history.replaceState({}, "", url.toString());

            try {
                const query = new URLSearchParams({ workspace_id: workspaceId, path: relativePath });
                const res = await fetch(`/api/workspace-tree/preview?${query.toString()}`);
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

                previewEl.textContent = data.content || "";
                nameEl.textContent = data.name || relativePath;
                if (scopeEl) scopeEl.textContent = `${data.workspace_name || "Workspace"} / ${data.relative_path || relativePath}`;

                const ext = (data.name || "").split(".").pop().toUpperCase() || "FILE";
                if (badgeEl) badgeEl.textContent = `${ext} DOCUMENT`;

                // Display full date/time/size stats bar at the top of detail view
                if (statsBar) statsBar.classList.remove("hidden");
                if (mtimeEl) mtimeEl.textContent = `Modified: ${formatFullDateTime(data.mtime || new Date().isoString)}`;
                
                const exactBytes = Number(data.size || 0);
                const kbStr = exactBytes > 1048576 ? `${(exactBytes/1048576).toFixed(1)} MB` : `${(exactBytes/1024).toFixed(1)} KB`;
                if (sizeEl) sizeEl.textContent = `Size: ${exactBytes.toLocaleString()} bytes (${kbStr})`;
                if (linesEl) linesEl.textContent = `Lines: ${(data.lines || 1).toLocaleString()}`;

            } catch (err) {
                previewEl.textContent = `Preview unavailable: ${err.message}`;
                if (statsBar) statsBar.classList.add("hidden");
            }
        }

        function copyWorkspaceDeepLink() {
            const workspaceId = workspaceTreeState.selectedWorkspaceId;
            const relativePath = workspaceTreeState.selectedRelativePath;
            const url = new URL(window.location.origin + "/dashboard");
            if (workspaceId) url.searchParams.set("workspace_id", workspaceId);
            if (relativePath) url.searchParams.set("path", relativePath);
            url.hash = "workspaces";
            navigator.clipboard?.writeText(url.toString());
        }

        function downloadWorkspaceFile() {
            const workspaceId = workspaceTreeState.selectedWorkspaceId;
            const relativePath = workspaceTreeState.selectedRelativePath;
            if (!workspaceId || !relativePath) return;
            const query = new URLSearchParams({ workspace_id: workspaceId, path: relativePath, download: "true" });
            window.open(`/api/workspace-tree/preview?${query.toString()}`, "_blank");
        }

        async function openWorkspaceFile(filePath, event) {
            if (event) {
                if (event.ctrlKey || event.metaKey || event.button === 1) return;
                event.preventDefault();
            }
            if (!filePath) return;

            const cleanPath = String(filePath).replace(/^file:\/\//, "").replace(/^[a-zA-Z]:[/\\]/, "").replace(/\\/g, "/");
            
            // Switch to workspaces tab
            switchTab('workspaces');

            try {
                const query = new URLSearchParams({ file: cleanPath });
                const resolveResponse = await fetch(`/api/workspace-tree/resolve?${query.toString()}`);
                const resolved = await resolveResponse.json();
                if (!resolveResponse.ok) {
                    showToast(`File unavailable in workspaces: ${resolved.detail || 'Not found'}`);
                    return;
                }
                await previewWorkspaceFile(resolved.workspace_id, resolved.relative_path);
            } catch (err) {
                console.error("Failed to open workspace file:", err);
                showToast(`Failed to open workspace file: ${err.message}`);
            }
        }

        async function renderDashboardWorkspacesSummary() {
            const wsSummary = document.getElementById("dashboard-workspaces");
            if (!wsSummary) return;
            try {
                const res = await fetch("/api/workspaces");
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const workspaces = (data.workspaces || []).slice(0, 6);
                wsSummary.innerHTML = workspaces.map(ws => {
                    return `<tr class="border-b border-slate-800/40 hover:bg-slate-900/10 transition">
                        <td class="py-2.5 px-3 font-semibold text-slate-200">${escapeHTML(ws.name)}</td>
                        <td class="py-2.5 px-3 text-slate-400 font-mono">${escapeHTML(ws.workspace_id)}</td>
                        <td class="py-2.5 px-3 text-[10px] uppercase font-bold text-emerald-400">registered</td>
                        <td class="py-2.5 px-3 text-slate-500">Lazy tree</td>
                    </tr>`;
                }).join("") || `<tr><td colspan="4" class="py-4 text-center text-slate-500 italic">No workspace roots returned.</td></tr>`;
            } catch (err) {
                wsSummary.innerHTML = `<tr><td colspan="4" class="py-4 text-center text-rose-400">Workspace summary unavailable: ${escapeHTML(err.message)}</td></tr>`;
            }
        }

        async function renderWorkspacesView() {
            const rootsEl = document.getElementById("workspace-tree-roots");
            const statusEl = document.getElementById("workspace-tree-status");
            if (!rootsEl) return;
            initWorkspaceSplitter();
            if (workspaceTreeState.initialized || workspaceTreeState.rendering) return;
            workspaceTreeState.rendering = true;

            // Fetch cross-device persistent state from server
            await fetchServerWorkspaceTreeState();
            updateWorkspaceSortUI();

            rootsEl.innerHTML = `<div class="text-slate-500 italic">Loading workspace tree…</div>`;
            try {
                const res = await fetch("/api/workspaces");
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                workspaceTreeState.workspaces = data.workspaces || [];
                if (statusEl) statusEl.textContent = `${data.workspace_count || workspaceTreeState.workspaces.length} workspaces · sorted by ${workspaceTreeState.sortBy} (${workspaceTreeState.sortOrder})`;
                rootsEl.innerHTML = workspaceTreeState.workspaces.map(ws => {
                    const rootNode = { name: ws.name, type: "directory", relative_path: "", previewable: false, children: [] };
                    return `<div class="rounded-lg border border-slate-800/60 bg-slate-900/30 p-1.5 mb-2">
                        ${renderWorkspaceNode(ws.workspace_id, rootNode, 0)}
                    </div>`;
                }).join("") || `<div class="text-slate-500 italic">No workspace roots configured.</div>`;
                if (rootsEl.dataset.workspaceEventsBound !== "true") {
                    rootsEl.dataset.workspaceEventsBound = "true";
                    rootsEl.addEventListener("click", event => {
                        const button = event.target.closest("button[data-workspace-action]");
                        if (!button || !rootsEl.contains(button)) return;
                        const node = button.closest(".workspace-node");
                        if (button.dataset.workspaceAction === "expand") {
                            void toggleWorkspaceDirectory(button);
                        } else if (node) {
                            void previewWorkspaceFile(node.dataset.workspaceId || "", node.dataset.relativePath || "");
                        }
                    });
                }

                // Rehydrate all open nested folders from server/local state
                await rehydrateExpandedPaths();

                const params = new URLSearchParams(window.location.search);
                const requestedFile = params.get("file");
                const requestedWorkspace = params.get("workspace_id");
                const requestedPath = params.get("path");
                if (requestedFile) {
                    const query = new URLSearchParams({ file: requestedFile });
                    const resolveResponse = await fetch(`/api/workspace-tree/resolve?${query.toString()}`);
                    const resolved = await resolveResponse.json();
                    if (!resolveResponse.ok) {
                        throw new Error(resolved.detail || `HTTP ${resolveResponse.status}`);
                    }
                    await previewWorkspaceFile(resolved.workspace_id, resolved.relative_path);
                } else if (requestedWorkspace || requestedPath) {
                    if (!/^ws-[0-9a-f]{32}$/.test(requestedWorkspace || "") || !requestedPath) {
                        throw new Error("Malformed workspace deep link.");
                    }
                    await previewWorkspaceFile(requestedWorkspace, requestedPath);
                } else if (workspaceTreeState.selectedWorkspaceId && workspaceTreeState.selectedRelativePath) {
                    await previewWorkspaceFile(workspaceTreeState.selectedWorkspaceId, workspaceTreeState.selectedRelativePath);
                }
                workspaceTreeState.initialized = true;
            } catch (err) {
                rootsEl.innerHTML = `<div class="text-rose-400">Workspace Tree unavailable: ${escapeHTML(err.message)}</div>`;
                if (statusEl) statusEl.textContent = "Workspace Tree failed to load.";
            } finally {
                workspaceTreeState.rendering = false;
            }
        }

        let skillsState = {
            allSkills: [],
            activeCategory: 'all',
            searchQuery: '',
            sortBy: 'name-asc'
        };

        async function renderSkillsView() {
            const grid = document.getElementById("skills-grid");
            if (!grid) return;
            if (!grid.children.length || grid.querySelector(".italic")) {
                grid.innerHTML = `<div class="col-span-full text-slate-500 italic">Loading live skill registry…</div>`;
            }
            try {
                const res = await fetch(`${API_PREFIX}/skills`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                skillsState.allSkills = data.skills || [];
                updateSkillCategoryCounts();
                filterSkillsView();
            } catch (err) {
                console.error("Error loading skills registry:", err);
                grid.innerHTML = `<div class="col-span-full text-rose-400 italic">Skills API unavailable: ${escapeHtml(err.message || err)}</div>`;
            }
        }

        function getNormalizedCategory(sk) {
            const c = String(sk.category || "").toLowerCase();
            const n = String(sk.name || "").toLowerCase();
            const d = String(sk.description || "").toLowerCase();
            const combo = `${c} ${n} ${d}`;
            if (c === "governance" || combo.includes("gov") || combo.includes("contract") || combo.includes("claim") || combo.includes("gate") || combo.includes("closeout")) return "governance";
            if (c === "testing" || combo.includes("tdd") || combo.includes("test") || combo.includes("pipeline") || combo.includes("validation") || combo.includes("evidence")) return "testing";
            if (c === "security" || combo.includes("secure") || combo.includes("auth") || combo.includes("credential") || combo.includes("permission")) return "security";
            if (c === "runtime" || combo.includes("runtime") || combo.includes("artifact") || combo.includes("immutable") || combo.includes("provenance")) return "runtime";
            if (c === "infrastructure" || combo.includes("cloudflare") || combo.includes("hermes") || combo.includes("infrastructure") || combo.includes("vm") || combo.includes("proxmox")) return "infrastructure";
            if (c === "workflow" || combo.includes("linear") || combo.includes("routing") || combo.includes("link") || combo.includes("decision") || combo.includes("goal")) return "workflow";
            return "general";
        }

        function updateSkillCategoryCounts() {
            const counts = { all: skillsState.allSkills.length, governance: 0, testing: 0, security: 0, runtime: 0, infrastructure: 0, workflow: 0, general: 0 };
            skillsState.allSkills.forEach(sk => {
                const cat = getNormalizedCategory(sk);
                if (counts[cat] !== undefined) counts[cat]++;
                else counts.general++;
            });
            Object.keys(counts).forEach(cat => {
                const el = document.getElementById(`cat-count-${cat}`);
                if (el) el.textContent = counts[cat];
            });
        }

        function filterSkillCategory(category) {
            skillsState.activeCategory = category;
            document.querySelectorAll(".skill-cat-btn").forEach(btn => {
                btn.classList.remove("active", "bg-indigo-600/20", "text-indigo-300", "border", "border-indigo-500/30", "font-semibold");
                btn.classList.add("text-slate-400", "font-medium");
            });
            const activeBtn = document.getElementById(`cat-btn-${category}`);
            if (activeBtn) {
                activeBtn.classList.remove("text-slate-400", "font-medium");
                activeBtn.classList.add("active", "bg-indigo-600/20", "text-indigo-300", "border", "border-indigo-500/30", "font-semibold");
            }
            filterSkillsView();
        }

        function filterSkillsView() {
            const grid = document.getElementById("skills-grid");
            if (!grid) return;
            const searchInput = document.getElementById("skills-search-input");
            const sortSelect = document.getElementById("skills-sort-select");

            const query = (searchInput?.value || "").trim().toLowerCase();
            const sortBy = sortSelect?.value || "name-asc";

            let filtered = skillsState.allSkills.filter(sk => {
                const cat = getNormalizedCategory(sk);
                if (skillsState.activeCategory !== "all" && cat !== skillsState.activeCategory) {
                    return false;
                }
                if (query) {
                    const name = (sk.name || "").toLowerCase();
                    const desc = (sk.description || "").toLowerCase();
                    const c = (sk.category || "").toLowerCase();
                    return name.includes(query) || desc.includes(query) || c.includes(query);
                }
                return true;
            });

            // Sorting
            filtered.sort((a, b) => {
                const nameA = (a.name || "").toLowerCase();
                const nameB = (b.name || "").toLowerCase();
                if (sortBy === "name-asc") return nameA.localeCompare(nameB);
                if (sortBy === "name-desc") return nameB.localeCompare(nameA);
                if (sortBy === "category") return (a.category || "").localeCompare(b.category || "");
                return 0;
            });

            if (filtered.length === 0) {
                grid.innerHTML = `<div class="col-span-full text-slate-500 italic py-8 text-center">No matching agent capabilities found.</div>`;
                return;
            }

            grid.innerHTML = filtered.map(sk => {
                const name = sk.name || sk.id || 'unnamed-skill';
                const builtin = Boolean(sk.builtin);
                const enabled = sk.enabled !== false;
                const category = sk.category || 'general';
                const version = sk.version || 'v1.0';

                return `
                    <div class="skill-card-container glass-panel p-4 rounded-xl flex flex-col justify-between space-y-3 relative overflow-hidden border border-slate-800/80 hover:border-slate-700 transition shadow-sm group">
                        <div class="flex justify-between items-start gap-2">
                            <span class="skill-version-badge px-2 py-0.5 rounded text-[9px] font-mono font-bold uppercase bg-slate-900 text-slate-400 border border-slate-800">${escapeHtml(version)}</span>
                            <div class="flex items-center gap-1.5">
                                ${builtin ? '<span class="px-2 py-0.5 rounded text-[9px] font-bold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 uppercase">Engine Core</span>' : '<span class="px-2 py-0.5 rounded text-[9px] font-bold bg-amber-500/10 text-amber-400 border border-amber-500/20 uppercase">Custom</span>'}
                                <span class="px-2 py-0.5 rounded text-[9px] font-bold ${enabled ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-slate-800 text-slate-400 border border-slate-700'} uppercase">${enabled ? 'Enabled' : 'Disabled'}</span>
                            </div>
                        </div>
                        <div>
                            <h3 class="skill-card-title font-bold text-slate-200 leading-tight text-sm font-mono group-hover:text-white transition">${escapeHtml(name)}</h3>
                            <div class="text-[10px] text-indigo-400 font-bold uppercase tracking-wider mt-1">
                                <span>${escapeHtml(category)}</span>
                            </div>
                        </div>
                        <p class="skill-card-desc text-xs text-slate-400 leading-relaxed line-clamp-3">${escapeHtml(sk.description || 'No description provided.')}</p>
                        
                        <div class="flex items-center gap-1.5 pt-2 border-t border-slate-800/60">
                            <button type="button" onclick="openSkillDetailModal('${encodeURIComponent(name)}')" class="skill-action-btn-details flex-1 px-2 py-1.5 rounded-lg text-xs font-semibold bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800 transition text-center">
                                Details
                            </button>
                            <button type="button" onclick="toggleSkillEnabled('${encodeURIComponent(name)}')" class="flex-1 ${enabled ? 'skill-action-btn-disable bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700' : 'skill-action-btn-enable bg-emerald-950/40 hover:bg-emerald-900/60 border border-emerald-800/60 text-emerald-300'} text-xs font-semibold py-1.5 rounded-lg transition text-center">
                                ${enabled ? 'Disable' : 'Enable'}
                            </button>
                            ${!builtin ? `
                                <button type="button" onclick="openDeleteSkillModal('${encodeURIComponent(name)}')" class="skill-action-btn-delete px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-rose-950/40 hover:bg-rose-900/60 border border-rose-800/60 text-rose-300 transition text-center flex items-center justify-center" title="Delete Skill">
                                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                                </button>
                            ` : ''}
                        </div>
                    </div>
                `;
            }).join("");
        }

        let pendingDeleteSkillTarget = "";

        function openDeleteSkillModal(encodedName) {
            const name = decodeURIComponent(encodedName);
            pendingDeleteSkillTarget = name;
            const targetEl = document.getElementById("delete-skill-target-name");
            if (targetEl) targetEl.textContent = name;
            const modal = document.getElementById("delete-skill-modal");
            if (modal) modal.classList.remove("hidden");
        }

        function closeDeleteSkillModal() {
            pendingDeleteSkillTarget = "";
            const modal = document.getElementById("delete-skill-modal");
            if (modal) modal.classList.add("hidden");
        }

        async function confirmDeleteCustomSkill() {
            if (!pendingDeleteSkillTarget) return;
            const name = pendingDeleteSkillTarget;
            try {
                const res = await fetch(`${API_PREFIX}/skills/${encodeURIComponent(name)}/delete`, { method: 'POST' });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || data.ok === false) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
                showToast(`Permanently deleted skill: ${name}`);
                closeDeleteSkillModal();
                await renderSkillsView();
            } catch (err) {
                console.error("Error deleting skill:", err);
                showToast(`Delete failed: ${err.message || err}`, true);
            }
        }

        async function toggleSkillEnabled(encodedName) {
            const name = decodeURIComponent(encodedName);
            try {
                const res = await fetch(`${API_PREFIX}/skills/${encodeURIComponent(name)}/toggle`, { method: 'POST' });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || data.ok === false) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
                showToast(`${data.enabled ? 'Enabled' : 'Disabled'} skill "${name}"`);
                await renderSkillsView();
            } catch (err) {
                console.error("Error toggling skill:", err);
                showToast(`Toggle failed: ${err.message || err}`, true);
            }
        }

        async function toggleSkillInstall(encodedSkillId, action) {
            const skillId = decodeURIComponent(encodedSkillId);
            if (action === "uninstall" && !confirm(`Are you sure you want to uninstall skill "${skillId}"?`)) {
                return;
            }
            try {
                const res = await fetch(`${API_PREFIX}/skills/${encodeURIComponent(skillId)}/${action}`, { method: 'POST' });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || data.ok === false) throw new Error(data.error || data.detail || `HTTP ${res.status}`);
                showToast(`${action === 'install' ? 'Installed' : 'Uninstalled'} ${skillId}`);
                await renderSkillsView();
                if (activeTab === 'signals') await renderSignalsView();
            } catch (err) {
                console.error("Error updating skill:", err);
                showToast(`Skill ${action} failed: ${err.message || err}`, true);
            }
        }

        function openUploadSkillModal() {
            const modal = document.getElementById("upload-skill-modal");
            if (modal) modal.classList.remove("hidden");
        }

        function closeUploadSkillModal() {
            const modal = document.getElementById("upload-skill-modal");
            if (modal) modal.classList.add("hidden");
        }

        async function submitCustomSkillUpload() {
            const nameInput = document.getElementById("upload-skill-name");
            const contentInput = document.getElementById("upload-skill-content");
            const name = (nameInput?.value || "").trim();
            const content = (contentInput?.value || "").trim();

            if (!name || !content) {
                showToast("Please provide both skill name and SKILL.md content.", true);
                return;
            }

            try {
                const res = await fetch(`${API_PREFIX}/skills/upload`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, content })
                });
                const data = await res.json();
                if (!res.ok || data.ok === false) throw new Error(data.detail || data.error || 'Upload failed');
                showToast(`Successfully uploaded skill: ${name}`);
                closeUploadSkillModal();
                if (nameInput) nameInput.value = "";
                if (contentInput) contentInput.value = "";
                await renderSkillsView();
            } catch (err) {
                console.error("Skill upload failed:", err);
                showToast(`Upload failed: ${err.message || err}`, true);
            }
        }

        async function openSkillDetailModal(encodedName) {
            const name = decodeURIComponent(encodedName);
            const modal = document.getElementById("skill-detail-modal");
            const titleEl = document.getElementById("skill-detail-title");
            const catEl = document.getElementById("skill-detail-category");
            const descEl = document.getElementById("skill-detail-desc");
            const pathEl = document.getElementById("skill-detail-path");

            if (!modal) return;
            const skill = skillsState.allSkills.find(s => s.name === name || s.id === name);
            if (titleEl) titleEl.textContent = name;
            if (catEl) catEl.textContent = skill?.category || "general";
            if (descEl) descEl.textContent = skill?.description || "No detailed description available.";
            if (pathEl) pathEl.textContent = skill?._path || "Universal Discovery Path";
            modal.classList.remove("hidden");
        }

        function closeSkillDetailModal() {
            const modal = document.getElementById("skill-detail-modal");
            if (modal) modal.classList.add("hidden");
        }

        function signalSeverityColor(value) {
            const severity = String(value || 'info').toLowerCase();
            if (severity.includes('error') || severity.includes('fail')) return 'text-rose-400 border-rose-500/20 bg-rose-500/10';
            if (severity.includes('warn') || severity.includes('block')) return 'text-amber-400 border-amber-500/20 bg-amber-500/10';
            if (severity.includes('lease') || severity.includes('lock')) return 'text-cyan-400 border-cyan-500/20 bg-cyan-500/10';
            if (severity.includes('success') || severity.includes('complete') || severity.includes('dispatch')) return 'text-emerald-400 border-emerald-500/20 bg-emerald-500/10';
            return 'text-indigo-400 border-indigo-500/20 bg-indigo-500/10';
        }

        function signalCard(item) {
            const ts = item.timestamp || item.created_at || item.started_at;
            const isLease = (item.event_type || '').includes('lock_') || item.source === 'swarmlock';
            const sev = item.severity || item.status;
            const color = isLease ? 'text-cyan-400 border-cyan-500/20 bg-cyan-500/10' : signalSeverityColor(sev);
            const transcript = item.transcript ? `<pre class="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg bg-slate-950/80 border border-slate-900 p-2 text-[10px] leading-relaxed text-slate-400">${escapeHtml(item.transcript)}</pre>` : '';
            const issueLink = item.issue_id 
                ? `<a href="https://prismatic.growthwebdev.com/tab/tasks?issue=${encodeURIComponent(item.issue_id)}" target="_blank" class="text-[10px] text-indigo-400 hover:text-indigo-300 underline font-mono font-semibold">${escapeHtml(item.issue_id)}</a>`
                : '';
            return `
                <div class="rounded-lg border border-slate-900 bg-slate-950/60 p-3 space-y-2" data-agent-signal-card>
                    <div class="flex flex-wrap items-center justify-between gap-2">
                        <div class="flex flex-wrap items-center gap-2">
                            <span class="text-slate-500 font-mono text-[10px]">${escapeHtml(ts ? formatDate(ts) : '—')}</span>
                            <span class="px-2 py-0.5 rounded border text-[10px] font-bold uppercase ${color}">${escapeHtml(item.event_type || item.status || 'event')}</span>
                            ${issueLink}
                        </div>
                        ${item.metadata?.duration_seconds ? `<span class="text-[10px] text-slate-500 font-mono">${item.metadata.duration_seconds}s</span>` : ''}
                    </div>
                    <div class="text-xs text-slate-300 whitespace-pre-wrap leading-relaxed">${escapeHtml(item.message || item.status || 'Signal event')}</div>
                    <div class="flex flex-wrap gap-2 text-[10px] text-slate-500">
                        ${item.run_id ? `<span>run=${escapeHtml(item.run_id)}</span>` : ''}
                        ${item.log_path ? `<span>log=${escapeHtml(item.log_path)}</span>` : ''}
                        <span>source=${escapeHtml(item.source || 'unknown')}</span>
                    </div>
                    ${transcript}
                </div>
            `;
        }

        let discoveredAgentsList = [];
        let signalSeverityFilter = 'all';
        let signalSearchQuery = '';
        let signalStreamPaused = false;

        function openAgentNudgeModal() {
            const modal = document.getElementById("agent-nudge-modal");
            if (modal) modal.classList.remove("hidden");
        }

        function closeAgentNudgeModal() {
            const modal = document.getElementById("agent-nudge-modal");
            if (modal) modal.classList.add("hidden");
        }

        function openEmitSignalModal() {
            const modal = document.getElementById("agent-emit-signal-modal");
            if (modal) modal.classList.remove("hidden");
        }

        function closeEmitSignalModal() {
            const modal = document.getElementById("agent-emit-signal-modal");
            if (modal) modal.classList.add("hidden");
        }

        function setSignalSeverityFilter(sev) {
            signalSeverityFilter = sev || 'all';
            document.querySelectorAll('#signals-severity-filters button').forEach(btn => {
                const isMatch = btn.dataset.sevFilter === signalSeverityFilter;
                btn.className = `px-2 py-0.5 rounded text-[10px] font-bold border transition ${
                    isMatch 
                        ? 'bg-indigo-600/30 text-indigo-300 border-indigo-500/50' 
                        : 'bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-200'
                }`;
            });
            renderSignalStreamConsole();
        }

        function filterSignalStream() {
            const input = document.getElementById("signals-search-input");
            signalSearchQuery = (input?.value || "").toLowerCase().trim();
            renderSignalStreamConsole();
        }

        function toggleSignalsPause() {
            signalStreamPaused = !signalStreamPaused;
            const btn = document.getElementById("signals-pause-btn");
            const dot = document.getElementById("signals-live-dot");
            if (btn) {
                btn.textContent = signalStreamPaused ? "Resume" : "Pause";
                btn.className = signalStreamPaused
                    ? "px-2 py-1 rounded text-[10px] font-semibold bg-amber-600/30 text-amber-300 border border-amber-500/50 transition"
                    : "px-2 py-1 rounded text-[10px] font-semibold bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition";
            }
            if (dot) {
                dot.className = signalStreamPaused
                    ? "w-2 h-2 rounded-full bg-amber-500"
                    : "w-2 h-2 rounded-full bg-emerald-500 animate-pulse";
            }
        }

        function clearSignalConsole() {
            const consoleBox = document.getElementById("signals-log-box") || document.getElementById("signals-console-stream");
            if (consoleBox) {
                consoleBox.innerHTML = '<div class="text-slate-500 italic py-2">Console cleared. Waiting for active telemetry signals...</div>';
            }
            const countEl = document.getElementById("signals-stream-count");
            if (countEl) countEl.textContent = "0";
        }

        async function submitAgentNudge() {
            const agentSelect = document.getElementById("nudge-target-agent");
            const messageText = document.getElementById("nudge-message-text");
            const agent = agentSelect?.value || "all";
            const message = (messageText?.value || "").trim();

            if (!message) {
                showToast("Please enter guidance instructions to inject.", true);
                return;
            }

            try {
                const res = await fetch("/api/gateway/signals/nudge", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ agent, message })
                });
                const data = await res.json();
                if (!res.ok || data.ok === false) throw new Error(data.detail || data.error || "Nudge failed");
                showToast(`Injected guidance for agent "${agent}"`);
                closeAgentNudgeModal();
                if (messageText) messageText.value = "";
                await renderSignalsView();
            } catch (err) {
                console.error("Nudge injection failed:", err);
                showToast(`Nudge failed: ${err.message || err}`, true);
            }
        }

        async function submitCustomSignal() {
            const agent = document.getElementById("emit-signal-agent")?.value.trim() || "agy";
            const severity = document.getElementById("emit-signal-severity")?.value || "info";
            const event_type = document.getElementById("emit-signal-type")?.value.trim() || "custom";
            const issue_id = document.getElementById("emit-signal-issue")?.value.trim() || "";
            const message = document.getElementById("emit-signal-message")?.value.trim() || "";

            if (!message) {
                showToast("Please enter a signal message.", true);
                return;
            }

            try {
                const res = await fetch("/api/gateway/signals/emit", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ agent, severity, event_type, issue_id, message, source: "operator_console" })
                });
                const data = await res.json();
                if (!res.ok || data.ok === false) throw new Error(data.detail || "Failed to emit signal");
                showToast(`Emitted signal for agent "${agent}"`);
                closeEmitSignalModal();
                const msgInput = document.getElementById("emit-signal-message");
                if (msgInput) msgInput.value = "";
                await renderSignalsView();
            } catch (err) {
                console.error("Signal emit error:", err);
                showToast(`Signal emit failed: ${err.message || err}`, true);
            }
        }

        function renderAgentTabs(agents) {
            const tabsBox = document.getElementById("signals-agent-tabs");
            if (!tabsBox) return;
            const allActive = activeSignalsAgent === "all" ? "bg-indigo-600/20 text-indigo-300 border-indigo-500/30 font-bold" : "bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-200";
            let html = `<button onclick="setSignalsAgent('all')" data-agent-tab="all" class="signals-agent-tab px-3 py-1.5 rounded-lg text-xs uppercase border transition flex-shrink-0 ${allActive}">All (${agents.length})</button>`;
            
            agents.forEach(a => {
                const aid = a.agent_id;
                const isActive = activeSignalsAgent === aid;
                const activeClass = isActive ? "bg-indigo-600/20 text-indigo-300 border-indigo-500/30 font-bold" : "bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-200";
                html += `<button onclick="setSignalsAgent('${aid}')" data-agent-tab="${aid}" class="signals-agent-tab px-3 py-1.5 rounded-lg text-xs uppercase border transition flex-shrink-0 ${activeClass}">${a.icon || '🤖'} ${escapeHtml(aid)} (${a.signal_count || 0})</button>`;
            });
            tabsBox.innerHTML = html;
        }

        function renderSignalPanes(payload) {
            latestSignalsPayload = payload || latestSignalsPayload || { items: [], by_agent: {}, counts: {} };
            const panes = document.getElementById('signals-agent-panes');
            if (!panes) return;

            const knownAgents = discoveredAgentsList.length ? discoveredAgentsList : [
                { agent_id: 'agy', name: 'Antigravity (AGY)', icon: '⚡', active_model: 'gemini-2.5-pro', status: 'idle' },
                { agent_id: 'hermes', name: 'Hermes Orchestrator', icon: '🌐', active_model: 'claude-3-7-sonnet', status: 'idle' },
                { agent_id: 'kai', name: 'Kai (UI Specialist)', icon: '🎨', active_model: 'claude-3-7-sonnet', status: 'idle' },
                { agent_id: 'fred', name: 'Fred (TDD Specialist)', icon: '⚙️', active_model: 'claude-3-7-sonnet', status: 'idle' },
                { agent_id: 'george', name: 'George (Peer Reviewer)', icon: '🛡️', active_model: 'gpt-4o', status: 'idle' },
                { agent_id: 'autobot', name: 'Autobot (CI Worker)', icon: '🤖', active_model: 'deepseek-r1', status: 'idle' }
            ];

            const byAgent = latestSignalsPayload.by_agent || {};
            const visibleAgents = activeSignalsAgent === 'all' 
                ? knownAgents 
                : knownAgents.filter(a => a.agent_id === activeSignalsAgent);

            panes.innerHTML = visibleAgents.map(ag => {
                const aid = ag.agent_id;
                const agentItems = byAgent[aid] || [];
                const statusBadge = ag.status === 'executing' 
                    ? '<span class="px-2 py-0.5 rounded text-[9px] font-bold uppercase bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Executing</span>'
                    : ag.status === 'errored'
                    ? '<span class="px-2 py-0.5 rounded text-[9px] font-bold uppercase bg-rose-500/10 text-rose-400 border border-rose-500/20">Errored</span>'
                    : ag.status === 'waiting_input'
                    ? '<span class="px-2 py-0.5 rounded text-[9px] font-bold uppercase bg-amber-500/10 text-amber-400 border border-amber-500/20">Waiting</span>'
                    : '<span class="px-2 py-0.5 rounded text-[9px] font-bold uppercase bg-slate-800 text-slate-400 border border-slate-700">Idle</span>';

                return `
                    <section class="agent-telemetry-pane rounded-xl border border-slate-800 bg-slate-950/40 overflow-hidden flex flex-col justify-between" data-agent-pane="${aid}">
                        <div class="px-4 py-3 border-b border-slate-800 bg-slate-900/60 flex items-center justify-between">
                            <div class="flex items-center gap-2">
                                <span class="text-base">${ag.icon || '🤖'}</span>
                                <div>
                                    <h3 class="agent-title-text text-xs font-bold uppercase tracking-wider text-slate-200">${escapeHtml(ag.name || aid)}</h3>
                                    <div class="text-[10px] text-indigo-400 font-mono mt-0.5 flex flex-wrap items-center gap-1.5">
                                        <span>MODEL: ${escapeHtml(ag.active_model || 'default')}</span>
                                        <span class="text-slate-600">•</span>
                                        <span class="text-slate-400 font-sans">${escapeHtml(ag.source || 'Registered Harness')}</span>
                                    </div>
                                </div>
                            </div>
                            <div class="flex items-center gap-2">
                                ${statusBadge}
                                <span class="text-[10px] text-slate-500 font-mono">${agentItems.length} events</span>
                            </div>
                        </div>
                        <div class="p-3 space-y-3 max-h-[520px] overflow-y-auto">
                            ${ag.current_task ? `
                                <div class="p-2 rounded-lg bg-slate-900/80 border border-slate-800 text-[11px] text-slate-300 flex items-center justify-between">
                                    <span class="text-slate-400 font-mono">Active Task:</span>
                                    <a href="https://prismatic.growthwebdev.com/tab/tasks?issue=${encodeURIComponent(ag.current_task)}" target="_blank" class="font-bold font-mono text-cyan-400 hover:text-cyan-300 underline">${escapeHtml(ag.current_task)}</a>
                                </div>
                            ` : ''}
                            ${agentItems.length ? agentItems.slice(0, 25).map(signalCard).join('') : `<div class="agent-desc-text text-xs text-slate-500 italic py-6 text-center">No ${escapeHtml(aid)} signals recorded yet. Ready for dispatch.</div>`}
                        </div>
                    </section>
                `;
            }).join('');

            renderSignalStreamConsole();
        }

        function renderSignalStreamConsole() {
            const consoleBox = document.getElementById("signals-log-box") || document.getElementById("signals-console-stream");
            if (!consoleBox) return;

            let items = latestSignalsPayload?.items || [];
            
            // Filter by agent if not "all"
            if (activeSignalsAgent && activeSignalsAgent !== "all") {
                items = items.filter(i => (i.agent || "").toLowerCase() === activeSignalsAgent.toLowerCase());
            }

            // Filter by severity
            if (signalSeverityFilter && signalSeverityFilter !== "all") {
                items = items.filter(i => {
                    const sev = (i.severity || i.status || "").toLowerCase();
                    const evt = (i.event_type || "").toLowerCase();
                    if (signalSeverityFilter === "lease") return sev.includes("lease") || evt.includes("lock_") || i.source === "swarmlock";
                    if (signalSeverityFilter === "warning") return sev.includes("warn");
                    if (signalSeverityFilter === "error") return sev.includes("err") || sev.includes("fail");
                    if (signalSeverityFilter === "info") return !sev.includes("warn") && !sev.includes("err") && !sev.includes("lease") && !evt.includes("lock_");
                    return true;
                });
            }

            // Filter by search query
            if (signalSearchQuery) {
                items = items.filter(i => {
                    const blob = `${i.agent || ''} ${i.event_type || ''} ${i.issue_id || ''} ${i.message || ''} ${i.source || ''}`.toLowerCase();
                    return blob.includes(signalSearchQuery);
                });
            }

            const countEl = document.getElementById("signals-stream-count");
            if (countEl) countEl.textContent = items.length;

            if (!items.length) {
                consoleBox.innerHTML = '<div class="text-slate-500 italic py-2">No matching telemetry signals found.</div>';
                return;
            }

            consoleBox.innerHTML = items.slice(0, 100).map(item => {
                const ts = item.timestamp || item.created_at || item.started_at;
                const isLease = (item.event_type || '').includes('lock_') || item.source === 'swarmlock';
                const sev = item.severity || item.status;
                const color = isLease ? 'text-cyan-400' : signalSeverityColor(sev).split(' ')[0];
                const issueMarkup = item.issue_id 
                    ? `<a href="https://prismatic.growthwebdev.com/tab/tasks?issue=${encodeURIComponent(item.issue_id)}" target="_blank" class="text-indigo-400 hover:text-indigo-300 underline font-semibold text-[10px]">[${escapeHtml(item.issue_id)}]</a>`
                    : '';
                const agentBadge = `<span class="px-1.5 py-0.2 rounded bg-slate-900 border border-slate-800 font-bold ${color}">[${escapeHtml(item.agent || item.source || 'signal')}]</span>`;

                return `
                    <div class="border-b border-slate-900/80 pb-1.5 mb-1.5 flex flex-wrap items-baseline gap-1.5 text-slate-300 signal-log-item">
                        <span class="text-slate-500 font-bold text-[10px] flex-shrink-0">[${escapeHtml(ts ? formatDate(ts) : '—')}]</span>
                        ${agentBadge}
                        <span class="text-slate-400 font-medium text-[10px]">${escapeHtml(item.event_type || item.status || 'event')}</span>
                        ${issueMarkup}
                        <span class="text-slate-300 break-all">${escapeHtml(item.message || '')}</span>
                    </div>
                `;
            }).join('');

            if (!signalStreamPaused) {
                const scrollParent = document.getElementById("signals-console-stream") || consoleBox;
                scrollParent.scrollTop = scrollParent.scrollHeight;
            }
        }

        let latestSwarmLockData = null;
        let activeEvictTarget = null;
        let activeSwarmWorkspace = "all";

        function getAgentColor(agentName) {
            let hash = 0;
            const str = String(agentName || "unknown");
            for (let i = 0; i < str.length; i++) hash = str.charCodeAt(i) + ((hash << 5) - hash);
            const h = Math.abs(hash) % 360;
            return `hsl(${h}, 65%, 45%)`;
        }

        function setSwarmWorkspace(ws) {
            activeSwarmWorkspace = ws;
            if (latestSwarmLockData) renderSwarmLockCockpit(latestSwarmLockData);
        }

        function renderSwarmLockCockpit(data) {
            latestSwarmLockData = data;
            const grid = document.getElementById("swarmlock-leases-grid");
            const filterBar = document.getElementById("swarmlock-workspace-filters");
            const activeLocksCountEl = document.getElementById("sig-metric-active-locks");
            const deflectionsEl = document.getElementById("sig-metric-deflections");
            const statusEl = document.getElementById("sig-metric-status");

            const allLocks = data?.locks || [];
            const activeLockCount = data?.active_lock_count ?? allLocks.length;
            const deflections = data?.deflected_collisions ?? 0;

            if (activeLocksCountEl) activeLocksCountEl.textContent = activeLockCount;
            if (deflectionsEl) deflectionsEl.textContent = deflections;
            if (statusEl) {
                if (activeLockCount > 0) {
                    statusEl.textContent = `${activeLockCount} Active Leases`;
                    statusEl.className = "text-xl font-bold font-mono text-cyan-400 mt-0.5";
                } else {
                    statusEl.textContent = "Optimal (Free)";
                    statusEl.className = "text-xl font-bold font-mono text-emerald-400 mt-0.5";
                }
            }

            // Render Workspace Filter Chips
            if (filterBar) {
                const workspaces = {};
                workspaces["all"] = allLocks.length;
                allLocks.forEach(l => {
                    const ws = l.workspace || "prismatic-engine";
                    workspaces[ws] = (workspaces[ws] || 0) + 1;
                });

                let filterHtml = "";
                for (const [wsName, count] of Object.entries(workspaces)) {
                    const isActive = activeSwarmWorkspace === wsName;
                    const activeClass = isActive
                        ? "bg-cyan-600/30 text-cyan-300 border-cyan-500/50 font-bold"
                        : "bg-slate-900/80 text-slate-400 border-slate-800 hover:text-slate-200";
                    filterHtml += `
                        <button type="button" onclick="setSwarmWorkspace('${escapeHtml(wsName)}')" class="swarmlock-ws-chip px-3 py-1.5 rounded-lg text-[11px] font-mono border transition inline-flex items-center gap-2 ${activeClass}">
                            <span>${escapeHtml(wsName === 'all' ? 'All Workspaces' : wsName)}</span>
                            <span class="swarmlock-ws-count-badge font-mono">${count}</span>
                        </button>
                    `;
                }
                filterBar.innerHTML = filterHtml;
            }

            if (!grid) return;

            const visibleLocks = activeSwarmWorkspace === "all"
                ? allLocks
                : allLocks.filter(l => (l.workspace || "prismatic-engine") === activeSwarmWorkspace);

            if (!visibleLocks.length) {
                grid.innerHTML = `
                    <div class="col-span-full p-6 text-center text-slate-500 text-xs italic bg-slate-900/30 rounded-xl border border-slate-800/50 flex flex-col items-center justify-center gap-2">
                        <div class="flex items-center gap-2 text-slate-400">
                            ${PRISMATIC_THEME.icons.shield}
                            <span class="font-semibold">No Active Resource Contention ${activeSwarmWorkspace !== 'all' ? `in workspace "${activeSwarmWorkspace}"` : ''}</span>
                        </div>
                        <p class="text-[11px] text-slate-500">All workspaces and files are free for agent dispatch. Collisions deflected to date: <strong class="text-amber-400 font-mono">${deflections}</strong>.</p>
                    </div>
                `;
                return;
            }

            grid.innerHTML = visibleLocks.map((lock, idx) => {
                const resource = lock.resource || "unknown_resource";
                const ext = resource.split(".").pop().toLowerCase();
                const isFile = resource.includes(".");
                const resourceIcon = isFile ? PRISMATIC_THEME.icons.file(ext) : PRISMATIC_THEME.icons.folder(true);
                const holder = lock.holder || "unknown_agent";
                const intention = (lock.intention || "EXCLUSIVE_MUTATION").toUpperCase();
                const intentionBadge = PRISMATIC_THEME.badge(intention, intention);
                const agentColor = getAgentColor(holder);
                const ttlRemaining = Math.max(0, Math.round((lock.expires_at || 0) - Date.now() / 1000));
                const ttlTotal = lock.ttl_seconds || 30;
                const ttlPct = Math.min(100, Math.max(0, (ttlRemaining / ttlTotal) * 100));

                let taskLinkHtml = `<span class="text-slate-500 text-[10px]">No task bound</span>`;
                if (lock.linear_issue || lock.task_id) {
                    const taskId = lock.linear_issue || lock.task_id;
                    const isLinear = taskId.startsWith("GRO-") || /^[A-Z]{2,}-\d+$/.test(taskId);
                    const taskIcon = isLinear ? PRISMATIC_THEME.icons.linear : PRISMATIC_THEME.icons.kanban;
                    const taskUrl = isLinear
                        ? `https://prismatic.growthwebdev.com/tab/tasks?issue=${encodeURIComponent(taskId)}`
                        : `https://prismatic.growthwebdev.com/tab/tasks?task=${encodeURIComponent(taskId)}`;
                    taskLinkHtml = `
                        <a href="${taskUrl}" target="_blank" class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-indigo-950/60 border border-indigo-800/60 text-indigo-300 hover:text-white text-[10px] font-mono transition">
                            ${taskIcon}
                            <span>${escapeHtml(taskId)}</span>
                            ${PRISMATIC_THEME.icons.externalLink}
                        </a>
                    `;
                }

                let collisionHtml = "";
                const contenders = lock.contentions || lock.contenders || [];
                if (contenders && contenders.length > 0) {
                    collisionHtml = `
                        <div class="mt-2 p-2 rounded-lg bg-amber-950/30 border border-amber-800/40 text-[10px] text-amber-300 space-y-1 animate-pulse">
                            <div class="flex items-center gap-1 font-bold">
                                ${PRISMATIC_THEME.icons.warning}
                                <span>Contention Deflected (${contenders.length} waiting)</span>
                            </div>
                            <div class="font-mono text-[9px] text-amber-200/80">
                                ${contenders.map(c => `${escapeHtml(c.agent_id || c.agent)} contended`).join(", ")}
                            </div>
                        </div>
                    `;
                }

                return `
                    <div class="swarmlock-lease-card p-3.5 rounded-xl border border-slate-800 bg-slate-900/60 flex flex-col justify-between space-y-3 relative overflow-hidden shadow-lg hover:border-slate-700 transition" data-lock-id="${idx}">
                        <div class="flex items-start justify-between gap-2">
                            <div class="flex items-center gap-2 min-w-0">
                                <span class="p-1.5 rounded-lg bg-slate-800/80 border border-slate-700/60">${resourceIcon}</span>
                                <div class="min-w-0">
                                    <div class="font-mono font-bold text-xs text-slate-200 truncate" title="${escapeHtml(resource)}">${escapeHtml(resource)}</div>
                                    <div class="text-[10px] text-slate-500 truncate mt-0.5">Lease token: <span class="font-mono text-slate-400">${escapeHtml(String(lock.lease_id || lock.token || "").slice(0, 8))}...</span></div>
                                </div>
                            </div>
                            <div class="flex-shrink-0">${intentionBadge}</div>
                        </div>

                        <div class="flex items-center justify-between gap-2 pt-1 border-t border-slate-800/80">
                            <div class="flex items-center gap-1.5">
                                <span class="w-2.5 h-2.5 rounded-full flex-shrink-0" style="background-color: ${agentColor};"></span>
                                <span class="font-bold text-xs text-slate-200">${escapeHtml(holder)}</span>
                            </div>
                            <div class="flex items-center gap-1.5">${taskLinkHtml}</div>
                        </div>

                        <div>
                            <div class="flex items-center justify-between text-[10px] text-slate-400 font-mono mb-1">
                                <span class="flex items-center gap-1 text-emerald-400">
                                    ${PRISMATIC_THEME.icons.heartbeat}
                                    <span>Heartbeat TTL</span>
                                </span>
                                <span>${ttlRemaining}s remaining</span>
                            </div>
                            <div class="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                                <div class="h-full bg-gradient-to-r from-emerald-500 to-cyan-400 transition-all duration-1000" style="width: ${ttlPct}%;"></div>
                            </div>
                        </div>

                        ${collisionHtml}

                        <div class="flex items-center justify-end gap-2 pt-2 border-t border-slate-800/60">
                            <button type="button" onclick="openInspectLockModal(${idx})" class="px-2.5 py-1 rounded-lg text-[10px] font-semibold bg-slate-800 hover:bg-slate-700 text-slate-300 transition flex items-center gap-1.5">
                                ${PRISMATIC_THEME.icons.inspect}
                                <span>Inspect</span>
                            </button>
                            <button type="button" onclick="openEvictLockModal(${idx})" class="px-2.5 py-1 rounded-lg text-[10px] font-bold bg-rose-950/60 hover:bg-rose-900/80 text-rose-300 border border-rose-800/60 transition flex items-center gap-1.5">
                                ${PRISMATIC_THEME.icons.trash}
                                <span>Force Evict</span>
                            </button>
                        </div>
                    </div>
                `;
            }).join("");
        }

        // 📜 SwarmLock Audit History Drawer Operations
        function toggleSwarmLockHistoryDrawer() {
            const drawer = document.getElementById("swarmlock-history-drawer");
            if (!drawer) return;
            const isHidden = drawer.classList.contains("hidden");
            if (isHidden) {
                drawer.classList.remove("hidden");
                fetchSwarmLockHistory();
            } else {
                drawer.classList.add("hidden");
            }
        }

        async function fetchSwarmLockHistory() {
            const tbody = document.getElementById("swarmlock-history-tbody");
            if (!tbody) return;
            try {
                const res = await fetch("/api/gateway/swarmlock/history?limit=50");
                if (res.ok) {
                    const data = await res.json();
                    renderSwarmLockHistory(data.events || []);
                }
            } catch (err) {
                console.error("Failed to load lock history:", err);
            }
        }

        function renderSwarmLockHistory(events) {
            const tbody = document.getElementById("swarmlock-history-tbody");
            if (!tbody) return;
            if (!events.length) {
                tbody.innerHTML = `<tr><td colspan="6" class="py-4 text-center text-slate-500 italic">No historical events recorded in current session.</td></tr>`;
                return;
            }
            tbody.innerHTML = events.map(e => {
                const ts = e.timestamp ? formatDate(e.timestamp) : "—";
                const type = e.event_type || "event";
                let badgeClass = "bg-slate-800 text-slate-400 border-slate-700";
                if (type === "acquired") badgeClass = "bg-emerald-950/60 text-emerald-300 border-emerald-800/60";
                else if (type === "deflected") badgeClass = "bg-amber-950/60 text-amber-300 border-amber-800/60";
                else if (type === "evicted") badgeClass = "bg-rose-950/60 text-rose-300 border-rose-800/60";
                else if (type === "released") badgeClass = "bg-indigo-950/60 text-indigo-300 border-indigo-800/60";

                const dur = e.duration_seconds ? `${e.duration_seconds}s` : "—";
                const details = e.reason || (e.holder ? `held by ${e.holder}` : (e.intention || "—"));
                const resName = e.resource || "—";
                
                // Format resource as clickable link to /workspaces
                let resourceHtml = escapeHtml(resName);
                if (resName && resName !== "—" && !resName.startsWith("workspace:")) {
                    const cleanPath = String(resName).replace(/^file:\/\//, "").replace(/^[a-zA-Z]:[/\\]/, "").replace(/\\/g, "/");
                    resourceHtml = `
                        <a href="/workspaces?file=${encodeURIComponent(cleanPath)}" onclick="openWorkspaceFile('${escapeHtml(cleanPath)}', event)" class="text-cyan-300 hover:text-cyan-100 hover:underline flex items-center gap-1 font-mono group" title="View in Workspaces: ${escapeHtml(cleanPath)}">
                            <span class="truncate max-w-[180px]">${escapeHtml(cleanPath)}</span>
                            <svg class="w-2.5 h-2.5 opacity-60 group-hover:opacity-100 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
                        </a>
                    `;
                }

                return `
                    <tr class="hover:bg-slate-900/40 transition">
                        <td class="py-1.5 px-2 text-slate-400 text-[10px] whitespace-nowrap">${escapeHtml(ts)}</td>
                        <td class="py-1.5 px-2 whitespace-nowrap"><span class="px-1.5 py-0.5 rounded border text-[9px] font-bold uppercase ${badgeClass}">${escapeHtml(type)}</span></td>
                        <td class="py-1.5 px-2 max-w-[200px]">${resourceHtml}</td>
                        <td class="py-1.5 px-2 font-semibold text-slate-200">${escapeHtml(e.agent_id || e.holder || "—")}</td>
                        <td class="py-1.5 px-2 text-slate-400 font-mono text-[10px]">${escapeHtml(dur)}</td>
                        <td class="py-1.5 px-2 text-slate-400 text-[10px] truncate max-w-[200px]" title="${escapeHtml(details)}">${escapeHtml(details)}</td>
                    </tr>
                `;
            }).join("");
        }

        // ⚙️ SwarmLock Policy Configuration Modal
        async function openSwarmLockConfigModal() {
            const modal = document.getElementById("swarmlock-config-modal");
            const ttlInput = document.getElementById("swarmlock-config-ttl");
            const deflectionsEl = document.getElementById("swarmlock-config-deflections");
            if (!modal) return;
            try {
                const res = await fetch("/api/gateway/swarmlock/config");
                if (res.ok) {
                    const data = await res.json();
                    if (ttlInput) ttlInput.value = data.stale_ttl_seconds || 300;
                    if (deflectionsEl) deflectionsEl.textContent = data.total_deflected_collisions || 0;
                }
            } catch (e) {}
            modal.classList.remove("hidden");
        }

        function closeSwarmLockConfigModal() {
            const modal = document.getElementById("swarmlock-config-modal");
            if (modal) modal.classList.add("hidden");
        }

        async function saveSwarmLockConfig() {
            const ttlInput = document.getElementById("swarmlock-config-ttl");
            const ttl = parseFloat(ttlInput?.value || "300");
            try {
                const res = await fetch("/api/gateway/swarmlock/config", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ stale_ttl_seconds: ttl })
                });
                if (res.ok) {
                    showToast(`Updated SwarmLock TTL to ${ttl}s`);
                    closeSwarmLockConfigModal();
                    await renderSignalsView();
                } else {
                    const err = await res.json();
                    alert(`Failed to save config: ${err.detail || err.error}`);
                }
            } catch (err) {
                alert(`Error saving config: ${err.message}`);
            }
        }

        function openInspectLockModal(idx) {
            const modal = document.getElementById("swarmlock-inspect-modal");
            const body = document.getElementById("swarmlock-inspect-body");
            if (!modal || !body || !latestSwarmLockData || !latestSwarmLockData.locks || !latestSwarmLockData.locks[idx]) return;

            const lock = latestSwarmLockData.locks[idx];
            body.innerHTML = `
                <div class="p-3 rounded-lg bg-slate-950 border border-slate-800 space-y-2">
                    <div><span class="text-slate-500">Resource:</span> <span class="text-cyan-300 font-bold">${escapeHtml(lock.resource)}</span></div>
                    <div><span class="text-slate-500">Holder Agent:</span> <span class="text-slate-200 font-bold">${escapeHtml(lock.holder)}</span></div>
                    <div><span class="text-slate-500">Workspace Domain:</span> <span class="text-emerald-300 font-bold">${escapeHtml(lock.workspace || "prismatic-engine")}</span></div>
                    <div><span class="text-slate-500">Intention:</span> <span class="text-amber-300">${escapeHtml(lock.intention || "EXCLUSIVE_MUTATION")}</span></div>
                    <div><span class="text-slate-500">Lease Token:</span> <span class="text-slate-400 break-all font-mono">${escapeHtml(lock.lease_id || lock.token || "—")}</span></div>
                    <div><span class="text-slate-500">Expires At:</span> <span class="text-slate-300">${new Date((lock.expires_at || 0) * 1000).toLocaleString()}</span></div>
                    <div><span class="text-slate-500">Associated Task:</span> <span class="text-indigo-400 font-bold">${escapeHtml(lock.task_id || lock.linear_issue || "None")}</span></div>
                    ${lock.transcript_ref ? `<div><span class="text-slate-500">Transcript Ref:</span> <span class="text-slate-400">${escapeHtml(lock.transcript_ref)}</span></div>` : ""}
                </div>
            `;
            modal.classList.remove("hidden");
        }

        function closeInspectLockModal() {
            const modal = document.getElementById("swarmlock-inspect-modal");
            if (modal) modal.classList.add("hidden");
        }

        function openEvictLockModal(idx) {
            const modal = document.getElementById("swarmlock-evict-modal");
            const resEl = document.getElementById("swarmlock-evict-resource");
            const holderEl = document.getElementById("swarmlock-evict-holder");
            if (!modal || !latestSwarmLockData || !latestSwarmLockData.locks || !latestSwarmLockData.locks[idx]) return;

            const lock = latestSwarmLockData.locks[idx];
            activeEvictTarget = lock;
            if (resEl) resEl.textContent = lock.resource;
            if (holderEl) holderEl.textContent = lock.holder;
            modal.classList.remove("hidden");
        }

        function closeEvictLockModal() {
            const modal = document.getElementById("swarmlock-evict-modal");
            if (modal) modal.classList.add("hidden");
            activeEvictTarget = null;
        }

        async function confirmEvictLock() {
            if (!activeEvictTarget) return;
            const reason = document.getElementById("swarmlock-evict-reason")?.value || "Operator manual eviction";
            try {
                const res = await fetch("/api/gateway/swarmlock/evict", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ resource: activeEvictTarget.resource, token: activeEvictTarget.token, reason: reason })
                });
                if (res.ok) {
                    closeEvictLockModal();
                    await renderSignalsView();
                } else {
                    const err = await res.json();
                    alert(`Failed to evict lock: ${err.detail || err.error || 'Unknown error'}`);
                }
            } catch (e) {
                alert(`Error executing eviction: ${e.message}`);
            }
        }

        async function renderSignalsView() {
            const container = document.getElementById("signals-log-box");
            if (container && !container.children.length) {
                container.innerHTML = `<div class="text-slate-500 italic">Loading assigned-agent signal streams…</div>`;
            }

            try {
                const [agentsRes, signalsRes, swarmlockRes] = await Promise.all([
                    fetch("/api/gateway/agents").catch(() => null),
                    fetch("/api/gateway/signals?limit=200").catch(() => null),
                    fetch("/api/gateway/swarmlock/status").catch(() => null)
                ]);

                if (swarmlockRes && swarmlockRes.ok) {
                    const swarmData = await swarmlockRes.json();
                    renderSwarmLockCockpit(swarmData);
                }

                if (agentsRes && agentsRes.ok) {
                    const agentData = await agentsRes.json();
                    discoveredAgentsList = agentData.agents || [];
                    const totalEl = document.getElementById("sig-metric-total");
                    if (totalEl) totalEl.textContent = discoveredAgentsList.length;
                    renderAgentTabs(discoveredAgentsList);
                }

                if (signalsRes && signalsRes.ok) {
                    const payload = await signalsRes.json();
                    renderSignalPanes(payload);
                }
            } catch (err) {
                console.error("Error loading agent signals:", err);
                if (container) container.innerHTML = `<div class="text-rose-400 italic">Signals API unavailable: ${escapeHtml(err.message || err)}</div>`;
            }
        }

        async function fetchQuotaSummary() {
            try {
                const quotaRes = await fetch(`${API_PREFIX}/quota`);
                if (quotaRes.ok) {
                    const data = await quotaRes.json();
                    const activeModels = (data.current || []).length;
                    const freshness = data.snapshot_age_sec !== null && data.snapshot_age_sec !== undefined ? formatAge(data.snapshot_age_sec) : "Freshness unknown";
                    const tone = data.snapshot_age_sec !== null && data.snapshot_age_sec !== undefined && data.snapshot_age_sec < 300 ? "green" : data.snapshot_age_sec !== null && data.snapshot_age_sec !== undefined && data.snapshot_age_sec < 900 ? "amber" : "red";
                    setSummaryBadge(
                        "summary-quota-badge",
                        "summary-quota-meta",
                        tone,
                        tone === "green" ? "FRESH" : tone === "amber" ? "STALE" : "OLD",
                        `${freshness} · ${activeModels} active model${activeModels === 1 ? "" : "s"}`
                    );
                }
            } catch (err) {
                console.error("Error loading quota summary:", err);
            }
        }

        async function fetchCompletedWorkGate() {
            const badge = document.getElementById("completed-work-gate-badge");
            const classification = document.getElementById("completed-work-classification");
            const artifact = document.getElementById("completed-work-artifact");
            const proofClasses = document.getElementById("completed-work-proof-classes");
            const verifier = document.getElementById("completed-work-verifier");
            const freshness = document.getElementById("completed-work-freshness");
            const hostedSignal = document.getElementById("completed-work-hosted-signal");
            const proof = document.getElementById("completed-work-receipt-proof");
            const marker = document.getElementById("completed-work-gate-marker");
            try {
                const res = await fetch(`${API_PREFIX}/verification/receipts?limit=1`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const latest = (data.receipts || [])[0];
                const accepted = latest?.classification === "accepted" && latest?.merge_eligible === true;
                if (badge) {
                    badge.textContent = latest ? (accepted ? "Native Accepted" : "Native Blocked") : "No Receipts";
                    badge.className = `px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${latest ? (accepted ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300' : 'border-amber-500/20 bg-amber-500/10 text-amber-300') : 'border-slate-700 bg-slate-800 text-slate-300'}`;
                }
                if (marker) marker.textContent = data.marker || "PROVIDER_NEUTRAL_VERIFICATION_RECEIPT_OK";
                if (classification) classification.textContent = latest ? `${latest.classification} / ${latest.merge_eligible ? "eligible" : "ineligible"}` : "no_receipts";
                if (artifact) artifact.textContent = latest ? `${(latest.candidate_sha || "").slice(0, 12)} / tree ${(latest.tree_sha || "").slice(0, 12)}` : "—";
                if (proofClasses) {
                    const scopes = latest?.proof_scope_status || {};
                    proofClasses.textContent = latest
                        ? Object.entries(scopes).map(([name, state]) => `${name}:${state.status}`).join(", ") || "none"
                        : "—";
                }
                if (verifier) {
                    const isolation = latest?.verifier_isolation || {};
                    verifier.textContent = latest
                        ? `${latest.verifier_id || "unknown"} / ${latest.backend_class || "unknown"}; independent=${isolation.independent === true}; network=${isolation.network_isolated === true}; filesystem=${isolation.filesystem_isolated === true}`
                        : "—";
                }
                if (freshness) freshness.textContent = latest ? `expires ${latest.expires_at || "unknown"}; revocation ${latest.revocation_status || "unknown"}` : "—";
                if (hostedSignal) {
                    const signals = latest?.hosted_signals || [];
                    hostedSignal.textContent = signals.length
                        ? signals.map(item => `OPTIONAL ${item.provider}: ${item.status}${item.reason_code ? ` (${item.reason_code})` : ""}`).join("; ")
                        : "Optional / none required";
                }
                if (proof) {
                    proof.textContent = latest ? JSON.stringify({
                        receipt_id: latest.receipt_id,
                        receipt_sha256: latest.receipt_sha256,
                        policy_sha256: latest.policy_sha256,
                        base_sha: latest.base_sha,
                        base_tree_sha: latest.base_tree_sha,
                        candidate_sha: latest.candidate_sha,
                        tree_sha: latest.tree_sha,
                        canonical_repository_root: latest.canonical_repository_root,
                        checkout_clean_state: latest.checkout_clean_state,
                        changed_path_containment: latest.changed_path_containment,
                        verifier_isolation: latest.verifier_isolation,
                        proof_scope_status: latest.proof_scope_status,
                        repository_id: latest.repository_id,
                        source_kind: latest.source_kind,
                        source_provider: latest.source_provider,
                        source_locator: latest.source_locator,
                        clean_checkout_id: latest.clean_checkout_id,
                        source_acquisition_digest: latest.source_acquisition_digest,
                        environment_digest: latest.environment_digest,
                        changed_paths: latest.changed_paths,
                        commands: latest.commands_and_exit_states,
                        logs: latest.logs_and_digests,
                        artifacts: latest.artifacts_and_digests,
                        decision_reason: latest.decision_reason,
                        non_claims: latest.non_claims,
                        acceptance_authority: data.acceptance_authority,
                        hosted_signals_required: data.hosted_signals_required,
                    }, null, 2) : "No native receipts persisted yet";
                }
            } catch (err) {
                console.error("Error loading provider-neutral verification receipts:", err);
                if (badge) {
                    badge.textContent = "Unavailable";
                    badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border border-rose-500/20 bg-rose-500/10 text-rose-300";
                }
                if (proof) proof.textContent = String(err.message || err);
            }
        }

        async function fetchPromotionDecisionLedger() {
            const badge = document.getElementById("promotion-ledger-badge");
            const decisionEl = document.getElementById("promotion-ledger-decision");
            const verificationEl = document.getElementById("promotion-ledger-verification");
            const stateEl = document.getElementById("promotion-ledger-state");
            const sideEffectsEl = document.getElementById("promotion-ledger-side-effects");
            try {
                const res = await fetch(`${API_PREFIX}/agy/promotion-decisions/latest`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const record = data.promotion_decision;
                const sideEffects = data.side_effects || record?.side_effects || {};
                const hasRecord = Boolean(record);
                const ready = record?.status === "decision_ready";
                if (badge) {
                    badge.textContent = hasRecord ? (ready ? "Decision Ready" : "Manual Review") : "No Decision";
                    badge.className = `px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${hasRecord ? (ready ? 'border-cyan-500/20 bg-cyan-500/10 text-cyan-300' : 'border-amber-500/20 bg-amber-500/10 text-amber-300') : 'border-slate-700 bg-slate-800 text-slate-300'}`;
                }
                if (decisionEl) decisionEl.textContent = record?.recommendation || "no_decision";
                if (verificationEl) verificationEl.textContent = record ? `${record.verification_gate || "unknown"} · ${record.verification_lane || "unknown"}` : "—";
                if (stateEl) stateEl.textContent = record ? `${record.marker}: ${record.promotion_decision_id}` : "No promotion ledger rows";
                if (sideEffectsEl) {
                    const unsafe = sideEffects.linear_comment_posted || sideEffects.github_pr_created || sideEffects.auto_merge_enabled || sideEffects.bulk_agent_dispatch;
                    sideEffectsEl.textContent = unsafe ? "unsafe side effect detected" : "no real Linear/GitHub/auto-merge side effects";
                }
            } catch (err) {
                console.error("Error loading promotion decision ledger:", err);
                if (badge) {
                    badge.textContent = "Unavailable";
                    badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border border-rose-500/20 bg-rose-500/10 text-rose-300";
                }
                if (stateEl) stateEl.textContent = String(err.message || err);
            }
        }

        async function fetchOperatorActionApproval() {
            const badge = document.getElementById("operator-approval-badge");
            const decisionEl = document.getElementById("operator-approval-decision");
            const actionEl = document.getElementById("operator-approval-action");
            const policyEl = document.getElementById("operator-approval-policy");
            const previewEl = document.getElementById("operator-approval-preview");
            const sideEffectsEl = document.getElementById("operator-approval-side-effects");
            try {
                const res = await fetch(`${API_PREFIX}/agy/operator-action-approvals/latest`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const approval = data.operator_action_approval;
                const sideEffects = data.side_effects || approval?.side_effects || {};
                const hasRecord = Boolean(approval);
                const approved = approval?.operator_decision === "approve" && approval?.policy_gate === "pass";
                const rejected = approval?.operator_decision === "reject";
                const deferred = approval?.operator_decision === "defer";
                if (badge) {
                    badge.textContent = hasRecord ? (approved ? "Approval Ready" : (rejected ? "Rejected" : (deferred ? "Deferred" : "Manual Review"))) : "No Approval";
                    badge.className = `px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${hasRecord ? (approved ? 'border-sky-500/20 bg-sky-500/10 text-sky-300' : 'border-amber-500/20 bg-amber-500/10 text-amber-300') : 'border-slate-700 bg-slate-800 text-slate-300'}`;
                }
                if (decisionEl) decisionEl.textContent = approval?.operator_decision || "no_approval";
                if (actionEl) actionEl.textContent = approval?.requested_action || "—";
                if (policyEl) policyEl.textContent = approval?.policy_gate || "—";
                if (previewEl) previewEl.textContent = approval ? `${approval.marker}: ${approval.execution_preview?.summary || "dry-run-only preview"}` : "No approval rows";
                if (sideEffectsEl) {
                    const unsafe = sideEffects.linear_comment_posted || sideEffects.github_pr_created || sideEffects.auto_merge_enabled || sideEffects.bulk_agent_dispatch;
                    sideEffectsEl.textContent = unsafe ? "unsafe side effect detected" : "no real Linear/GitHub/auto-merge side effects";
                }
            } catch (err) {
                console.error("Error loading operator action approval:", err);
                if (badge) {
                    badge.textContent = "Unavailable";
                    badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border border-rose-500/20 bg-rose-500/10 text-rose-300";
                }
                if (previewEl) previewEl.textContent = String(err.message || err);
            }
        }

        async function fetchApprovedActionExecutor() {
            const badge = document.getElementById("approved-executor-badge");
            const actionEl = document.getElementById("approved-executor-action");
            const modeEl = document.getElementById("approved-executor-mode");
            const guardEl = document.getElementById("approved-executor-final-guard");
            const statusEl = document.getElementById("approved-executor-status");
            const commandEl = document.getElementById("approved-executor-command");
            const sideEffectsEl = document.getElementById("approved-executor-side-effects");
            try {
                const res = await fetch(`${API_PREFIX}/agy/approved-action-executors/latest`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const executor = data.approved_action_executor;
                const sideEffects = data.side_effects || executor?.side_effects || {};
                const hasRecord = Boolean(executor);
                const dryReady = executor?.execution_status === "dry_run_ready";
                const finalBlocked = executor?.execution_status === "blocked_final_authorization_required";
                const blocked = executor?.execution_status?.startsWith("blocked");
                if (badge) {
                    badge.textContent = hasRecord ? (dryReady ? "Dry Run Ready" : (finalBlocked ? "Final Auth Required" : (blocked ? "Blocked" : "No Request"))) : "No Request";
                    badge.className = `px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${hasRecord ? (dryReady ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300' : 'border-amber-500/20 bg-amber-500/10 text-amber-300') : 'border-slate-700 bg-slate-800 text-slate-300'}`;
                }
                if (actionEl) actionEl.textContent = executor?.requested_action || "—";
                if (modeEl) modeEl.textContent = executor?.executor_mode || "—";
                if (guardEl) guardEl.textContent = executor ? (executor.final_authorization_present ? "authorization present" : "authorization required / not present") : "—";
                if (statusEl) statusEl.textContent = executor?.execution_status || "no_request";
                if (commandEl) commandEl.textContent = executor ? `${executor.marker}: ${executor.command_preview?.summary || "DRY_RUN_ONLY: command preview"}` : "No executor request rows";
                if (sideEffectsEl) {
                    const unsafe = sideEffects.linear_comment_posted || sideEffects.github_pr_created || sideEffects.auto_merge_enabled || sideEffects.production_deployed || sideEffects.real_executor_invoked;
                    sideEffectsEl.textContent = unsafe ? "unsafe side effect detected" : "no real Linear/GitHub/auto-merge side effects";
                }
            } catch (err) {
                console.error("Error loading approved action executor:", err);
                if (badge) {
                    badge.textContent = "Unavailable";
                    badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border border-rose-500/20 bg-rose-500/10 text-rose-300";
                }
                if (commandEl) commandEl.textContent = String(err.message || err);
            }
        }

        async function fetchFinalActionAuthorization() {
            const badge = document.getElementById("final-auth-badge");
            const decisionEl = document.getElementById("final-auth-decision");
            const actionEl = document.getElementById("final-auth-action");
            const tokenEl = document.getElementById("final-auth-token");
            const envEl = document.getElementById("final-auth-env");
            const guardEl = document.getElementById("final-auth-guard");
            const eligibilityEl = document.getElementById("final-auth-eligibility");
            const sideEffectsEl = document.getElementById("final-auth-side-effects");
            try {
                const res = await fetch(`${API_PREFIX}/agy/final-action-authorizations/latest`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const authorization = data.final_action_authorization;
                const sideEffects = data.side_effects || authorization?.side_effects || {};
                const hasRecord = Boolean(authorization);
                const guard = authorization?.final_guard_state;
                const decision = authorization?.authorization_decision;
                if (badge) {
                    const label = hasRecord ? (guard === "eligible_not_executed" ? "Eligible Not Executed" : (decision === "reject" ? "Rejected" : (decision === "defer" ? "Deferred" : "Blocked By Default"))) : "No Authorization";
                    badge.textContent = label;
                    badge.className = `px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${guard === 'eligible_not_executed' ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300' : hasRecord ? 'border-amber-500/20 bg-amber-500/10 text-amber-300' : 'border-slate-700 bg-slate-800 text-slate-300'}`;
                }
                if (decisionEl) decisionEl.textContent = authorization?.authorization_decision || "—";
                if (actionEl) actionEl.textContent = authorization?.requested_action || "—";
                if (tokenEl) tokenEl.textContent = authorization ? (authorization.authorization_token_present ? "present" : "not present") : "—";
                if (envEl) envEl.textContent = authorization ? (authorization.real_execution_env_present ? "enabled" : "not enabled") : "—";
                if (guardEl) guardEl.textContent = authorization?.final_guard_state || "—";
                if (eligibilityEl) {
                    eligibilityEl.textContent = authorization ? `${authorization.marker}: ${authorization.execution_eligibility?.reason || "final guard blocked by default"}` : "No final authorization rows";
                }
                if (sideEffectsEl) {
                    const unsafe = sideEffects.linear_comment_posted || sideEffects.github_pr_created || sideEffects.auto_merge_enabled || sideEffects.production_deployed || sideEffects.real_executor_invoked;
                    sideEffectsEl.textContent = unsafe ? "unsafe side effect detected" : "no real Linear/GitHub/auto-merge side effects";
                }
            } catch (err) {
                console.error("Error loading final action authorization:", err);
                if (badge) {
                    badge.textContent = "Unavailable";
                    badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border border-rose-500/20 bg-rose-500/10 text-rose-300";
                }
                if (eligibilityEl) eligibilityEl.textContent = String(err.message || err);
            }
        }

        async function fetchQuarantinedExecutionAdapter() {
            const badge = document.getElementById("quarantined-adapter-badge");
            const modeEl = document.getElementById("quarantined-adapter-mode");
            const actionEl = document.getElementById("quarantined-adapter-action");
            const egressEl = document.getElementById("quarantined-adapter-egress");
            const envelopeEl = document.getElementById("quarantined-adapter-envelope");
            const auditEl = document.getElementById("quarantined-adapter-audit");
            const sideEffectsEl = document.getElementById("quarantined-adapter-side-effects");
            const summaryEl = document.getElementById("quarantined-adapter-summary");
            try {
                const res = await fetch(`${API_PREFIX}/agy/quarantined-execution-adapters/latest`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const adapter = data.quarantined_execution_adapter;
                const sideEffects = data.side_effects || adapter?.side_effects || {};
                const hasRecord = Boolean(adapter);
                const state = adapter?.adapter_state;
                if (badge) {
                    const label = hasRecord ? (state === "sealed_preview_ready" ? "Sealed Preview Ready" : (state === "manual_review" ? "Manual Review" : "Blocked By Final Guard")) : "No Adapter";
                    badge.textContent = label;
                    badge.className = `px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${state === 'sealed_preview_ready' ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300' : hasRecord ? 'border-amber-500/20 bg-amber-500/10 text-amber-300' : 'border-slate-700 bg-slate-800 text-slate-300'}`;
                }
                if (modeEl) modeEl.textContent = adapter?.adapter_mode || "—";
                if (actionEl) actionEl.textContent = adapter?.requested_action || "—";
                if (egressEl) egressEl.textContent = adapter?.egress_policy?.policy || "—";
                if (envelopeEl) {
                    envelopeEl.textContent = adapter ? `${String(adapter.command_envelope_sha256 || "").slice(0, 16)}… ${adapter.command_envelope?.schema || "sealed command envelope"}` : "—";
                }
                if (auditEl) {
                    auditEl.textContent = adapter ? `${adapter.audit_packet?.audit_packet_id || "adapter-audit"} / ${adapter.audit_packet?.marker || adapter.marker}` : "—";
                }
                if (sideEffectsEl) {
                    const unsafe = sideEffects.linear_comment_posted || sideEffects.github_pr_created || sideEffects.auto_merge_enabled || sideEffects.production_deployed || sideEffects.real_executor_invoked || sideEffects.executed;
                    sideEffectsEl.textContent = unsafe ? "unsafe side effect detected" : "no real Linear/GitHub/auto-merge side effects";
                }
                if (summaryEl) {
                    summaryEl.textContent = adapter ? `${adapter.marker}: ${adapter.adapter_reason || "deny-all adapter preview"}` : "No quarantined execution adapter rows";
                }
            } catch (err) {
                console.error("Error loading quarantined execution adapter:", err);
                if (badge) {
                    badge.textContent = "Unavailable";
                    badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border border-rose-500/20 bg-rose-500/10 text-rose-300";
                }
                if (summaryEl) summaryEl.textContent = String(err.message || err);
            }
        }

        async function fetchSandboxedExecutionCanary() {
            const badge = document.getElementById("sandbox-canary-badge");
            const modeEl = document.getElementById("sandbox-canary-mode");
            const actionEl = document.getElementById("sandbox-canary-action");
            const envelopeEl = document.getElementById("sandbox-canary-envelope");
            const policyEl = document.getElementById("sandbox-canary-policy");
            const transcriptEl = document.getElementById("sandbox-canary-transcript");
            const sideEffectsEl = document.getElementById("sandbox-canary-side-effects");
            const summaryEl = document.getElementById("sandbox-canary-summary");
            try {
                const res = await fetch(`${API_PREFIX}/agy/sandboxed-execution-canaries/latest`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const canary = data.sandboxed_execution_canary;
                const sideEffects = data.side_effects || canary?.side_effects || {};
                const hasRecord = Boolean(canary);
                const state = canary?.sandbox_state;
                if (badge) {
                    const label = hasRecord ? (state === "noop_canary_ready" ? "No-op Canary Ready" : (state === "manual_review" ? "Manual Review" : "Blocked By Final Guard")) : "No Canary";
                    badge.textContent = label;
                    badge.className = `px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${state === 'noop_canary_ready' ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300' : hasRecord ? 'border-amber-500/20 bg-amber-500/10 text-amber-300' : 'border-slate-700 bg-slate-800 text-slate-300'}`;
                }
                if (modeEl) modeEl.textContent = canary?.canary_mode || "—";
                if (actionEl) actionEl.textContent = canary?.requested_action || "—";
                if (envelopeEl) {
                    envelopeEl.textContent = canary ? `${canary.command_envelope_verified ? 'hash verified' : 'mismatch blocked'} · ${String(canary.command_envelope_sha256 || "").slice(0, 16)}…` : "—";
                }
                if (policyEl) {
                    policyEl.textContent = canary?.sandbox_policy?.policy || "—";
                }
                if (transcriptEl) {
                    transcriptEl.textContent = canary ? `${canary.sandbox_transcript?.sandbox_transcript_id || "sandbox-transcript"} / ${canary.sandbox_transcript?.summary || "no-op transcript"}` : "—";
                }
                if (sideEffectsEl) {
                    const unsafe = sideEffects.linear_comment_posted || sideEffects.github_pr_created || sideEffects.auto_merge_enabled || sideEffects.production_deployed || sideEffects.real_executor_invoked || sideEffects.executed;
                    sideEffectsEl.textContent = unsafe ? "unsafe side effect detected" : "no real Linear/GitHub/auto-merge side effects";
                }
                if (summaryEl) {
                    summaryEl.textContent = canary ? `${canary.marker}: ${canary.sandbox_reason || "no-op sandbox canary"}` : "No sandboxed execution canary rows";
                }
            } catch (err) {
                console.error("Error loading sandboxed execution canary:", err);
                if (badge) {
                    badge.textContent = "Unavailable";
                    badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border border-rose-500/20 bg-rose-500/10 text-rose-300";
                }
                if (summaryEl) summaryEl.textContent = String(err.message || err);
            }
        }

        async function fetchRealExecutorArmingGate() {
            const badge = document.getElementById("real-executor-arming-badge");
            const modeEl = document.getElementById("real-executor-arming-mode");
            const actionEl = document.getElementById("real-executor-arming-action");
            const tokenEl = document.getElementById("real-executor-arming-token");
            const envEl = document.getElementById("real-executor-arming-env");
            const missingEl = document.getElementById("real-executor-arming-missing");
            const satisfiedEl = document.getElementById("real-executor-arming-satisfied");
            const summaryEl = document.getElementById("real-executor-arming-summary");
            if (!badge || !modeEl || !actionEl || !tokenEl || !envEl || !missingEl || !satisfiedEl || !summaryEl) return;
            try {
                const response = await fetch("/api/agy/real-executor-arming-gates/latest");
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();
                const gate = data.real_executor_arming_gate;
                if (!gate) {
                    badge.textContent = "No Arming Gate";
                    summaryEl.textContent = "No real executor arming gate recorded yet";
                    return;
                }
                const state = gate.arming_state || "unknown";
                badge.textContent = state === "blocked_missing_real_authorization" ? "Blocked Missing Authorization" : state.replace(/_/g, " ");
                modeEl.textContent = gate.arming_mode || "—";
                actionEl.textContent = gate.requested_action || "—";
                tokenEl.textContent = gate.operator_token_present ? "present" : "not present";
                envEl.textContent = gate.real_executor_env_present ? "enabled" : "not enabled";
                const missing = (gate.missing_prerequisites || []).map((item) => item.key).slice(0, 6).join(", ");
                missingEl.textContent = missing || "none represented";
                const satisfied = (gate.satisfied_prerequisites || []).map((item) => item.key).slice(0, 6).join(", ");
                satisfiedEl.textContent = satisfied || "none represented";
                summaryEl.textContent = `${gate.marker}: ${gate.arming_reason || "readiness-only gate"}; no real Linear/GitHub/auto-merge side effects`;
            } catch (err) {
                badge.textContent = "Error";
                summaryEl.textContent = `Unable to load real executor arming gate: ${err.message}`;
            }
        }

        async function fetchRawAgentOutputQueue() {
            const badge = document.getElementById("raw-output-badge");
            const latestEl = document.getElementById("raw-output-latest");
            const setText = (id, value) => {
                const el = document.getElementById(id);
                if (el) el.textContent = String(value ?? 0);
            };
            try {
                const res = await fetch(`${API_PREFIX}/agents/raw-output?limit=1`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const counts = data.counts || {};
                setText("raw-output-accepted", counts.accepted);
                setText("raw-output-normalized", counts.normalized);
                setText("raw-output-rejected", counts.rejected);
                setText("raw-output-repairable", counts.repairable);
                setText("raw-output-rerun-required", counts.rerun_required);
                setText("raw-output-policy-violation", counts.policy_violation);
                const latest = (data.raw_outputs || [])[0];
                if (latestEl) latestEl.textContent = latest ? `${latest.raw_output_id} · ${latest.normalization_status} · ${latest.repair_hint || "no_hint"}` : "No persisted raw output rows";
                if (badge) {
                    const rejected = counts.rejected || 0;
                    badge.textContent = data.count > 0 ? (rejected ? "Needs Review" : "Clean") : "No Rows";
                    badge.className = `px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${data.count > 0 ? (rejected ? 'border-amber-500/20 bg-amber-500/10 text-amber-300' : 'border-fuchsia-500/20 bg-fuchsia-500/10 text-fuchsia-300') : 'border-slate-700 bg-slate-800 text-slate-300'}`;
                }
            } catch (err) {
                console.error("Error loading raw agent output queue:", err);
                if (badge) {
                    badge.textContent = "Unavailable";
                    badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border border-rose-500/20 bg-rose-500/10 text-rose-300";
                }
                if (latestEl) latestEl.textContent = String(err?.message || err || "Gateway unavailable");
            }
        }

        async function fetchMergeBacklog() {
            const badge = document.getElementById("merge-backlog-badge");
            const action = document.getElementById("merge-backlog-action");
            const verification = document.getElementById("merge-backlog-verification");
            const branch = document.getElementById("merge-backlog-branch");
            const autoMerge = document.getElementById("merge-backlog-auto-merge");
            try {
                const res = await fetch(`${API_PREFIX}/agy/merge-backlog?limit=1`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const item = (data.merge_backlog || [])[0];
                const count = data.count ?? 0;
                const pass = item?.verification_gate === "pass";
                if (badge) {
                    badge.textContent = count > 0 ? (pass ? "Gate Pass" : "Review") : "No Rows";
                    badge.className = `px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${count > 0 ? (pass ? 'border-cyan-500/20 bg-cyan-500/10 text-cyan-300' : 'border-amber-500/20 bg-amber-500/10 text-amber-300') : 'border-slate-700 bg-slate-800 text-slate-300'}`;
                }
                window.prompt5MergeCandidateId = item?.completed_work_id || null;
                if (action) action.textContent = item?.recommended_action || "no_rows";
                if (verification) verification.textContent = item ? `${item.verification_gate} · ${item.verification_lane}` : "no_rows";
                if (branch) branch.textContent = item?.pr_branch || "No row planned yet";
                if (autoMerge) autoMerge.textContent = item?.eligible_for_auto_merge ? "ERROR: enabled" : "Disabled";
            } catch (err) {
                console.error("Error loading AGY merge backlog:", err);
                if (badge) {
                    badge.textContent = "Unavailable";
                    badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border border-rose-500/20 bg-rose-500/10 text-rose-300";
                }
                if (action) action.textContent = "unavailable";
                if (verification) verification.textContent = "blocked";
                if (branch) branch.textContent = String(err?.message || err || "Gateway unavailable");
                if (autoMerge) autoMerge.textContent = "Disabled";
            }
        }


        async function stagePrCandidate() {
            const status = document.getElementById("pr-candidate-status");
            const output = document.getElementById("pr-candidate-output");
            const button = document.getElementById("pr-candidate-action");
            const completedWorkId = window.prompt5MergeCandidateId;
            if (!completedWorkId) {
                if (status) status.textContent = "No completed-work row is available to stage.";
                return;
            }
            if (button) button.disabled = true;
            try {
                const res = await fetch(`${API_PREFIX}/agy/merge-backlog/${encodeURIComponent(completedWorkId)}/pr-candidate`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ requested_by: "dashboard", action: "stage_pr_candidate" })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
                if (status) status.textContent = `${data.lifecycle_state || data.status} · ${data.marker || "PROMPT5_PR_CANDIDATE_LIFECYCLE_OK"}`;
                if (output) {
                    output.classList.remove("hidden");
                    output.textContent = JSON.stringify({
                        candidate: data.candidate,
                        side_effects: data.side_effects,
                        non_claims: data.non_claims,
                    }, null, 2);
                }
            } catch (err) {
                console.error("Error staging PR candidate metadata:", err);
                if (status) status.textContent = String(err?.message || err || "Gateway unavailable");
            } finally {
                if (button) button.disabled = false;
            }
        }

        async function stagePrDryRun() {
            const status = document.getElementById("pr-candidate-status");
            const output = document.getElementById("pr-candidate-output");
            const button = document.getElementById("pr-dry-run-action");
            const completedWorkId = window.prompt5MergeCandidateId;
            if (!completedWorkId) {
                if (status) status.textContent = "No completed-work row is available for PR dry-run planning.";
                return;
            }
            if (button) button.disabled = true;
            try {
                const res = await fetch(`${API_PREFIX}/agy/merge-backlog/${encodeURIComponent(completedWorkId)}/pr-dry-run`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ requested_by: "dashboard", action: "operator_pr_creation_dry_run", linear_writeback: true })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
                if (status) status.textContent = `${data.status} · ${data.marker || "PROMPT5_OPERATOR_PR_DRY_RUN_OK"}`;
                if (output) {
                    output.classList.remove("hidden");
                    output.textContent = JSON.stringify({
                        branch_plan: data.branch_plan,
                        github_pr_plan: data.github_pr_plan,
                        verification_gate_selection: data.verification_gate_selection,
                        linear_writeback: data.linear_writeback,
                        side_effects: data.side_effects,
                        non_claims: data.non_claims,
                    }, null, 2);
                }
            } catch (err) {
                console.error("Error planning PR dry run:", err);
                if (status) status.textContent = String(err?.message || err || "Gateway unavailable");
            } finally {
                if (button) button.disabled = false;
            }
        }


        async function stagePrApprovalGate() {
            const status = document.getElementById("pr-candidate-status");
            const output = document.getElementById("pr-candidate-output");
            const button = document.getElementById("pr-approval-action");
            const completedWorkId = window.prompt5MergeCandidateId;
            if (!completedWorkId) {
                if (status) status.textContent = "No completed-work row is available for PR approval gating.";
                return;
            }
            if (button) button.disabled = true;
            const token = `APPROVE_REAL_PR:${completedWorkId}`;
            try {
                const res = await fetch(`${API_PREFIX}/agy/merge-backlog/${encodeURIComponent(completedWorkId)}/pr-approval`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        requested_by: "dashboard",
                        approved_by: "dashboard-operator",
                        approval_token: token,
                        approval_note: "Dashboard Prompt 5.4 explicit approval gate",
                        action: "record_real_pr_creation_approval",
                        expose_real_pr_action: true
                    })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
                if (status) status.textContent = `${data.status} · ${data.marker || "PROMPT5_REAL_PR_APPROVAL_GATE_OK"}`;
                if (output) {
                    output.classList.remove("hidden");
                    output.textContent = JSON.stringify({
                        approval_record: data.approval_record,
                        policy_gate: data.policy_gate,
                        real_pr_creation_action: data.real_pr_creation_action,
                        side_effects: data.side_effects,
                        non_claims: data.non_claims,
                    }, null, 2);
                }
            } catch (err) {
                console.error("Error recording PR approval gate:", err);
                if (status) status.textContent = String(err?.message || err || "Gateway unavailable");
            } finally {
                if (button) button.disabled = false;
            }
        }

        async function stageApprovedPrExecutor() {
            const status = document.getElementById("pr-candidate-status");
            const output = document.getElementById("pr-candidate-output");
            const button = document.getElementById("pr-executor-action");
            const completedWorkId = window.prompt5MergeCandidateId;
            if (!completedWorkId) {
                if (status) status.textContent = "No completed-work row is available for approved PR executor planning.";
                return;
            }
            if (button) button.disabled = true;
            const token = `APPROVE_REAL_PR:${completedWorkId}`;
            try {
                const approvalRes = await fetch(`${API_PREFIX}/agy/merge-backlog/${encodeURIComponent(completedWorkId)}/pr-approval`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        requested_by: "dashboard",
                        approved_by: "dashboard-operator",
                        approval_token: token,
                        approval_note: "Dashboard Prompt 5.5 executor preflight approval gate",
                        action: "record_real_pr_creation_approval",
                        expose_real_pr_action: true
                    })
                });
                const approval = await approvalRes.json();
                if (!approvalRes.ok) throw new Error(approval?.detail || `HTTP ${approvalRes.status}`);
                const res = await fetch(`${API_PREFIX}/agy/merge-backlog/${encodeURIComponent(completedWorkId)}/pr-executor`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        requested_by: "dashboard",
                        approved_by: "dashboard-operator",
                        approval_token: token,
                        approval_id: approval?.approval_record?.approval_id,
                        final_operator_trigger: true,
                        execute: false,
                        executor_mode: "dry_run",
                        allow_real_side_effects: false
                    })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
                if (status) status.textContent = `${data.status} · ${data.marker || "PROMPT5_APPROVED_REAL_PR_EXECUTOR_OK"}`;
                if (output) {
                    output.classList.remove("hidden");
                    output.textContent = JSON.stringify({
                        executor_mode: data.executor_mode,
                        commands_rendered: data.commands_rendered,
                        commands_executed: data.commands_executed,
                        policy_gate: data.policy_gate,
                        executor_plan: data.executor_plan,
                        executor_result: data.executor_result,
                        audit_writeback: data.audit_writeback,
                        executor_run_id: data.executor_run_id,
                        side_effects: data.side_effects,
                        non_claims: data.non_claims,
                    }, null, 2);
                }
            } catch (err) {
                console.error("Error planning approved PR executor:", err);
                if (status) status.textContent = String(err?.message || err || "Gateway unavailable");
            } finally {
                if (button) button.disabled = false;
            }
        }

        async function runPrompt6ExecutorCanaryDryRun() {
            const status = document.getElementById("pr-candidate-status");
            const output = document.getElementById("pr-candidate-output");
            const button = document.getElementById("prompt6-executor-canary-action");
            if (button) button.disabled = true;
            try {
                const res = await fetch(`${API_PREFIX}/agy/executor-runs/canary-dry-run`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        requested_by: "dashboard",
                        executor_mode: "dry_run",
                        execute: false,
                        allow_real_side_effects: false
                    })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
                const history = await fetch(`${API_PREFIX}/agy/executor-runs?limit=5`);
                const recent = history.ok ? await history.json() : { runs: data.recent_runs || [] };
                if (status) status.textContent = `${data.status} · ${data.marker || "PROMPT6_EXECUTOR_AUDIT_CANARY_OK"} · run_id ${data.run_id || "n/a"}`;
                if (output) {
                    output.classList.remove("hidden");
                    output.textContent = JSON.stringify({
                        prompt6_executor_audit_canary: true,
                        run_id: data.run_id,
                        status: data.status,
                        marker: data.marker,
                        executor_mode: data.executor_mode,
                        commands_rendered: data.commands_rendered,
                        commands_executed: data.commands_executed,
                        side_effects: {
                            real_github_pr_created: data.real_github_pr_created,
                            git_branch_created: data.git_branch_created,
                            auto_merge_enabled: data.auto_merge_enabled,
                            production_deployed: data.production_deployed,
                            AGY_dispatch: data.AGY_dispatch
                        },
                        non_claims: data.non_claims,
                        blocked_reasons: data.blocked_reasons,
                        recent_executor_run_history: recent.runs || data.recent_runs || []
                    }, null, 2);
                }
            } catch (err) {
                console.error("Error running Prompt 6 executor canary dry run:", err);
                if (status) status.textContent = String(err?.message || err || "Gateway unavailable");
            } finally {
                if (button) button.disabled = false;
            }
        }

        async function fetchOvernightGuard() {
            const badge = document.getElementById("overnight-guard-badge");
            const marker = document.getElementById("overnight-guard-marker");
            const state = document.getElementById("overnight-guard-state");
            const evidence = document.getElementById("overnight-guard-evidence");
            const safety = document.getElementById("overnight-guard-safety");
            const tasks = document.getElementById("overnight-guard-tasks");
            const blockers = document.getElementById("overnight-guard-blockers");
            const next = document.getElementById("overnight-guard-next");
            try {
                const res = await fetch(`${API_PREFIX}/agy/overnight-guard`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const guard = data.guard || {};
                const policy = guard.policy || {};
                const issues = (guard.blockers || []).concat(guard.warnings || []);
                const isReady = guard.readiness_state === "ready" && issues.length === 0;
                const isPaused = guard.readiness_state === "paused";
                if (badge) {
                    badge.textContent = isReady ? "Ready" : (isPaused ? "Paused" : "Blocked");
                    badge.className = `px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${isReady ? 'border-violet-500/20 bg-violet-500/10 text-violet-300' : (isPaused ? 'border-amber-500/20 bg-amber-500/10 text-amber-300' : 'border-rose-500/20 bg-rose-500/10 text-rose-300')}`;
                }
                if (marker) marker.textContent = data.marker || "AGY_OVERNIGHT_READINESS_GUARD_OK";
                if (state) state.textContent = guard.readiness_state || "unknown";
                const issueSummary = issues.join(" · ");
                if (evidence) evidence.textContent = isReady ? "complete retained proof" : (issueSummary || "not ready");
                if (safety) safety.textContent = `auto-merge=${policy.auto_merge_enabled === true} · deploy=${policy.production_deploy_enabled === true} · PR=${policy.real_github_pr_create_enabled === true}`;
                if (tasks) tasks.textContent = String(data.tasks_launched ?? 0);
                if (blockers) blockers.textContent = issues.length ? `${guard.latest_one_task_success_marker || "no marker"} · ${issueSummary}` : (guard.latest_one_task_success_marker || "AGY one-task proof present");
                if (next) next.textContent = guard.next_safe_action || "No overnight run active";
            } catch (err) {
                console.error("Error loading AGY overnight guard:", err);
                if (badge) {
                    badge.textContent = "Unavailable";
                    badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border border-rose-500/20 bg-rose-500/10 text-rose-300";
                }
                if (state) state.textContent = "unavailable";
                if (evidence) evidence.textContent = "API unavailable";
                if (safety) safety.textContent = "auto-merge=false · deploy=false · PR=false";
                if (tasks) tasks.textContent = "0";
                if (blockers) blockers.textContent = String(err?.message || err || "Gateway unavailable");
                if (next) next.textContent = "Restore guard API before any overnight run";
            }
        }

        function renderMorningBriefingError(reason) {
            const timestampEl = document.getElementById("briefing-timestamp");
            if (timestampEl) timestampEl.textContent = reason;

            const fallbackBadge = document.getElementById("briefing-fallback-badge");
            if (fallbackBadge) {
                fallbackBadge.textContent = (reason.includes("404") || reason.includes("unavailable")) ? "Report Unavailable" : "Report Error";
                fallbackBadge.className = "px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider border border-rose-500/20 bg-rose-500/10 text-rose-300";
            }

            const kpis = ["briefing-total-runs", "briefing-completed", "briefing-failed", "briefing-success-rate", "briefing-credits"];
            kpis.forEach(id => {
                const el = document.getElementById(id);
                if (el) el.textContent = "—";
            });

            const escContainer = document.getElementById("briefing-escalations");
            if (escContainer) escContainer.innerHTML = `<div class="text-rose-400/80 italic">${reason}</div>`;

            const commitsContainer = document.getElementById("briefing-commits");
            if (commitsContainer) commitsContainer.innerHTML = `<div class="text-rose-400/80 italic">${reason}</div>`;

            const runsTbody = document.getElementById("briefing-runs-tbody");
            if (runsTbody) runsTbody.innerHTML = `<tr><td colspan="3" class="py-4 text-center text-rose-400/80 italic">${reason}</td></tr>`;
        }

        async function fetchMorningBriefing() {
            try {
                const res = await fetch("/api/gateway/overnight-report/latest");
                if (res.ok) {
                    const data = await res.json();
                    
                    // Update timestamp & badges
                    document.getElementById("briefing-timestamp").textContent = `Generated: ${data.generated_at ? data.generated_at.substring(0, 16).replace('T', ' ') : 'N/A'} UTC`;
                    
                    const fallbackBadge = document.getElementById("briefing-fallback-badge");
                    if (data.period && data.period.fallback_used) {
                        fallbackBadge.textContent = "Fallback Lookback Mode";
                        fallbackBadge.className = "px-2 py-0.5 rounded-full text-[9px] font-bold uppercase tracking-wider border border-amber-500/20 bg-amber-500/10 text-amber-300";
                    } else {
                        fallbackBadge.classList.add("hidden");
                    }
                    
                    // Update KPIs
                    const s = data.summary || {};
                    document.getElementById("briefing-total-runs").textContent = s.total_dispatches ?? 0;
                    document.getElementById("briefing-completed").textContent = s.completed ?? 0;
                    document.getElementById("briefing-failed").textContent = s.failed ?? 0;
                    document.getElementById("briefing-success-rate").textContent = `${s.success_rate ?? 100}%`;
                    document.getElementById("briefing-credits").textContent = s.total_credits_spent ?? 0;
                    
                    // Update Escalations
                    const escContainer = document.getElementById("briefing-escalations");
                    if (data.escalations && data.escalations.length > 0) {
                        escContainer.innerHTML = data.escalations.map(esc => `
                            <div class="p-2 rounded border border-rose-500/20 bg-rose-500/5 text-rose-300">
                                <div class="flex items-center justify-between font-semibold">
                                    <span>${esc.issue_id} (${esc.lane})</span>
                                    <span class="text-[9px] text-slate-500 font-mono">${esc.time ? esc.time.substring(11, 16) : ''}</span>
                                </div>
                                <div class="text-[10px] text-slate-400 mt-0.5">${esc.reason}</div>
                            </div>
                        `).join("");
                    } else {
                        escContainer.innerHTML = `<div class="text-slate-500 italic">No escalations occurred.</div>`;
                    }
                    
                    // Update Commits
                    const commitsContainer = document.getElementById("briefing-commits");
                    if (data.git_commits && data.git_commits.length > 0) {
                        commitsContainer.innerHTML = data.git_commits.map(c => `
                            <div class="flex items-center space-x-1.5 font-mono text-[10px] text-slate-400 py-0.5">
                                <span class="text-indigo-400 font-bold">${c.sha}</span>
                                <span class="text-slate-300 truncate" style="max-width: 250px;">${c.message}</span>
                            </div>
                        `).join("");
                    } else {
                        commitsContainer.innerHTML = `<div class="text-slate-500 italic">No recent commits.</div>`;
                    }
                    
                    // Update Runs table
                    const runsTbody = document.getElementById("briefing-runs-tbody");
                    if (data.runs && data.runs.length > 0) {
                        runsTbody.innerHTML = data.runs.slice(0, 10).map(r => {
                            const statusClass = (r.status === 'completed' || r.exit_code === 0) ? 'text-emerald-400' : (r.status === 'failed' || (r.exit_code !== null && r.exit_code !== 0)) ? 'text-rose-400' : 'text-amber-400';
                            return `
                                <tr class="border-b border-slate-800/40 hover:bg-slate-900/10">
                                    <td class="py-1.5 px-2 text-slate-300 font-semibold font-mono">${r.agent}</td>
                                    <td class="py-1.5 px-2 text-slate-400 font-mono">${r.issue_id}</td>
                                    <td class="py-1.5 px-2 font-bold ${statusClass}">${r.status}</td>
                                </tr>
                            `;
                        }).join("");
                    } else {
                        runsTbody.innerHTML = `<tr><td colspan="3" class="py-4 text-center text-slate-500 italic">No runs recorded.</td></tr>`;
                    }
                } else {
                    renderMorningBriefingError(res.status === 404 ? "Report unavailable (404)" : `Report error (${res.status})`);
                }
            } catch (err) {
                console.error("Error loading morning briefing:", err);
                renderMorningBriefingError("Network error loading report");
            }
        }

        async function fetchBudgetCaps() {
            const status = document.getElementById("budget-caps-status");
            try {
                const res = await fetch(`${API_PREFIX}/quota/caps`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const caps = await res.json();
                const daily = document.getElementById("budget-daily-limit");
                const autoPause = document.getElementById("budget-auto-pause");
                if (daily) daily.value = Number(caps.daily_limit ?? 15).toFixed(2);
                if (autoPause) autoPause.value = String(caps.auto_pause !== false);
                if (status) status.textContent = `Loaded · ${caps.auto_pause !== false ? 'auto-pause on' : 'auto-pause off'}`;
            } catch (err) {
                console.error("Error loading budget caps:", err);
                if (status) status.textContent = "Budget caps unavailable";
            }
        }

        async function saveBudgetCaps() {
            const status = document.getElementById("budget-caps-status");
            const daily = document.getElementById("budget-daily-limit");
            const autoPause = document.getElementById("budget-auto-pause");
            const payload = {
                daily_limit: Number(daily?.value || 0),
                per_model: {},
                auto_pause: autoPause?.value !== "false"
            };
            try {
                const res = await fetch(`${API_PREFIX}/quota/caps`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const caps = await res.json();
                if (status) status.textContent = `Saved · daily ${Number(caps.daily_limit).toFixed(2)}`;
                showToast("Budget caps saved");
            } catch (err) {
                console.error("Error saving budget caps:", err);
                if (status) status.textContent = "Save failed";
                showToast("Budget cap save failed", true);
            }
        }

        async function fetchJulesCapacity() {
            const setText = (id, value) => {
                const el = document.getElementById(id);
                if (el) el.textContent = value;
            };
            const badge = document.getElementById("jules-capacity-badge");
            const progress = document.getElementById("jules-capacity-progress");
            try {
                const res = await fetch(`${API_PREFIX}/jules/capacity`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                const limit = Number(data.limit || 300);
                const observed = Number(data.observed_launches || 0);
                const pct = data.status === "unavailable" ? 0 : Math.min(100, Math.max(0, (observed / limit) * 100));
                setText("jules-capacity-observed", data.status === "unavailable" ? "—" : observed);
                setText("jules-capacity-limit", limit);
                setText("jules-capacity-remaining", data.status === "unavailable" ? "—" : (data.remaining_observed_capacity ?? "—"));
                setText("jules-capacity-active", data.active ?? "—");
                setText("jules-capacity-awaiting", data.awaiting ?? "—");
                setText("jules-capacity-completed", data.completed ?? "—");
                setText("jules-capacity-failed", data.failed ?? "—");
                const coverage = data.status === "unavailable" ? "Unavailable" : String(data.coverage_state || "partial_coverage").replaceAll("_", " ");
                setText("jules-capacity-coverage", coverage);
                const snapshot = data.snapshot_at ? formatDate(data.snapshot_at) : "unknown";
                const label = data.status === "unavailable" ? "Unavailable" : (data.status === "fresh" ? "Fresh" : data.status === "stale" ? "Stale" : "Partial coverage");
                setText("jules-capacity-freshness", `${label} · snapshot ${snapshot} · source ${data.source || 'jules-capacity-ledger'} · aggregate counts only`);
                setText("jules-capacity-nonclaims", (data.non_claims || []).length ? `Non-claims: ${(data.non_claims || []).join(', ')}` : "Full-day ledger coverage observed for this UTC window.");
                if (progress) progress.style.width = `${pct}%`;
                if (badge) {
                    badge.textContent = label;
                    badge.className = "px-2 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider " + (data.status === "unavailable" ? "bg-rose-500/10 text-rose-300 border border-rose-500/20" : data.status === "fresh" ? "bg-emerald-500/10 text-emerald-300 border border-emerald-500/20" : "bg-amber-500/10 text-amber-300 border border-amber-500/20");
                }
            } catch (err) {
                console.error("Error loading Jules capacity:", err);
                setText("jules-capacity-freshness", "Unavailable · Jules capacity endpoint failed");
                setText("jules-capacity-coverage", "Unavailable");
                if (progress) progress.style.width = "0%";
                if (badge) {
                    badge.textContent = "Unavailable";
                    badge.className = "px-2 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider bg-rose-500/10 text-rose-300 border border-rose-500/20";
                }
            }
        }

        async function fetchQuotaData() {
            try {
                const quotaRes = await fetch(`${API_PREFIX}/quota`);
                if (quotaRes.ok) {
                    const data = await quotaRes.json();
                    
                    const syncStatus = document.getElementById("quota-sync-status");
                    const syncAge = document.getElementById("quota-sync-age");
                    if (syncStatus) {
                        const snapshotAt = data.snapshot_at ? formatDate(data.snapshot_at) : "unknown";
                        syncStatus.textContent = `Snapshot captured ${snapshotAt}`;
                    }
                    if (syncAge) {
                        syncAge.textContent = data.snapshot_age_sec !== null && data.snapshot_age_sec !== undefined ? formatAge(data.snapshot_age_sec) : "Freshness: unknown";
                    }
                    
                    // Render Cards
                    const grid = document.getElementById("quota-cards-grid");
                    const current = data.current || [];
                    if (current.length === 0) {
                        grid.innerHTML = `<div class="col-span-3 text-center text-slate-500 italic py-6">No active region quotas snapshot ledger details found.</div>`;
                    } else {
                        grid.innerHTML = current.map(item => {
                            const pct = Number(item.remaining_pct);
                            const hasPct = !Number.isNaN(pct);
                            const effectivePct = hasPct ? pct : 0;
                            const barColor = effectivePct < 20 ? 'bg-red-500' : effectivePct < 50 ? 'bg-amber-500' : 'bg-emerald-500';
                            const labelColor = effectivePct < 20 ? 'text-red-400' : effectivePct < 50 ? 'text-amber-400' : 'text-emerald-400';
                            const status = quotaStatusLabel(item);
                            return `
                                <div class="glass-panel p-4 rounded-xl space-y-3 flex flex-col justify-between">
                                    <div class="flex justify-between items-start gap-2">
                                        <h3 class="font-bold text-xs uppercase text-slate-400">${item.display_name || item.model}</h3>
                                        <span class="text-xs font-bold font-mono ${hasPct ? labelColor : 'text-slate-400'}">${formatQuotaPct(item.remaining_pct)} remaining</span>
                                    </div>
                                    <div class="w-full bg-slate-950 h-2 rounded-full overflow-hidden">
                                        <div class="h-full ${barColor}" style="width: ${effectivePct}%"></div>
                                    </div>
                                    <div class="flex justify-between items-center text-[10px] text-slate-500 gap-2">
                                        <span>Reset in ${formatDate(item.reset_time)}</span>
                                        <span class="font-mono">${status}</span>
                                    </div>
                                </div>
                            `;
                        }).join("");
                    }
                    
                    // Render History Events
                    const eventsTbody = document.getElementById("quota-events-tbody");
                    const events = data.recent_events || [];
                    if (events.length === 0) {
                        eventsTbody.innerHTML = `<tr><td colspan="3" class="py-4 text-center text-slate-500 italic">No quota breaches reported within the last 24 hours.</td></tr>`;
                    } else {
                        eventsTbody.innerHTML = events.map(ev => {
                            const details = ev.details || ev.event_type || 'No details available';
                            return `
                            <tr class="border-b border-slate-800/40 hover:bg-slate-900/10 transition">
                                <td class="py-2.5 px-3 text-slate-500 font-mono">${formatDate(ev.timestamp)}</td>
                                <td class="py-2.5 px-3 font-semibold text-slate-200">${ev.model || 'Unknown model'}</td>
                                <td class="py-2.5 px-3 text-red-400 font-medium">${details}</td>
                            </tr>
                        `;
                        }).join("");
                    }
                }
            } catch (err) {
                console.error("Error loading quota data:", err);
            }
        }

        function taskAdmissionValue(id) {
            const element = document.getElementById(id);
            return element ? element.value.trim() : "";
        }

        function boundedAdmissionError(payload, status) {
            const code = payload && typeof payload.error === "string" ? payload.error : `http_${status}`;
            return /^[a-z0-9_]{1,64}$/.test(code) ? code : "bounded_request_failed";
        }

        function taskAdmissionHeaders(token, idempotencyKey, includeJson = false) {
            const headers = { "Authorization": `Bearer ${token}` };
            if (includeJson) {
                headers["Content-Type"] = "application/json";
                headers["Idempotency-Key"] = idempotencyKey;
            }
            return headers;
        }

        function renderTaskAdmissionProof(record, replayed) {
            const proof = document.getElementById("admission-proof");
            if (!proof) return;
            const compact = {
                task_id: record.task_id,
                status: record.status,
                replayed: Boolean(replayed),
                base_commit: String(record.base_commit || "").slice(0, 12),
                base_tree: String(record.base_tree || "").slice(0, 12),
                task_file_sha256: String(record.task_file_sha256 || "").slice(0, 16),
                producer_identity: record.producer_identity,
                writer_cap: record.writer_cap,
                worktree: record.worktree,
                actor: record.actor,
                event_id: record.event_id,
                received_at: record.received_at,
                launch_performed: false
            };
            proof.textContent = JSON.stringify(compact, null, 2);
            proof.classList.remove("hidden");
        }

        async function fetchTaskAdmissionHistory(token) {
            const response = await fetch(`/api/dashboard/task-admissions?limit=10`, {
                headers: taskAdmissionHeaders(token, "")
            });
            let payload = {};
            try { payload = await response.json(); } catch (_) {}
            if (!response.ok) throw new Error(boundedAdmissionError(payload, response.status));
            const history = document.getElementById("admission-history");
            if (!history) return;
            const records = Array.isArray(payload.records) ? payload.records : [];
            history.textContent = records.length
                ? records.map((record) => `${record.task_id} · ${record.status} · ${record.producer_identity} · ${record.received_at}`).join(" | ")
                : "No durable task admissions recorded.";
        }

        async function loadTaskAdmissions() {
            const tokenInput = document.getElementById("admission-token");
            const status = document.getElementById("admission-status");
            const token = tokenInput ? tokenInput.value : "";
            if (!token) {
                if (status) status.textContent = "Operator bearer required for protected readback.";
                return;
            }
            try {
                await fetchTaskAdmissionHistory(token);
                if (status) status.textContent = "Durable admission readback refreshed.";
            } catch (error) {
                if (status) status.textContent = `Readback failed: ${error.message}`;
            } finally {
                if (tokenInput) tokenInput.value = "";
            }
        }

        async function submitTaskAdmission() {
            const tokenInput = document.getElementById("admission-token");
            const status = document.getElementById("admission-status");
            const button = document.getElementById("admission-submit");
            const token = tokenInput ? tokenInput.value : "";
            const idempotencyKey = taskAdmissionValue("admission-idempotency");
            if (!token) {
                if (status) status.textContent = "Operator bearer required.";
                return;
            }
            const payload = {
                version: 1,
                task_id: taskAdmissionValue("admission-task-id"),
                base_commit: taskAdmissionValue("admission-base-commit"),
                base_tree: taskAdmissionValue("admission-base-tree"),
                task_file: taskAdmissionValue("admission-task-file"),
                task_file_sha256: taskAdmissionValue("admission-task-sha"),
                producer_identity: taskAdmissionValue("admission-producer"),
                worktree: taskAdmissionValue("admission-worktree"),
                writer_cap: 1,
                idempotency_key: idempotencyKey,
                created_at: taskAdmissionValue("admission-created-at"),
                status: "admitted"
            };
            if (button) button.disabled = true;
            if (status) status.textContent = "Recording durable intent…";
            try {
                const response = await fetch(`/api/dashboard/task-admissions`, {
                    method: "POST",
                    headers: taskAdmissionHeaders(token, idempotencyKey, true),
                    body: JSON.stringify(payload)
                });
                let result = {};
                try { result = await response.json(); } catch (_) {}
                if (!response.ok) throw new Error(boundedAdmissionError(result, response.status));
                renderTaskAdmissionProof(result.record || {}, result.replayed);
                await fetchTaskAdmissionHistory(token);
                if (status) status.textContent = result.replayed
                    ? "Exact admission replay confirmed; no duplicate event created."
                    : "Durable admission recorded. Producer was not launched.";
            } catch (error) {
                if (status) status.textContent = `Admission failed: ${error.message}`;
            } finally {
                if (tokenInput) tokenInput.value = "";
                if (button) button.disabled = false;
            }
        }

        let activeSettingsCategoryFilter = "all";
        let latestCredentialsPayload = {};

        function filterSettingsCards(category) {
            activeSettingsCategoryFilter = category;
            document.querySelectorAll(".settings-filter-btn").forEach(btn => {
                const isMatch = btn.getAttribute("data-settings-filter") === category;
                btn.className = `settings-filter-btn px-3 py-1.5 rounded-lg text-xs uppercase border transition ${isMatch ? "bg-indigo-600/20 text-indigo-300 border-indigo-500/30 font-bold" : "bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-200"}`;
            });
            renderSettingsCards();
        }

        async function initiateOAuthFlow(service) {
            showToast(`Initiating OAuth for ${service}...`);
            try {
                const res = await fetch(`/api/gateway/oauth/${encodeURIComponent(service)}/initiate`, { method: "POST" });
                const data = await res.json();
                if (!res.ok || data.ok === false) throw new Error(data.error || "OAuth initiation failed");
                showToast(`OAuth Connected: ${data.message || 'Verification complete'}`);
                await loadSettingsCredentials();
            } catch (err) {
                console.error("OAuth error:", err);
                showToast(`OAuth Failed: ${err.message || err}`, true);
            }
        }

        async function testCredentialKey(key) {
            const input = document.getElementById(`setting-${key}`);
            const value = input?.value || "";
            showToast(`Testing connectivity for ${key}...`);
            try {
                const res = await fetch("/api/gateway/credentials/test", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ key, value })
                });
                const data = await res.json();
                if (!res.ok || data.ok === false) throw new Error(data.error || "Test failed");
                showToast(data.message || "Credential verified successfully!");
            } catch (err) {
                console.error("Test error:", err);
                showToast(`Test failed: ${err.message || err}`, true);
            }
        }

        function openAddServiceModal() {
            const modal = document.getElementById("add-service-modal");
            if (modal) modal.classList.remove("hidden");
        }

        function closeAddServiceModal() {
            const modal = document.getElementById("add-service-modal");
            if (modal) modal.classList.add("hidden");
        }

        function applyServicePreset(preset) {
            const presets = {
                github: { name: "GitHub (OAuth / Personal Access Token)", category: "oauth", authtype: "oauth2", key: "GITHUB_TOKEN" },
                gitlab: { name: "GitLab (Self-Hosted / Cloud PAT)", category: "oauth", authtype: "pat", key: "GITLAB_TOKEN" },
                bitbucket: { name: "Bitbucket Cloud / Server", category: "oauth", authtype: "pat", key: "BITBUCKET_TOKEN" },
                linear: { name: "Linear Task Manager", category: "workspace", authtype: "api_key", key: "LINEAR_API_KEY" },
                jira: { name: "Atlassian Jira API", category: "workspace", authtype: "api_key", key: "JIRA_API_TOKEN" },
                asana: { name: "Asana Workspace PAT", category: "workspace", authtype: "pat", key: "ASANA_PAT" },
                trello: { name: "Trello Board API Key", category: "workspace", authtype: "api_key", key: "TRELLO_API_KEY" },
                clickup: { name: "ClickUp Workspace API", category: "workspace", authtype: "api_key", key: "CLICKUP_API_KEY" },
                hermes: { name: "Hermes Orchestrator Hub", category: "infra", authtype: "endpoint", key: "HERMES_ENDPOINT" },
                agy: { name: "Antigravity (AGY CLI)", category: "oauth", authtype: "oauth2", key: "AGY_MODEL" },
                jules: { name: "Jules CLI Capacity Ledger", category: "oauth", authtype: "oauth2", key: "JULES_DB_PATH" },
                devin: { name: "Devin AI Harness", category: "ai", authtype: "api_key", key: "DEVIN_API_KEY" },
                openai: { name: "OpenAI API (GPT-4o)", category: "ai", authtype: "api_key", key: "OPENAI_API_KEY" },
                anthropic: { name: "Anthropic Claude API", category: "ai", authtype: "api_key", key: "ANTHROPIC_API_KEY" },
                gemini: { name: "Google Gemini API", category: "ai", authtype: "api_key", key: "GEMINI_API_KEY" },
                deepseek: { name: "DeepSeek AI API", category: "ai", authtype: "api_key", key: "DEEPSEEK_API_KEY" },
                cloudflare: { name: "Cloudflare API Token", category: "infra", authtype: "pat", key: "CLOUDFLARE_API_TOKEN" },
                custom: { name: "Custom Integration", category: "workspace", authtype: "api_key", key: "CUSTOM_SERVICE_KEY" }
            };

            const cfg = presets[preset] || presets.custom;
            const nameEl = document.getElementById("new-service-name");
            const catEl = document.getElementById("new-service-category");
            const authEl = document.getElementById("new-service-authtype");
            const keyEl = document.getElementById("new-service-key");

            if (nameEl) nameEl.value = cfg.name;
            if (catEl) catEl.value = cfg.category;
            if (authEl) authEl.value = cfg.authtype;
            if (keyEl) keyEl.value = cfg.key;
        }

        async function submitAddService(event) {
            event.preventDefault();
            const name = document.getElementById("new-service-name")?.value || "Custom Service";
            const category = document.getElementById("new-service-category")?.value || "workspace";
            const authType = document.getElementById("new-service-authtype")?.value || "api_key";
            const key = document.getElementById("new-service-key")?.value || "";
            const value = document.getElementById("new-service-value")?.value || "";

            showToast(`Saving integration service ${name}...`);
            try {
                const res = await fetch("/api/gateway/services/add", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name, category, auth_type: authType, key, value })
                });
                const data = await res.json();
                if (!res.ok || data.ok === false) throw new Error(data.error || "Save service failed");
                showToast(`Service "${name}" added successfully!`);
                closeAddServiceModal();
                await loadSettingsCredentials();
            } catch (err) {
                console.error("Add service error:", err);
                showToast(`Failed to add service: ${err.message || err}`, true);
            }
        }

        async function removeCustomService(serviceId, name) {
            if (!confirm(`Are you sure you want to remove the integration service "${name || serviceId}"?`)) return;

            showToast(`Removing service ${serviceId}...`);
            try {
                const res = await fetch(`/api/gateway/services/${encodeURIComponent(serviceId)}`, { method: "DELETE" });
                const data = await res.json();
                if (!res.ok || data.ok === false) throw new Error(data.error || "Delete failed");
                showToast(`Service "${name || serviceId}" removed.`);
                await loadSettingsCredentials();
            } catch (err) {
                console.error("Remove service error:", err);
                showToast(`Failed to remove service: ${err.message || err}`, true);
            }
        }

        function renderSettingsCards() {
            const grid = document.getElementById("settings-service-cards-grid");
            if (!grid) return;

            const cards = [
                {
                    key: "google_antigravity",
                    name: "Google Antigravity OAuth",
                    icon: "⚡",
                    category: "oauth",
                    type: "oauth2",
                    fulfilled: Boolean(latestCredentialsPayload.oauth_services?.google_antigravity?.fulfilled),
                    details: latestCredentialsPayload.oauth_services?.google_antigravity?.details || "Google OAuth 2.0 Credentials (~/.gemini/antigravity)",
                    actionLabel: latestCredentialsPayload.oauth_services?.google_antigravity?.action_label || "Connect Google OAuth"
                },
                {
                    key: "jules_cli",
                    name: "Jules CLI OAuth",
                    icon: "🚀",
                    category: "oauth",
                    type: "oauth2",
                    fulfilled: Boolean(latestCredentialsPayload.oauth_services?.jules_cli?.fulfilled),
                    details: latestCredentialsPayload.oauth_services?.jules_cli?.details || "Jules Capacity Ledger & Daily Quota Store",
                    actionLabel: latestCredentialsPayload.oauth_services?.jules_cli?.action_label || "Authorize Jules OAuth"
                },
                {
                    key: "github_oauth",
                    name: "GitHub OAuth & CLI Token",
                    icon: "🐙",
                    category: "oauth",
                    type: "oauth2",
                    fulfilled: Boolean(latestCredentialsPayload.oauth_services?.github_oauth?.fulfilled || latestCredentialsPayload.credentials?.GITHUB_TOKEN?.configured),
                    details: latestCredentialsPayload.oauth_services?.github_oauth?.details || "GitHub OAuth & CLI Token (~/.config/gh)",
                    actionLabel: latestCredentialsPayload.oauth_services?.github_oauth?.action_label || "Connect GitHub OAuth"
                },
                {
                    key: "OPENAI_API_KEY",
                    name: "OpenAI (George Reviewer)",
                    icon: "🤖",
                    category: "ai",
                    type: "api_key",
                    fulfilled: Boolean(latestCredentialsPayload.credentials?.OPENAI_API_KEY?.configured),
                    details: latestCredentialsPayload.credentials?.OPENAI_API_KEY?.redacted_value || "GPT-4o Peer Reviewer Engine"
                },
                {
                    key: "ANTHROPIC_API_KEY",
                    name: "Anthropic Claude (Kai & Fred)",
                    icon: "🧠",
                    category: "ai",
                    type: "api_key",
                    fulfilled: Boolean(latestCredentialsPayload.credentials?.ANTHROPIC_API_KEY?.configured),
                    details: latestCredentialsPayload.credentials?.ANTHROPIC_API_KEY?.redacted_value || "Claude 3.7 Sonnet Execution Engine"
                },
                {
                    key: "GEMINI_API_KEY",
                    name: "Google Gemini API Key",
                    icon: "✨",
                    category: "ai",
                    type: "api_key",
                    fulfilled: Boolean(latestCredentialsPayload.credentials?.GEMINI_API_KEY?.configured),
                    details: latestCredentialsPayload.credentials?.GEMINI_API_KEY?.redacted_value || "Google Gemini 2.5 Pro Engine"
                },
                {
                    key: "HERMES_ENDPOINT",
                    name: "Hermes Orchestrator Hub",
                    icon: "🌐",
                    category: "infra",
                    type: "endpoint",
                    fulfilled: Boolean(latestCredentialsPayload.credentials?.HERMES_ENDPOINT?.configured),
                    details: latestCredentialsPayload.credentials?.HERMES_ENDPOINT?.redacted_value || "http://100.83.32.92:9000"
                },
                {
                    key: "LINEAR_API_KEY",
                    name: "Linear API Key",
                    icon: "📐",
                    category: "workspace",
                    type: "api_key",
                    fulfilled: Boolean(latestCredentialsPayload.credentials?.LINEAR_API_KEY?.configured),
                    details: latestCredentialsPayload.credentials?.LINEAR_API_KEY?.redacted_value || "Task Backlog & Swarm Sync"
                },
                {
                    key: "CLOUDFLARE_API_TOKEN",
                    name: "Cloudflare API Token",
                    icon: "☁️",
                    category: "infra",
                    type: "token",
                    fulfilled: Boolean(latestCredentialsPayload.credentials?.CLOUDFLARE_API_TOKEN?.configured),
                    details: latestCredentialsPayload.credentials?.CLOUDFLARE_API_TOKEN?.redacted_value || "DNS & Tunnels Governance"
                }
            ];

            // Merge user-added custom services
            const customServices = latestCredentialsPayload.custom_services || [];
            customServices.forEach(cs => {
                cards.push({
                    key: cs.service_id,
                    name: cs.name,
                    icon: cs.icon || "🔌",
                    category: cs.category || "workspace",
                    type: cs.auth_type || "api_key",
                    fulfilled: Boolean(cs.fulfilled),
                    details: cs.details || "Custom Service",
                    removable: true,
                    serviceId: cs.service_id
                });
            });

            const filtered = activeSettingsCategoryFilter === "all"
                ? cards
                : cards.filter(c => c.category === activeSettingsCategoryFilter);

            grid.innerHTML = filtered.map(c => {
                const isFulfilled = c.fulfilled;
                const cardClass = isFulfilled ? "service-card-fulfilled" : "";
                const badge = isFulfilled
                    ? `<span class="px-2 py-0.5 rounded text-[9px] font-bold uppercase bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">🟢 Connected / Fulfilled</span>`
                    : `<span class="px-2 py-0.5 rounded text-[9px] font-bold uppercase bg-slate-800 text-slate-400 border border-slate-700">⚪ Not Configured</span>`;

                const removeBtn = c.removable
                    ? `<button type="button" onclick="removeCustomService('${c.serviceId}', '${escapeHtml(c.name)}')" class="text-xs text-rose-400 hover:text-rose-300 font-bold ml-2" title="Remove Service">&times;</button>`
                    : "";

                const actionBtns = c.category === "oauth"
                    ? `<button type="button" onclick="initiateOAuthFlow('${c.key}')" class="px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 text-slate-200 hover:text-white hover:border-indigo-500 text-xs font-semibold flex items-center gap-1 transition shadow-sm">${escapeHtml(c.actionLabel || 'Connect OAuth')}</button>`
                    : `<button type="button" onclick="testCredentialKey('${c.key}')" class="px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 text-slate-300 hover:text-white hover:border-cyan-500 text-xs font-semibold transition">Test Ping</button>`;

                return `
                    <div class="service-integration-card p-4 rounded-xl space-y-3 flex flex-col justify-between ${cardClass}">
                        <div class="space-y-1.5">
                            <div class="flex items-center justify-between">
                                <div class="flex items-center gap-2">
                                    <span class="text-base">${c.icon}</span>
                                    <h4 class="service-card-title text-xs font-bold text-slate-200 uppercase tracking-wider">${escapeHtml(c.name)}</h4>
                                    ${removeBtn}
                                </div>
                                ${badge}
                            </div>
                            <p class="service-card-desc text-[11px] text-slate-400 font-mono break-all">${escapeHtml(c.details)}</p>
                        </div>
                        <div class="pt-2 border-t border-slate-800/60 flex items-center justify-between">
                            <span class="text-[10px] text-slate-500 uppercase tracking-wider font-bold">${c.type}</span>
                            ${actionBtns}
                        </div>
                    </div>
                `;
            }).join('');
        }

        async function loadSettingsCredentials() {
            try {
                const res = await fetch("/api/gateway/credentials/status");
                if (res.ok) {
                    latestCredentialsPayload = await res.json();
                    renderSettingsCards();
                }
            } catch (err) {
                console.error("Error loading credentials status:", err);
            }
        }

        async function saveSettingsCredentials() {
            const keys = ["GOOGLE_SA_JSON", "GA4_ACCOUNT_ID", "GTM_ACCOUNT_ID", "GSC_VERIFICATION_TOKEN", "CLOUDFLARE_API_TOKEN", "VERCEL_TOKEN", "GITHUB_TOKEN", "LINEAR_API_KEY", "HERMES_ENDPOINT", "AGY_MODEL", "JULES_DB_PATH", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"];
            const payload = {};
            keys.forEach(k => {
                const el = document.getElementById(`setting-${k}`);
                if (el && el.value) payload[k] = el.value.trim();
            });

            try {
                const res = await fetch("/api/gateway/credentials/update", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok || data.ok === false) throw new Error(data.error || "Save failed");
                showToast("Successfully updated and persisted service credentials!");
                await loadSettingsCredentials();
            } catch (err) {
                console.error("Save credentials failed:", err);
                showToast(`Save failed: ${err.message || err}`, true);
            }
        }

        // WebSockets
        function connectWS() {
            let ws;
            try {
                const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
                const wsUrl = protocol + "//" + window.location.host + "/ws";
                ws = new WebSocket(wsUrl);
            } catch (e) {
                setTimeout(connectWS, 3000);
                return;
            }

            ws.onopen = () => {
                const dot = document.getElementById("status-dot");
                const text = document.getElementById("status-text");
                dot.className = "w-2.5 h-2.5 rounded-full bg-emerald-500 status-pulse";
                text.className = "text-[10px] sm:text-xs font-semibold text-emerald-400 uppercase tracking-wider";
                text.textContent = "Connected";
                fetchData();
            };

            ws.onmessage = (e) => {
                try {
                    const event = JSON.parse(e.data);
                    if (["webhook_queued", "webhook_processing", "webhook_completed", "webhook_failed", "dispatcher_cycle"].includes(event.type) || (event.type && event.type.startsWith("review_factory_"))) {
                        fetchData();
                        if (activeTab === "review-factory") {
                            loadReviewFactory();
                        }
                    }
                    if (event.type === "swarmlock_status" && event.payload) {
                        renderSwarmLockCockpit(event.payload);
                        const drawer = document.getElementById("swarmlock-history-drawer");
                        if (drawer && !drawer.classList.contains("hidden")) {
                            fetchSwarmLockHistory();
                        }
                    }
                    if (event.type === "signal.emitted" || event.type === "signal") {
                        if (activeTab === "signals") {
                            renderSignalsView();
                        }
                        const sig = event.payload || event.signal || {};
                        addLocalSignal(sig.agent || event.type, sig.message || event.message || "Signal received", sig.severity || "info");
                    } else {
                        // Append event to signals console log
                        addLocalSignal(event.type.replace("webhook_", "").toUpperCase(), event.message || JSON.stringify(event), "info");
                    }
                } catch (err) {}
            };

            ws.onclose = (event) => {
                const dot = document.getElementById("status-dot");
                const text = document.getElementById("status-text");
                if (event.code === 1008) {
                    dot.className = "w-2.5 h-2.5 rounded-full bg-amber-500";
                    text.className = "text-[10px] sm:text-xs font-semibold text-amber-400 uppercase tracking-wider";
                    text.textContent = "Auth Required";
                    return;
                }
                dot.className = "w-2.5 h-2.5 rounded-full bg-red-500 status-pulse";
                text.className = "text-[10px] sm:text-xs font-semibold text-slate-400 uppercase tracking-wider";
                text.textContent = "Connecting";
                setTimeout(connectWS, 3000);
            };
        }

        // ==========================================
        // SwarmProof Truth Oracle & Verification UI
        // ==========================================

        let swarmProofReceiptsData = [];

        async function renderSwarmProofView() {
            try {
                // Fetch stats and receipts in parallel
                const [statusRes, receiptsRes] = await Promise.all([
                    fetch('/api/swarmproof/status').catch(() => null),
                    fetch('/api/swarmproof/receipts').catch(() => null),
                ]);

                if (statusRes && statusRes.ok) {
                    const statusData = await statusRes.json();
                    const invEl = document.getElementById('sp-metric-invariants');
                    if (invEl) invEl.textContent = `${statusData.active_invariants || 10} / 10 Active`;
                    
                    const ledgersEl = document.getElementById('sp-metric-ledgers');
                    if (ledgersEl) ledgersEl.textContent = statusData.total_receipts || '0';
                    
                    const redGreenEl = document.getElementById('sp-metric-red-green');
                    if (redGreenEl) redGreenEl.textContent = statusData.red_green_traces || '0';
                    
                    const deflEl = document.getElementById('sp-metric-deflections');
                    if (deflEl) deflEl.textContent = statusData.deflections_blocked || '0';
                }

                if (receiptsRes && receiptsRes.ok) {
                    const receiptsData = await receiptsRes.json();
                    swarmProofReceiptsData = Array.isArray(receiptsData) ? receiptsData : (receiptsData.receipts || []);
                    renderSwarmProofReceiptsTable(swarmProofReceiptsData);
                } else {
                    renderSwarmProofReceiptsTable([]);
                }
            } catch (err) {
                console.error("renderSwarmProofView error:", err);
            }
        }

        function filterSwarmProofReceipts() {
            const query = (document.getElementById('sp-search-input')?.value || '').toLowerCase().trim();
            const stage = document.getElementById('sp-stage-filter')?.value || 'ALL';

            const filtered = swarmProofReceiptsData.filter(r => {
                const matchesStage = stage === 'ALL' || r.stage === stage;
                if (!matchesStage) return false;
                if (!query) return true;
                const searchStr = `${r.task_id || ''} ${r.agent_id || ''} ${r.commit_sha || ''} ${r.command || ''}`.toLowerCase();
                return searchStr.includes(query);
            });

            renderSwarmProofReceiptsTable(filtered);
        }

        function renderSwarmProofReceiptsTable(receipts) {
            const tbody = document.getElementById('swarmproof-receipts-tbody');
            const empty = document.getElementById('swarmproof-receipts-empty');
            if (!tbody) return;

            tbody.innerHTML = '';
            if (!receipts || receipts.length === 0) {
                if (empty) empty.classList.remove('hidden');
                return;
            }
            if (empty) empty.classList.add('hidden');

            receipts.forEach((r, idx) => {
                const tr = document.createElement('tr');
                tr.className = 'border-b border-slate-800/40 hover:bg-slate-900/60 transition';

                // Stage badge
                let stageBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700">${escapeHtml(r.stage || 'TEST')}</span>`;
                if (r.stage === 'PRE_REPAIR_RED') {
                    stageBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950/60 text-rose-300 border border-rose-800/60">RED FAIL</span>`;
                } else if (r.stage === 'POST_REPAIR_GREEN') {
                    stageBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950/60 text-emerald-300 border border-emerald-800/60">GREEN PASS</span>`;
                } else if (r.stage === 'SMOKE') {
                    stageBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-cyan-950/60 text-cyan-300 border border-cyan-800/60">SMOKE</span>`;
                }

                // Task ID link
                const taskLink = r.task_id
                    ? `<a href="https://prismatic.growthwebdev.com/tab/tasks?issue=${escapeHtml(r.task_id)}" target="_blank" class="text-indigo-400 hover:underline flex items-center gap-1 font-bold">${escapeHtml(r.task_id)} ${PRISMATIC_THEME.icons.externalLink}</a>`
                    : `<span class="text-slate-500 font-mono">LOCAL</span>`;

                // Status
                const statusBadge = r.passed
                    ? `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-950/60 text-emerald-400 border border-emerald-800/60">PASSED</span>`
                    : `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-rose-950/60 text-rose-400 border border-rose-800/60">FAILED</span>`;

                tr.innerHTML = `
                    <td class="py-2.5 px-3 whitespace-nowrap">${taskLink}</td>
                    <td class="py-2.5 px-3 whitespace-nowrap">${stageBadge}</td>
                    <td class="py-2.5 px-3 whitespace-nowrap">
                        <span class="text-slate-200 font-semibold">${escapeHtml(r.agent_id || 'agy')}</span>
                        <span class="text-[10px] text-slate-500">(${escapeHtml(r.model || 'inherit')})</span>
                    </td>
                    <td class="py-2.5 px-3 max-w-xs truncate" title="${escapeHtml(r.command || '')}">
                        <span class="text-slate-300">${escapeHtml(r.command || 'N/A')}</span>
                    </td>
                    <td class="py-2.5 px-3 whitespace-nowrap">
                        ${r.exit_code === 0 ? '<span class="text-emerald-400 font-bold">0</span>' : '<span class="text-rose-400 font-bold">' + (r.exit_code ?? 'ERR') + '</span>'}
                    </td>
                    <td class="py-2.5 px-3 whitespace-nowrap text-slate-400">
                        ${(r.duration_seconds != null ? r.duration_seconds.toFixed(2) : '0.00')}s
                    </td>
                    <td class="py-2.5 px-3 whitespace-nowrap text-[11px] text-slate-500 font-mono">
                        ${escapeHtml((r.commit_sha || '').substring(0, 7) || 'HEAD')}
                    </td>
                    <td class="py-2.5 px-3 whitespace-nowrap">${statusBadge}</td>
                    <td class="py-2.5 px-3 whitespace-nowrap text-right">
                        <button onclick="openSwarmProofReceiptModal(${idx})" class="px-2 py-1 rounded bg-indigo-950/50 hover:bg-indigo-900 text-indigo-300 border border-indigo-800/50 text-[10px] font-semibold transition inline-flex items-center gap-1">
                            ${PRISMATIC_THEME.icons.inspect} Inspect
                        </button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        }

        // Modals management
        function openSwarmProofVerifyModal() {
            const modal = document.getElementById('swarmproof-verify-modal');
            if (modal) modal.classList.remove('hidden');
        }
        function closeSwarmProofVerifyModal() {
            const modal = document.getElementById('swarmproof-verify-modal');
            if (modal) modal.classList.add('hidden');
        }

        async function executeSwarmProofVerify() {
            const input = document.getElementById('sp-verify-input')?.value.trim();
            const strict = document.getElementById('sp-verify-strict')?.checked ?? true;
            const resBox = document.getElementById('sp-verify-results');
            if (!input || !resBox) return;

            resBox.classList.remove('hidden');
            resBox.innerHTML = '<div class="text-slate-400">Evaluating payload against 10 Anti-Deception Invariants...</div>';

            try {
                let parsed;
                try {
                    parsed = JSON.parse(input);
                } catch(e) {
                    parsed = { text: input };
                }

                const res = await fetch('/api/swarmproof/verify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ payload: parsed, strict: strict })
                });
                const data = await res.json();
                
                if (data.passed) {
                    resBox.className = 'mt-3 p-3 rounded-lg border border-emerald-500/50 bg-emerald-950/30 font-mono text-[11px] space-y-2';
                    resBox.innerHTML = `
                        <div class="flex items-center gap-2 text-emerald-400 font-bold text-xs">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                            VERIFIED SUCCESS: All Active Invariants Passed
                        </div>
                        <div class="text-slate-300 text-[10px]">
                            ${(data.passed_invariants || []).map(inv => `<div class="text-emerald-400">✓ ${escapeHtml(inv)}</div>`).join('')}
                        </div>
                    `;
                } else {
                    resBox.className = 'mt-3 p-3 rounded-lg border border-rose-500/50 bg-rose-950/30 font-mono text-[11px] space-y-2';
                    resBox.innerHTML = `
                        <div class="flex items-center gap-2 text-rose-400 font-bold text-xs">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
                            VERIFICATION REJECTED (Fail-Closed)
                        </div>
                        <div class="space-y-1 text-[10px]">
                            ${(data.violations || []).map(v => `<div class="text-rose-300">✗ [INV-${v.invariant_number || '0'}] ${escapeHtml(v.name)}: ${escapeHtml(v.message)}</div>`).join('')}
                        </div>
                    `;
                }
            } catch (err) {
                resBox.className = 'mt-3 p-3 rounded-lg border border-rose-500/50 bg-rose-950/30 font-mono text-[11px]';
                resBox.innerHTML = `<div class="text-rose-400">Verification request error: ${escapeHtml(err.message)}</div>`;
            }
        }

        function openSwarmProofASTModal() {
            const modal = document.getElementById('swarmproof-ast-modal');
            if (modal) modal.classList.remove('hidden');
        }
        function closeSwarmProofASTModal() {
            const modal = document.getElementById('swarmproof-ast-modal');
            if (modal) modal.classList.add('hidden');
        }

        async function executeSwarmProofASTAnalyze() {
            const baseline = document.getElementById('sp-ast-baseline')?.value;
            const candidate = document.getElementById('sp-ast-candidate')?.value;
            const resBox = document.getElementById('sp-ast-results');
            if (!resBox) return;

            resBox.classList.remove('hidden');
            resBox.innerHTML = '<div class="text-slate-400">Parsing AST structures and computing diff metrics...</div>';

            try {
                const res = await fetch('/api/swarmproof/analyze-ast', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ baseline: baseline || '', candidate: candidate || '' })
                });
                const data = await res.json();
                
                if (data.is_clean) {
                    resBox.className = 'mt-3 p-3 rounded-lg border border-emerald-500/50 bg-emerald-950/30 font-mono text-[11px] space-y-1.5';
                    resBox.innerHTML = `
                        <div class="text-emerald-400 font-bold">✓ CLEAN: No Assertion Degradation or Weakening Detected</div>
                        <div class="text-slate-300 text-[10px]">
                            Assertions: Baseline (${data.baseline_asserts || 0}) → Candidate (${data.candidate_asserts || 0})
                        </div>
                    `;
                } else {
                    resBox.className = 'mt-3 p-3 rounded-lg border border-rose-500/50 bg-rose-950/30 font-mono text-[11px] space-y-1.5';
                    let syntaxExtra = '';
                    if (data.syntax_error) {
                        syntaxExtra = `
                            <div class="mt-2 p-2 rounded bg-slate-900 border border-slate-800 text-[10px] text-slate-300">
                                <div><span class="text-rose-400 font-bold">Line ${data.syntax_error.lineno}:</span> ${escapeHtml(data.syntax_error.text || '')}</div>
                            </div>
                        `;
                    }
                    resBox.innerHTML = `
                        <div class="text-rose-400 font-bold">✗ WEAKENING DETECTED: AST Anti-Weakening Guard Tripped</div>
                        <div class="space-y-1 text-[10px]">
                            ${(data.violations || []).map(v => `<div class="text-rose-300">• ${escapeHtml(v)}</div>`).join('')}
                        </div>
                        ${syntaxExtra}
                    `;
                }
            } catch (err) {
                resBox.className = 'mt-3 p-3 rounded-lg border border-rose-500/50 bg-rose-950/30 font-mono text-[11px]';
                resBox.innerHTML = `<div class="text-rose-400">AST analysis error: ${escapeHtml(err.message)}</div>`;
            }
        }

        function openSwarmProofRunModal() {
            const modal = document.getElementById('swarmproof-run-modal');
            if (modal) modal.classList.remove('hidden');
        }
        function closeSwarmProofRunModal() {
            const modal = document.getElementById('swarmproof-run-modal');
            if (modal) modal.classList.add('hidden');
        }

        async function executeSwarmProofRunTest() {
            const command = document.getElementById('sp-run-command')?.value.trim();
            const stage = document.getElementById('sp-run-stage')?.value || 'POST_REPAIR_GREEN';
            const taskId = document.getElementById('sp-run-task-id')?.value.trim() || 'LOCAL';
            const resBox = document.getElementById('sp-run-results');
            if (!command || !resBox) return;

            resBox.classList.remove('hidden');
            resBox.innerHTML = '<div class="text-slate-400">Executing deterministic test run...</div>';

            try {
                const res = await fetch('/api/swarmproof/run-test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ command: command, stage: stage, task_id: taskId })
                });
                const data = await res.json();
                
                if (!res.ok) {
                    resBox.className = 'mt-3 p-3 rounded-lg border border-rose-500/50 bg-rose-950/30 font-mono text-[11px] space-y-1.5';
                    resBox.innerHTML = `
                        <div class="text-rose-400 font-bold">✗ Execution Rejected: ${escapeHtml(data.detail || 'Error')}</div>
                    `;
                    return;
                }

                if (data.passed) {
                    resBox.className = 'mt-3 p-3 rounded-lg border border-emerald-500/50 bg-emerald-950/30 font-mono text-[11px] space-y-1.5';
                    resBox.innerHTML = `
                        <div class="text-emerald-400 font-bold">✓ TEST PASSED (Exit Code 0)</div>
                        <div class="text-slate-300 text-[10px]">
                            Duration: ${(data.duration_seconds || 0).toFixed(3)}s | Tree SHA: ${escapeHtml(data.tree_sha || 'N/A')}
                        </div>
                    `;
                } else {
                    resBox.className = 'mt-3 p-3 rounded-lg border border-rose-500/50 bg-rose-950/30 font-mono text-[11px] space-y-1.5';
                    resBox.innerHTML = `
                        <div class="text-rose-400 font-bold">✗ TEST FAILED (Exit Code ${data.exit_code})</div>
                        <div class="text-slate-300 text-[10px]">
                            Stage: ${escapeHtml(data.stage || stage)} | Duration: ${(data.duration_seconds || 0).toFixed(3)}s
                        </div>
                    `;
                }
                // Refresh receipts table
                renderSwarmProofView();
            } catch (err) {
                resBox.className = 'mt-3 p-3 rounded-lg border border-rose-500/50 bg-rose-950/30 font-mono text-[11px]';
                resBox.innerHTML = `<div class="text-rose-400">Run test error: ${escapeHtml(err.message)}</div>`;
            }
        }

        function openSwarmProofHooksModal() {
            const modal = document.getElementById('swarmproof-hooks-modal');
            if (modal) modal.classList.remove('hidden');
            checkSwarmProofHookStatus();
        }
        function closeSwarmProofHooksModal() {
            const modal = document.getElementById('swarmproof-hooks-modal');
            if (modal) modal.classList.add('hidden');
        }

        async function checkSwarmProofHookStatus() {
            const statusCommit = document.getElementById('sp-hook-status-commit');
            const statusPush = document.getElementById('sp-hook-status-push');
            try {
                const res = await fetch('/api/swarmproof/status');
                const data = await res.json();
                if (statusCommit) {
                    statusCommit.textContent = data.hooks_installed?.pre_commit ? 'ACTIVE (Protected)' : 'NOT INSTALLED';
                    statusCommit.className = data.hooks_installed?.pre_commit ? 'text-emerald-400 font-bold' : 'text-slate-500';
                }
                if (statusPush) {
                    statusPush.textContent = data.hooks_installed?.pre_push ? 'ACTIVE (Protected)' : 'NOT INSTALLED';
                    statusPush.className = data.hooks_installed?.pre_push ? 'text-emerald-400 font-bold' : 'text-slate-500';
                }
            } catch (e) {
                if (statusCommit) statusCommit.textContent = 'Status Unavailable';
                if (statusPush) statusPush.textContent = 'Status Unavailable';
            }
        }

        async function executeSwarmProofHookAction(action) {
            const fb = document.getElementById('sp-hook-feedback');
            if (fb) {
                fb.classList.remove('hidden');
                fb.innerHTML = `<span class="text-slate-400">${action === 'install' ? 'Installing' : 'Uninstalling'} git hooks...</span>`;
            }
            try {
                const res = await fetch(`/api/swarmproof/hooks/${action}`, { method: 'POST' });
                const data = await res.json();
                if (fb) {
                    fb.innerHTML = data.success
                        ? `<span class="text-emerald-400 font-bold">✓ ${escapeHtml(data.message)}</span>`
                        : `<span class="text-rose-400 font-bold">✗ ${escapeHtml(data.message)}</span>`;
                }
                checkSwarmProofHookStatus();
            } catch (err) {
                if (fb) fb.innerHTML = `<span class="text-rose-400 font-bold">✗ Error: ${escapeHtml(err.message)}</span>`;
            }
        }

        function openSwarmProofReceiptModal(idx) {
            const r = swarmProofReceiptsData[idx];
            if (!r) return;
            const modal = document.getElementById('swarmproof-receipt-modal');
            const title = document.getElementById('sp-modal-title');
            const content = document.getElementById('sp-modal-content');
            if (!modal || !content) return;

            if (title) title.textContent = `Proof Inspection — ${r.task_id || 'Verification Receipt'}`;

            content.innerHTML = `
                <div class="grid grid-cols-2 md:grid-cols-4 gap-2.5 p-3 rounded-lg border border-slate-800 bg-slate-950">
                    <div>
                        <div class="text-[10px] text-slate-500 uppercase font-bold">Task Identifier</div>
                        <div class="text-xs font-bold text-indigo-400">${escapeHtml(r.task_id || 'N/A')}</div>
                    </div>
                    <div>
                        <div class="text-[10px] text-slate-500 uppercase font-bold">Stage</div>
                        <div class="text-xs font-bold text-slate-200">${escapeHtml(r.stage || 'N/A')}</div>
                    </div>
                    <div>
                        <div class="text-[10px] text-slate-500 uppercase font-bold">Exit Code</div>
                        <div class="text-xs font-bold ${r.exit_code === 0 ? 'text-emerald-400' : 'text-rose-400'}">${r.exit_code ?? 'N/A'}</div>
                    </div>
                    <div>
                        <div class="text-[10px] text-slate-500 uppercase font-bold">Duration</div>
                        <div class="text-xs font-bold text-slate-300">${(r.duration_seconds != null ? r.duration_seconds.toFixed(3) : '0.000')}s</div>
                    </div>
                </div>

                <div class="space-y-2 p-3 rounded-lg border border-slate-800 bg-slate-950">
                    <div class="text-[10px] text-slate-500 uppercase font-bold">Cryptographic Fingerprints</div>
                    <div class="text-[11px] space-y-1 font-mono">
                        <div class="text-slate-400">Commit SHA: <span class="text-slate-200">${escapeHtml(r.commit_sha || 'N/A')}</span></div>
                        <div class="text-slate-400">Tree SHA: <span class="text-slate-200">${escapeHtml(r.tree_sha || 'N/A')}</span></div>
                        <div class="text-slate-400">Stdout SHA-256: <span class="text-slate-200">${escapeHtml(r.stdout_sha256 || 'N/A')}</span></div>
                        <div class="text-slate-400">Stderr SHA-256: <span class="text-slate-200">${escapeHtml(r.stderr_sha256 || 'N/A')}</span></div>
                    </div>
                </div>

                <div>
                    <div class="text-[10px] text-slate-500 uppercase font-bold mb-1">Executed Command</div>
                    <pre class="bg-black/60 p-2.5 rounded-lg border border-slate-800 text-[11px] text-slate-300 overflow-x-auto whitespace-pre-wrap">${escapeHtml(r.command || 'N/A')}</pre>
                </div>

                <div>
                    <div class="text-[10px] text-slate-500 uppercase font-bold mb-1">Captured Output Preview</div>
                    <pre class="bg-black/60 p-2.5 rounded-lg border border-slate-800 text-[11px] text-slate-300 overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap">${escapeHtml(r.stdout_preview || r.stderr_preview || 'No stdout/stderr captured.')}</pre>
                </div>
            `;

            modal.classList.remove('hidden');
        }

        function closeSwarmProofReceiptModal() {
            const modal = document.getElementById('swarmproof-receipt-modal');
            if (modal) modal.classList.add('hidden');
        }

        const dashboardTabIds = new Set([
            "dashboard", "telemetry", "merge", "review-factory", "workspaces",
            "skills", "signals", "swarmproof", "pwp", "plugins", "crons", "quota", "foundation", "settings",
        ]);

        function dashboardTabFromURL() {
            const requestedHash = window.location.hash.replace(/^#/, "");
            if (dashboardTabIds.has(requestedHash)) return requestedHash;
            const path = window.location.pathname.replace(/^\//, "").replace(/^tab\//, "");
            if (dashboardTabIds.has(path)) return path;
            return null;
        }

        window.addEventListener("popstate", (e) => {
            const tab = (e.state && e.state.tab) || dashboardTabFromURL() || "dashboard";
            switchTab(tab, null, false);
        });

        document.addEventListener("DOMContentLoaded", () => {
            applyTheme();
            const initialParams = new URLSearchParams(window.location.search);
            const workspaceDeepLink = (
                initialParams.has("file")
                || initialParams.has("workspace_id")
                || initialParams.has("path")
            );
            const initialTab = workspaceDeepLink
                ? "workspaces"
                : (dashboardTabFromURL() || "dashboard");
            if (initialTab !== "dashboard") {
                switchTab(initialTab, null, false);
            } else {
                fetchData();
            }
            loadPluginGovernance();
            loadPWPStatus();
            loadCanonicalAgyActivity();
            loadReviewFactory();
            connectWS();
            // Event-driven WebSocket handles real-time sync.
            // 30-second passive background safety fallback only.
            pollingInterval = setInterval(() => {
                fetchData();
                loadCanonicalAgyActivity();
            }, 30000);
            
            // Close modal on click outside
            window.onclick = function(event) {
                const modal = document.getElementById("modal-container");
                const cronModal = document.getElementById("cron-delete-modal");
                if (event.target === modal) {
                    closeModal();
                }
                if (event.target === cronModal) {
                    closeCronDeleteModal();
                }
                // SwarmProof modals backdrop click
                const spModals = [
                    'swarmproof-verify-modal',
                    'swarmproof-ast-modal',
                    'swarmproof-run-modal',
                    'swarmproof-hooks-modal',
                    'swarmproof-receipt-modal'
                ];
                spModals.forEach(id => {
                    const el = document.getElementById(id);
                    if (el && event.target === el) {
                        el.classList.add('hidden');
                    }
                });
            };

            // Global Escape key dismisses modals
            window.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' || e.key === 'Esc') {
                    closeModal();
                    closeCronDeleteModal();
                    [
                        'swarmproof-verify-modal',
                        'swarmproof-ast-modal',
                        'swarmproof-run-modal',
                        'swarmproof-hooks-modal',
                        'swarmproof-receipt-modal'
                    ].forEach(id => {
                        const el = document.getElementById(id);
                        if (el) el.classList.add('hidden');
                    });
                }
            });
            
            // Show AGY as default agent details
            showAgentDetail('agy');
        });
