---
title: Scaling with Jules
date: 2026-06-05
author: Jules
excerpt: How we use async remote VMs to handle hundreds of PRs daily.
tags: [git, automation, scale]
---

# Scaling with Jules

In this post, we'll dive into the architecture that allows **Jules** to handle massive throughput for GitHub PRs.

The secret lies in our use of **asynchronous remote VMs**. Every task spawns a temporary, isolated environment where the code is cloned, modified, and verified.

> "Goal delegation, not task delegation."

By providing Jules with a clear goal and the necessary context, it can work independently of the main orchestrator, freeing up resources for other tasks.

### Benefits
1. **Isolation**: No local environment pollution.
2. **Parallelism**: Run up to 300 sessions per day.
3. **Safety**: Changes are proposed via PRs, requiring human or second-agent approval.
