# Linear budget gate

`prismatic.linear.budget.LinearBudget` is the shared local gate used by the AGY sandbox supervisor before Linear GraphQL requests.

Operational contract:

- Canonical DB: `prismatic_state/linear_budget.db`
- Default bucket: 2,500 local Linear request tokens/hour for the shared `global` bucket
- Missing module failure mode: AGY supervisor fails closed before worker spawn and logs `LinearBudget unavailable` / `startup gates failed` in `/tmp/longrun-watchdog.log`
- The module avoids optional settings imports so early supervisor startup can import it from the active engine checkout.

Recovery check:

```bash
cd /home/ubuntu/work/prismatic-engine
python3 - <<'PY'
from prismatic.linear.budget import LinearBudget
b = LinearBudget(db_path='/tmp/linear_budget_import_test.db')
print(b.check_and_consume('test', cost=1))
PY
```

Expected: `True`.
