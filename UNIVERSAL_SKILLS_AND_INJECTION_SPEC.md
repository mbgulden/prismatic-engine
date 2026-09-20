# 📐 Architectural Specification: Universal Skill Discovery Engine, Mandatory Injection Gate & Dashboard Surface Restoration

**Engine Version**: Prismatic Engine v0.3.3  
**Feature Name**: Universal Skill Discovery Engine, Mandatory Injection Gate & Dashboard Surface Restoration  
**Task Identifier**: [GRO-4363](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4363)  
**Status**: ARCHITECTURAL SPECIFICATION & PRODUCTION DESIGN  

---

## 1. Universal & Portable Skill Discovery Hierarchy

To ensure skills are discoverable on ANY operating system or deployment model (Windows, Linux, macOS, Docker container, cloud VM, or serverless runtime) without hardcoding user-specific paths:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                 UNIVERSAL PORTABLE SKILL DISCOVERY RESOLUTION               │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
  Tier 1: Explicit Override ──▶ `os.environ["PRISMATIC_SKILLS_PATH"]`
                                       │
  Tier 2: Workspace Root    ──▶ `<git_repo_root>/.agents/skills/` (Versioned in Git)
                                       │
  Tier 3: Engine Package    ──▶ `Path(__file__).parent / "skills"` (Package Data)
                                       │
  Tier 4: User Home         ──▶ `Path.home() / ".prismatic" / "skills"`
```

### Resolution Rules:
- **Portability**: Relative path calculations resolve via `Path(__file__).resolve().parent`, making skills universally accessible regardless of machine OS or install prefix.
- **Precedence**: Workspace-specific skills in `.agents/skills/` override package default skills in `prismatic/skills/`.

---

## 2. Mandatory Prompt Context Injection Gate ("Used EVERY Time")

To guarantee that AGY CLI, Fred, Kai, Autobot, and all subagents invoke and verify compliance against mandatory verification skills (`subagent-claim-verification-gate`, `antigravity-prismatic-pr-evidence`, `prismatic-agent-closeout-contract`) on EVERY work turn:

1. **System Prompt Directive Injection**:
   - Automatically appended to `.agents/AGENTS.md` and injected into every agent turn prompt context.
2. **Fail-Closed Execution Gate**:
   - `task_admission_consumer.py` and `prismatic/cli` validate that before a task task is marked complete, `result-packet.json` exists, `VERIFICATION_MARKER` equals `PE_CLEAN_ROOM_VERIFIED_OK` or `PE_PACKET_REPLAY_VERIFIED_OK`, and log digests match.

---

## 3. Dashboard UI Surface Restoration (`https://prismatic.growthwebdev.com/skills`)

1. **Eliminate 1s Flickering**:
   - Replaces `setInterval(fetchSkills, 1000)` continuous polling with WebSocket event subscription (`skills.synced`) + on-mount fetch.
2. **Installed Status Badge**:
   - Skills present in `.agents/skills/` or `prismatic/skills/` render an explicit green **Installed** badge.
3. **Uninstall & Reset Actions**:
   - Render **Uninstall** button for user custom skills and **Reset to Default** for core skills.
4. **Upload Skill Modal**:
   - Render **+ Upload Skill** button opening a drag-and-drop modal posting to `POST /api/skills/upload`.

---

## 4. Subsystem Components

1. **`prismatic/skills.py`**: Implementation of `get_universal_skills_dirs`, `list_skills`, and `upload_skill`.
2. **`prismatic/gateway/server.py`**: Restored `/skills` HTML surface, `/api/skills` JSON handlers, and `POST /api/skills/upload`.
3. **`tests/test_universal_skills.py`**: Unit test suite.
