I will explore the codebase at `/home/ubuntu/work/prismatic-engine/` to locate the existing scripts and understand the project layout, which will help us design a cohesive implementation plan for `GRO-1496`.
I will list the contents of the `scripts` directory to locate the mentioned files.
I will view the contents of [scripts/pre-push-hook.py](file:///home/ubuntu/work/prismatic-engine/scripts/pre-push-hook.py) to see how validation is currently handled.
I will view [scripts/verify-pipeline.sh](file:///home/ubuntu/work/prismatic-engine/scripts/verify-pipeline.sh) to see the existing script verification process.
I will view [scripts/pre-commit-hook.sh](file:///home/ubuntu/work/prismatic-engine/scripts/pre-commit-hook.sh) and [scripts/canary_test.sh](file:///home/ubuntu/work/prismatic-engine/scripts/canary_test.sh) to check if they have stub implementations or prior code.
I will view [scripts/canary_test.sh](file:///home/ubuntu/work/prismatic-engine/scripts/canary_test.sh) to see the existing canary test script.
I will list the contents of the `specs` directory to see if there are any existing templates or other implementation plans.
I will view the existing [specs/safe-update-pipeline-design.md](file:///home/ubuntu/work/prismatic-engine/specs/safe-update-pipeline-design.md) file to understand the architecture, design choices, and details of the safe update pipeline.
I will list the contents of [specs/implementation-plans/](file:///home/ubuntu/work/prismatic-engine/specs/implementation-plans) to see how other plans in this directory are styled and structured.
I will view the existing implementation plan [specs/implementation-plans/GRO-1494-dual-runtime-plan.md](file:///home/ubuntu/work/prismatic-engine/specs/implementation-plans/GRO-1494-dual-runtime-plan.md) to align our new plan with the existing structure and tone.
I will view the existing implementation plan [specs/implementation-plans/GRO-1498-path-param-plan.md](file:///home/ubuntu/work/prismatic-engine/specs/implementation-plans/GRO-1498-path-param-plan.md) to compare formatting and structure.
I will view the content of [specs/implementation-plans/GRO-1494-dual-runtime-plan.md](file:///home/ubuntu/work/prismatic-engine/specs/implementation-plans/GRO-1494-dual-runtime-plan.md) from line 30 onwards to see the actual format.
I will view the content of [specs/implementation-plans/GRO-1499-portable-skills-plan.md](file:///home/ubuntu/work/prismatic-engine/specs/implementation-plans/GRO-1499-portable-skills-plan.md) to see if it follows a different layout.
I will view [pyproject.toml](file:///home/ubuntu/work/prismatic-engine/pyproject.toml) to understand the project configuration and dev dependencies.
I will list the contents of the `tests` directory to see the structure of current unit/integration tests.
I will view [tests/test_dispatcher_activation.py](file:///home/ubuntu/work/prismatic-engine/tests/test_dispatcher_activation.py) to see the existing test structure.
I will view the [.pre-commit-config.yaml](file:///home/ubuntu/work/prismatic-engine/.pre-commit-config.yaml) file to see the current pre-commit setup.
I will view [scripts/setup-dual-venvs.sh](file:///home/ubuntu/work/prismatic-engine/scripts/setup-dual-venvs.sh) to understand how the development virtual environment (`.venv_dev`) is structured and what packages it installs.
I will view [specs/distribution-packaging-design.md](file:///home/ubuntu/work/prismatic-engine/specs/distribution-packaging-design.md) to understand how the packaging (GRO-1495) is designed and how our update plan should integrate with it.
I will view the [Dockerfile](file:///home/ubuntu/work/prismatic-engine/Dockerfile) in the repository root to see the current container build setup.
I will view [scripts/export-prismatic-env.sh](file:///home/ubuntu/work/prismatic-engine/scripts/export-prismatic-env.sh) to ensure we use the correct environment variable names and directories.
I will now write the detailed implementation plan for `GRO-1496` to the file `/home/ubuntu/work/prismatic-engine/specs/implementation-plans/GRO-1496-safe-update-plan.md`. This plan will define the configuration for pre-commit hooks, the canary test harness structure, the rollback runner, the 120s watchdog wrapper, and 6 concrete sub-tasks to achieve safe promotion and update pipelines.
I have completed and saved the implementation plan for **GRO-1496: Safe Update Pipeline (Pre-commit Hooks + Canary Harness)**.

### Summary of Work Done
1. **Analyzed Existing Context**: Inspected the repository structure, [.pre-commit-config.yaml](file:///home/ubuntu/work/prismatic-engine/.pre-commit-config.yaml), [scripts/pre-commit-hook.sh](file:///home/ubuntu/work/prismatic-engine/scripts/pre-commit-hook.sh), and [scripts/canary_test.sh](file:///home/ubuntu/work/prismatic-engine/scripts/canary_test.sh) to ensure full backward-compatibility and clean integration.
2. **Designed the Pre-Commit Gate**:
   - Outlined modifications to `.pre-commit-config.yaml` to run `ruff`, `yamllint`, `shellcheck`, `mypy` type checks, and local system `pytest` runs.
   - Refactored `scripts/pre-commit-hook.sh` to enforce a zero-tolerance failure mode (terminating with non-zero status code on any linting, type-checking, or testing failures).
3. **Structured the Canary Sandbox Harness**:
   - Specified how `scripts/canary_test.sh` runs inside the Docker sandbox on `PVE1` with a mock Linear GraphQL provider.
   - Incorporated executing the full test suite (`pytest tests/`) and specific dispatcher/contract boundaries checks.
4. **Detailed the Watchdog & Auto-Rollback Mechanism**:
   - Defined the 120-second watchdog command wrapper (`timeout 120s`) to prevent hang-ups during canary tests or daemon updates.
   - Defined `scripts/rollback_core.sh` to swap active symlinks back to the previous stable release version on the target machine (`PVE3`) and safely restart the dispatcher daemon.
5. **Drafted 6 Concrete Sub-tasks**: Organized the execution of this plan into 6 clear sub-tasks mapped to specific owners (Ned / Fred).

The implementation plan is saved at: [GRO-1496-safe-update-plan.md](file:///home/ubuntu/work/prismatic-engine/specs/implementation-plans/GRO-1496-safe-update-plan.md).
