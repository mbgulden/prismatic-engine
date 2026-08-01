---
title: Dashboard V1 Cut
version: v1
rf_slice: RF-5
status: frozen
---

## Overview
This document specifies the V1 metrics scope and UI components for the Review Factory dashboard.

## Display Elements
- **Queue Operations Tab**: Shows queue depth grouped by state, age distribution, and overall throughput.
- **Authorization State**: Lists pending, consumed, and expired authorizations.
- **Reviewer Pool**: Displays active leases and reviewer agent utilization.
- **Deferred to V2**: Latency percentiles and strict SLA tracking.

## API Routes
- `GET /api/review-factory/queue`
- `GET /api/review-factory/job/{id}`
- `GET /api/review-factory/authorizations`

## Events
- **WebSocket Events**:
  - `review_factory.job_state_changed`
  - `review_factory.authorization_requested`

## Web Integration
- **Dashboard File**: Found at `prismatic/gateway/dashboard_src/tabs/review_factory.html`.
