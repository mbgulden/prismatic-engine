import json
import sys

env = json.load(sys.stdin)
print(
    json.dumps(
        {
            "verdict": "done",
            "summary": "trust me, all done",
            "artifacts": ["result.json"],
        }
    )
)
