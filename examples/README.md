# Prismatic Engine examples

Small, runnable examples. From the repo root with the package installed
(`pip install -e ".[gateway]"`):

| Example | What it shows | Run |
|---|---|---|
| `hello-plugin/` | Build, validate, and load a minimal plugin from scratch | `python3 examples/hello-plugin/run.py` |
| `evidence-contract/` | The execution evidence contract: why `self_reported` can never count as Done | `python3 examples/evidence-contract/run.py` |
| `trust-ledger/` | Append and read trust-ledger events (the earned-autonomy audit trail) | `python3 examples/trust-ledger/run.py` |

Each example runs with plain `python3`, prints its result, and leaves no state
behind (temporary directories only).
