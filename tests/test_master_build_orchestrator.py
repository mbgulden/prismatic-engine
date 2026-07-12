# SPDX-License-Identifier: AGPL-3.0-only
# Prismatic Engine — Portable Agent Orchestration
# Copyright (C) 2026 Michael Gulden

import unittest
import os
import json
from prismatic.master_build_orchestrator import MasterBuildOrchestrator

class TestMasterBuildOrchestrator(unittest.TestCase):
    def setUp(self):
        self.schema_path = "schemas/orchestration-session-manifest-schema.json"
        self.valid_manifest_path = "test_valid_manifest.json"
        self.invalid_manifest_path = "test_invalid_manifest.json"
        
        valid_manifest = {
            "version": 1,
            "updated_at": "2026-06-14T08:00:00Z",
            "units": [
                {
                    "id": "TEST-UNIT-1",
                    "title": "Test Unit Title",
                    "target_repository": "owner/repo",
                    "source": {
                        "system": "manual",
                        "reference": "test ref"
                    },
                    "risk_class": "docs-only",
                    "worker": "hermes",
                    "state": "proposed",
                    "expected_artifacts": ["artifact1"],
                    "acceptance_checks": ["check1"],
                    "human_approval_required": False,
                    "timeout_policy": {
                        "max_status_checks": 5,
                        "terminal_on_timeout": "blocked"
                    },
                    "last_verified_at": "2026-06-14T08:00:00Z"
                }
            ]
        }
        
        with open(self.valid_manifest_path, "w") as f:
            json.dump(valid_manifest, f)
            
        invalid_manifest = {
            "version": "not-an-integer",
            "units": []
        }
        
        with open(self.invalid_manifest_path, "w") as f:
            json.dump(invalid_manifest, f)

    def tearDown(self):
        if os.path.exists(self.valid_manifest_path):
            os.remove(self.valid_manifest_path)
        if os.path.exists(self.invalid_manifest_path):
            os.remove(self.invalid_manifest_path)

    def test_validation_success(self):
        orchestrator = MasterBuildOrchestrator(self.valid_manifest_path, self.schema_path)
        self.assertTrue(orchestrator.validate_anchor_manifest())

    def test_validation_failure(self):
        orchestrator = MasterBuildOrchestrator(self.invalid_manifest_path, self.schema_path)
        self.assertFalse(orchestrator.validate_anchor_manifest())

if __name__ == "__main__":
    unittest.main()