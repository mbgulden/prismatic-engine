import json
import sys

env = json.load(sys.stdin)
workdir = env["workdir"]
primes = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
with open(f"{workdir}/result.json", "w") as f:
    json.dump({"primes": primes}, f)
print(
    json.dumps(
        {
            "verdict": "done",
            "summary": "wrote result.json",
            "artifacts": ["result.json"],
        }
    )
)
