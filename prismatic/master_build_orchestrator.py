# SPDX-License-Identifier: AGPL-3.0-only
# Prismatic Engine — Portable Agent Orchestration
# Copyright (C) 2026 Michael Gulden
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Prismatic Engine — Master Build Orchestrator
===========================================

Orchestrates multi-agent builds and validates the anchor manifest.
The anchor manifest (session-manifest.json) is the source of truth
for all orchestration units and their states.

This orchestrator ensures that:
1. The manifest follows the required JSON schema.
2. Build units are dispatched to the correct workers.
3. State transitions are tracked and verified.
"""

import os
import sys
import json
import logging
import argparse
from typing import Any, Dict, Optional

import jsonschema
import yaml

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("master-build-orchestrator")


class MasterBuildOrchestrator:
    """Handles the orchestration of builds and manifest validation."""

    def __init__(self, manifest_path: str, schema_path: str):
        self.manifest_path = manifest_path
        self.schema_path = schema_path
        self.manifest_data: Optional[Dict[str, Any]] = None

    def load_manifest(self) -> bool:
        """Loads the anchor manifest from the filesystem."""
        try:
            if not os.path.exists(self.manifest_path):
                logger.error(f"Manifest not found: {self.manifest_path}")
                return False

            with open(self.manifest_path, "r") as f:
                if self.manifest_path.endswith(".yaml") or self.manifest_path.endswith(".yml"):
                    self.manifest_data = yaml.safe_load(f)
                else:
                    self.manifest_data = json.load(f)

            # Strip $schema if present to pass strict validation
            if isinstance(self.manifest_data, dict) and "$schema" in self.manifest_data:
                logger.info("Stripping $schema from manifest for validation.")
                del self.manifest_data["$schema"]
            
            logger.info(f"Loaded manifest from {self.manifest_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load manifest: {e}")
            return False

    def validate_anchor_manifest(self) -> bool:
        """Validates the anchor manifest against the defined schema."""
        if not self.manifest_data:
            if not self.load_manifest():
                return False

        try:
            if not os.path.exists(self.schema_path):
                logger.error(f"Schema not found: {self.schema_path}")
                return False

            with open(self.schema_path, "r") as f:
                schema = json.load(f)

            jsonschema.validate(instance=self.manifest_data, schema=schema)
            logger.info("Anchor manifest validation successful.")
            return True
        except jsonschema.exceptions.ValidationError as ve:
            logger.error(f"Anchor manifest validation failed: {ve.message}")
            return False
        except Exception as e:
            logger.error(f"An error occurred during validation: {e}")
            return False

    def orchestrate(self) -> bool:
        """Main orchestration logic."""
        if not self.validate_anchor_manifest():
            return False

        # Placeholder for actual orchestration logic
        logger.info("Orchestrating build units...")
        units = self.manifest_data.get("units", [])
        for unit in units:
            unit_id = unit.get("id")
            state = unit.get("state")
            worker = unit.get("worker")
            logger.info(f"Processing unit {unit_id} (State: {state}, Worker: {worker})")
            
            # Logic to dispatch signals would go here
            
        return True


def main():
    parser = argparse.ArgumentParser(description="Prismatic Master Build Orchestrator")
    parser.add_argument(
        "--manifest", 
        default="ops/orchestration/session-manifest.json",
        help="Path to the anchor manifest file"
    )
    parser.add_argument(
        "--schema", 
        default="schemas/orchestration-session-manifest-schema.json",
        help="Path to the manifest JSON schema"
    )
    parser.add_argument(
        "--validate-only", 
        action="store_true",
        help="Only validate the manifest without orchestrating"
    )

    args = parser.parse_args()

    orchestrator = MasterBuildOrchestrator(args.manifest, args.schema)

    if args.validate_only:
        success = orchestrator.validate_anchor_manifest()
    else:
        success = orchestrator.orchestrate()

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()