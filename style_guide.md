# Prismatic Engine — Brand Style Guide & Design Tokens

This document defines the visual standards, color palette, typography, iconography, and layout principles for **Prismatic Engine** (`prismaticengine.com`).

---

## 🎨 Color Palette

The color palette represents a dark, industrial "chassis" combined with a vibrant, high-fidelity light spectrum.

### Base Colors
* **Obsidian (Core Background)**: `#080809` (HSL: `240°, 9%, 5%`)
* **Chassis (Card Background)**: `#121217` (HSL: `240°, 12%, 8%`)
* **Charcoal (Input/Code Background)**: `#0e0e12` (HSL: `240°, 10%, 6%`)
* **Steel (Border/Grid Lines)**: `#222226` (HSL: `240°, 6%, 14%`)
* **Active (Border Highlight)**: `#3A3A42` (HSL: `240°, 6%, 24%`)
* **White Beam (Highlights/Flares)**: `#FFFFFF`

### Text Colors
* **Primary Text**: `#F5F5F7` (High contrast, slightly warm off-white)
* **Muted Text**: `#8E8E93` (Medium contrast, slate grey)

### The Agent Spectrum
Each agent in the swarm represents a unique wavelength of light:
* **🔴 AGY (Orchestration & Vision)**: `#FF4545` (HSL: `0°, 100%, 63%`)
* **🟠 Jules (Workspace & PR Review)**: `#FF9F1C` (HSL: `35°, 100%, 55%`)
* **🟡 Custom (Bolt-On Extensions)**: `#FFCC00` (HSL: `48°, 100%, 50%`)
* **🟢 Hermes (Routing & Messaging)**: `#00E676` (HSL: `151°, 100%, 45%`)
* **🔵 Codex (Code & Repository)**: `#00B0FF` (HSL: `198°, 100%, 50%`)
* **🟣 Research (Deep Analysis)**: `#D500F9` (HSL: `291°, 100%, 49%`)

---

## font Typography

We utilize clean, modern sans-serif typography for UI text and high-contrast monospaced typography for code blocks, terminal logs, and system statuses.

* **Sans-Serif (Default)**: `'Geist', 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;`
* **Monospaced (System/Logs)**: `'Geist Mono', 'JetBrains Mono', monospace;`

---

## 📐 Iconography & Visual Metaphors

Our design language centers on **Refraction** — raw data entering a triangular prism and splitting into a spectrum of specialized wavelengths (agents).

### Standalone SVG Assets
* **Prismatic Logo**: A simplified geometric design combining:
  1. A vertical white stem (the letter "P" shape).
  2. A glowing triangular glass prism forming the curve of the "P".
  3. An incoming white data beam and an outgoing refracted multi-color spectrum beam.
* **Animated Hero Illustration**: An isometric grid layout displaying the raw data beam entering a 3D glass prism, refracting into six distinct curves with flowing comets and custom agent icons.

### Agent Symbols
* **AGY**: Ruby Gem (representing structural integrity, clarity, and value).
* **Jules**: Gear (representing mechanics, workspace audits, and automation).
* **Custom**: Puzzle Piece (representing flexibility and modular extensions).
* **Hermes**: Wing (representing speed, messaging, and event dispatches).
* **Codex**: Code Brackets (representing source code generation and editing).
* **Research**: Magnifying Glass (representing queries and deep data search).

---

## 🖥️ Layout & Grid System

* **Grounded Grid Backdrop**: The background is a fixed 40px × 40px grid with `1px` steel lines at `12%` opacity, grounding all visual elements.
* **Navigation Bar**: A sticky header with `rgba(10, 10, 11, 0.85)` background, a `1px` steel bottom border, and `backdrop-filter: blur(12px)` for glassmorphic elevation.
* **Chassis Cards**: Content cards have a strict border-radius of `4px` with a `1px` glass border, a top-left/top-right dark metal rivet (`3px` circular dot), and high depth shadow.
* **Typewriter Terminal**: A simulated terminal logging widget displaying active logs from the agent mesh, featuring a blinking cursor (`0.8s` animation) and HSL spectrum-colored logs.
