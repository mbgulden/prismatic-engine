import pytest
from prismatic.router import score_agent, select_agent

# Dummy agent registry configuration
DUMMY_REGISTRY = {
    "agents": {
        "fred": {
            "supported_modalities": ["text", "code"],
            "specialization_weights": {
                "implementer": 0.9,
                "reviewer": 0.4
            },
            "vram_preload": ["claude-opus"]
        },
        "agy": {
            "supported_modalities": ["text", "image", "audio"],
            "specialization_weights": {
                "designer": 0.95,
                "researcher": 0.8,
                "reviewer": 0.7
            },
            "vram_preload": ["gemini-3.5-flash", "claude-opus"]
        },
        "kai": {
            "supported_modalities": ["text"],
            "specialization_weights": {
                "writer": 0.85
            },
            "vram_preload": []
        }
    }
}

def test_modality_eligibility():
    # Task requires text and code modalities
    task_metadata = {
        "required_modalities": ["text", "code"],
        "capability": "implementer"
    }
    
    # Fred supports both text and code
    score_fred = score_agent("fred", DUMMY_REGISTRY["agents"]["fred"], task_metadata)
    assert score_fred > 0.0
    
    # AGY does not support code (supports text, image, audio)
    score_agy = score_agent("agy", DUMMY_REGISTRY["agents"]["agy"], task_metadata)
    assert score_agy == 0.0

def test_specialization_weight():
    # Task is reviewer task
    task_metadata = {
        "required_modalities": ["text"],
        "capability": "reviewer"
    }
    
    # Fred has reviewer weight 0.4
    score_fred = score_agent("fred", DUMMY_REGISTRY["agents"]["fred"], task_metadata)
    # No VRAM preload bonus because no model specified
    assert score_fred == 0.4
    
    # AGY has reviewer weight 0.7
    score_agy = score_agent("agy", DUMMY_REGISTRY["agents"]["agy"], task_metadata)
    assert score_agy == 0.7

def test_case_insensitivity_capability():
    # Task specifies capability in different case
    task_metadata = {
        "required_modalities": ["text"],
        "capability": "DESIGNER"  # registry has lowercase 'designer'
    }
    
    score_agy = score_agent("agy", DUMMY_REGISTRY["agents"]["agy"], task_metadata)
    assert score_agy == 0.95

def test_vram_preload_bonus():
    # Task requires gemini-3.5-flash
    task_metadata = {
        "required_modalities": ["text"],
        "required_model": "gemini-3.5-flash",
        "capability": "reviewer"
    }
    
    # AGY supports text, reviewer weight 0.7, and has gemini-3.5-flash preloaded
    # Score should be 0.7 + 0.3 = 1.0
    score_agy = score_agent("agy", DUMMY_REGISTRY["agents"]["agy"], task_metadata)
    assert score_agy == 1.0
    
    # Fred supports text, reviewer weight 0.4, but does not have gemini-3.5-flash preloaded
    # Score should be 0.4 + 0.0 = 0.4
    score_fred = score_agent("fred", DUMMY_REGISTRY["agents"]["fred"], task_metadata)
    assert score_fred == 0.4

def test_select_agent_basic():
    # Task: reviewer role, requires text and claude-opus
    task_metadata = {
        "required_modalities": ["text"],
        "required_model": "claude-opus",
        "capability": "reviewer"
    }
    
    # Fred: weight 0.4 + 0.3 (claude-opus preloaded) = 0.7
    # AGY: weight 0.7 + 0.3 (claude-opus preloaded) = 1.0
    # Kai: weight 0.5 (default reviewer weight) + 0.0 (no preload) = 0.5
    
    best_agent, scores = select_agent(task_metadata, DUMMY_REGISTRY)
    assert best_agent == "agy"
    assert scores["agy"] == 1.0
    assert scores["fred"] == 0.7
    assert scores["kai"] == 0.5

def test_select_agent_tie_breaking():
    # Two agents with same score should tie-break alphabetically on name
    # Task: writer role, requires text
    task_metadata = {
        "required_modalities": ["text"],
        "capability": "reviewer"
    }
    
    # Modify registry to give fred and agy same reviewer weight, no preloads
    modified_registry = {
        "agents": {
            "fred": {
                "supported_modalities": ["text"],
                "specialization_weights": {"reviewer": 0.8}
            },
            "agy": {
                "supported_modalities": ["text"],
                "specialization_weights": {"reviewer": 0.8}
            }
        }
    }
    
    best_agent, scores = select_agent(task_metadata, modified_registry)
    # 'agy' comes before 'fred' alphabetically
    assert best_agent == "agy"
    assert scores["agy"] == 0.8
    assert scores["fred"] == 0.8

def test_no_eligible_agents():
    task_metadata = {
        "required_modalities": ["video"],
        "capability": "designer"
    }
    best_agent, scores = select_agent(task_metadata, DUMMY_REGISTRY)
    assert best_agent is None
    assert scores == {}
