#!/bin/bash
# Shell wrapper to execute the Prismatic Engine portable skills export script

# Get the directory of this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is required to run the export script." >&2
    exit 1
fi

# Run the python script
python3 "$SCRIPT_DIR/export.py"
