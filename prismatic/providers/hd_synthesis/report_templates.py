"""
HD Synthesis Backend — Report Templates
========================================

Narrative templates for individual, relationship, and transit reports.
Designed for plain-English synthesis of raw HD engine data.
"""

INDIVIDUAL_REPORT_TEMPLATE = """
# Human Design Deep Dive: {name}

## Your Operating System
**Type:** {type_summary}
**Authority:** {authority_summary}
**Profile:** {profile_summary}

## The Inner Landscape
{inner_self_narrative}

## How You Move in the World
{outer_self_narrative}

## Real-World Scenarios
{scenarios}

---
*Note: This report is a synthesis of your natal chart data. While the engine provides the map, your lived experience is the territory.*
"""

RELATIONSHIP_REPORT_TEMPLATE = """
# Relationship Synthesis: {person_a} & {person_b}

## The Foundation
{dynamics_summary}

## User Manual: {person_a}'s Guide to {person_b}
{person_a_perspective}

## User Manual: {person_b}'s Guide to {person_a}
{person_b_perspective}

## Deep Synthesis & Collaboration
{synthesis_framework}

## 12-Month Transit Weather
{transit_forecast}
"""

TRANSIT_REPORT_TEMPLATE = """
# Transit Weather Report: {name}

## Current Conditioning
{current_weather}

## The "Phantom Costume"
{phantom_gates_summary}

## 12-Month Outlook
{monthly_forecast}
"""

# Jargon to Plain English Map
JARGON_MAP = {
    "Sacral": "Gut Instinct / Vitality Center",
    "Splenic": "Survival Intuition / Spontaneous Knowing",
    "Emotional": "The Emotional Wave / Feeling Clarity",
    "Not-Self": "The Shadow State / Resistance",
    "Authority": "Decision-Making Compass",
    "Projector": "The Guide / System Seer",
    "Generator": "The Builder / Energy Source",
    "Manifesting Generator": "The Multi-Hyphenate / Fast Builder",
    "Manifestor": "The Initiator / Impact Maker",
    "Reflector": "The Mirror / Community Pulse",
}
