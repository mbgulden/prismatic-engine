#!/usr/bin/env python3
"""
Audit Checksums — Compare SHA256 of artifacts against expected hashes.

Usage:
    python3 scripts/audit-checksums.py [--manifest <path>]

Exits:
    0 if all match or manifest is missing.
    1 on mismatch or read error.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

def calculate_sha256(filepath):
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def main():
    parser = argparse.ArgumentParser(description="Audit artifact checksums.")
    parser.add_argument("--manifest", default="prismatic_state/artifact_checksums.json", help="Path to checksum manifest.")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)

    if not manifest_path.exists():
        print(f"Note: {manifest_path} missing. Skipping checksum audit.")
        sys.exit(0)

    try:
        with open(manifest_path, "r") as f:
            checksums = json.load(f)
    except Exception as e:
        print(f"Error reading {manifest_path}: {e}")
        sys.exit(1)

    mismatches = []
    missing_files = []

    for filepath, expected_hash in checksums.items():
        path = Path(filepath)
        if not path.exists():
            print(f"Missing: {filepath}")
            missing_files.append(filepath)
            continue
        
        try:
            actual_hash = calculate_sha256(path)
            if actual_hash != expected_hash:
                print(f"Mismatch: {filepath} (Expected: {expected_hash}, Actual: {actual_hash})")
                mismatches.append(filepath)
            else:
                print(f"OK: {filepath}")
        except Exception as e:
            print(f"Error hashing {filepath}: {e}")
            mismatches.append(filepath)

    if mismatches or missing_files:
        print(f"\nAudit FAILED: {len(mismatches)} mismatches, {len(missing_files)} missing files.")
        sys.exit(1)

    print("\nAudit PASSED: All checksums match.")
    sys.exit(0)

if __name__ == "__main__":
    main()
