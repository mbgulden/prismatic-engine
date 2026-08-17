# Prismatic Hub Canonical Theme & Design System Reference

This document establishes the single source of truth for UI layouts, visual styles, custom icons, and component standards across the Prismatic Hub dashboard. All agents, components, and tabs must adhere to these tokens to maintain aesthetic consistency and avoid reinventing styles.

---

## 1. Central Theme Dictionary (`PRISMATIC_THEME`)

The frontend engine maintains `PRISMATIC_THEME` in `prismatic/gateway/templates/dashboard.html` as the unified registry for icons, badges, colors, and layout tokens.

```javascript
const PRISMATIC_THEME = {
    icons: {
        file: function(ext = "") { ... },     // Code (Sky), Docs (Indigo), Config (Emerald), Media (Purple)
        folder: function(isOpen = false) { ... }, // Open (Amber-400), Closed (Amber-500)
        lock: `<svg ...>`,                   // Cyan mutex lock
        unlock: `<svg ...>`,                 // Emerald unlocked
        shield: `<svg ...>`,                 // Amber defense & deflection shield
        heartbeat: `<svg ...>`,              // Emerald pulsing heartbeat
        warning: `<svg ...>`,                // Amber alert triangle
        externalLink: `<svg ...>`,           // Action link arrow
        linear: `<svg ...>`,                 // Linear task mark
        github: `<svg ...>`,                 // GitHub Octocat
        kanban: `<svg ...>`,                 // Hermes Kanban board
        robot: `<svg ...>`,                  // Agent bot head
        agent: `<svg ...>`,                  // User / principal profile
        branch: `<svg ...>`                  // Git branch
    },
    badge: function(type, label) { ... }     // Intention & status pill generators
};
```

---

## 2. Icon Palette & File Extension Mapping

| Extension Category | Target Extensions | Accent Color | Theme Function |
| :--- | :--- | :--- | :--- |
| **Code & Scripts** | `.py`, `.ts`, `.js`, `.tsx`, `.jsx`, `.sh`, `.json`, `.html`, `.css`, `.go`, `.rs` | Sky (`text-sky-400`) | `PRISMATIC_THEME.icons.file("py")` |
| **Documentation** | `.md`, `.txt`, `.log`, `.rst`, `.doc`, `.pdf` | Indigo (`text-indigo-400`) | `PRISMATIC_THEME.icons.file("md")` |
| **Configurations** | `.env`, `.yaml`, `.yml`, `.toml`, `.ini`, `.conf` | Emerald (`text-emerald-400`) | `PRISMATIC_THEME.icons.file("yaml")` |
| **Media & Assets** | `.png`, `.jpg`, `.svg`, `.ico`, `.webp` | Purple (`text-purple-400`) | `PRISMATIC_THEME.icons.file("png")` |
| **Directories** | Folders (Open / Closed) | Amber (`text-amber-400`) | `PRISMATIC_THEME.icons.folder(isOpen)` |

---

## 3. Dynamic Agent Identity & Task Resolvers

### Dynamic Agent Palette Hashing
Agent avatar pills must **never** be hard-coded. Use deterministic HSL string hashing:
```javascript
function getAgentColor(agentId) {
    let hash = 0;
    const str = String(agentId || "unknown");
    for (let i = 0; i < str.length; i++) hash = str.charCodeAt(i) + ((hash << 5) - hash);
    return `hsl(${Math.abs(hash % 360)}, 75%, 45%)`;
}
```

### Portable Task Linking (`resolveTaskLink`)
Automatically routes tasks to their native provider with the appropriate custom SVG icon:
- **Linear**: `GRO-xxxx` → `PRISMATIC_THEME.icons.linear` → `https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-xxxx`
- **GitHub**: `#xx` / `gh-xx` → `PRISMATIC_THEME.icons.github` → `https://github.com/mbgulden/prismatic-engine/issues/xx`
- **Hermes Kanban**: `task-xx` → `PRISMATIC_THEME.icons.kanban` → `/tab/tasks?task=task-xx`

---

## 4. Primitive Intention Badges

| Intention Primitive | Style Token | Usage |
| :--- | :--- | :--- |
| `EXCLUSIVE_MUTATION` | `bg-rose-950/60 text-rose-300 border-rose-800/60` | Destructive edits, schema migrations, refactors |
| `SAFE_REFACTOR` | `bg-cyan-950/60 text-cyan-300 border-cyan-800/60` | TDD changes, additive features, new endpoints |
| `REENTRANT_READ` | `bg-indigo-950/60 text-indigo-300 border-indigo-800/60` | Read-only analysis, static verification, research |

---

## 5. Surface & Container Standards

1. **Top Cockpit Card**: `.glass-panel.rounded-xl.border.border-slate-800.bg-slate-900/40`
2. **Inner Sub-Panel / Lease Card**: `.rounded-xl.border.border-slate-800.bg-slate-900/60`
3. **Contention Warning**: `.p-2.rounded-lg.bg-amber-950/50.border.border-amber-800/50.text-amber-300`
4. **Modals**: Fixed overlay with `bg-slate-950/80 backdrop-blur-md` and inner card `.pe-modal-card.glass-panel.bg-slate-900.border-slate-700/80`.
