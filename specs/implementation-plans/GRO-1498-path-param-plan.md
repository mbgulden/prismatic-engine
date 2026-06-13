I will list the contents of the `/home/ubuntu/work/prismatic-engine/specs` directory to see if there is an existing structure or similar implementation plans that I should align the format with.
I will read the path parameterization analysis document to get more context and align our plan with any findings or recommendations made there.
I will check the existing implementation plans to see what format and structure they follow.
I will read a portion of one of the spec files to understand the design document structure and style in the codebase.
I will list the directories in `/home/ubuntu` to understand where files are located.
I will list the root contents of the `/home/ubuntu/work/prismatic-engine` directory to see the project's structure, files, and where tests are located.
I will list the contents of the `prismatic` directory to locate source files and the test suite.
I will locate the test files in the codebase to verify where the tests reside and how they are structured.
I will run `pytest` to see how the existing test suite behaves and what tests are currently present.
I will list the `.venv_dev` directory to check if it contains a virtual environment we can use to run tests.
I will run `./.venv_dev/bin/pytest` to see if we can execute the test suite from the virtual environment.
I will view `pyproject.toml` to see how project dependencies and tests are configured.
I will view `install.sh` to see how the system is installed.
I will read `scripts/canary_test.sh` to understand how the test or verification process works in this project.
I will list the files in the `scripts` directory to see other utility or testing scripts available.
I will view the `scripts/migrate_paths.py` file to see if there is an existing migration script implementation or template.
I will list the contents of the orchestrator profile directory to verify the file layout and see what files exist.
I will list the contents of the `skills` subdirectory to understand how skills are structured in the orchestrator profile.
I will list the contents of the `agent-orchestration` skill directory to check the types of files within a skill.
I will list the contents of the `agent-ned` subdirectory to check the actual files and their extensions.
I will list the files in the `scripts` subdirectory of `agent-ned` to see the executable scripts.
I will perform a search across the workspace to see if there are other references to `GRO-1498` or existing notes about it.
I will run a grep command in the terminal to find references to `GRO-1498` in the repository.
I will read lines 15 to 40 of `specs/second-witness-context.md` to see the context around GRO-1498.
I will check the first 50 lines of `specs/portable-skill-export-design.md` to align the tone and style of our implementation plan with other design documents in the repository.
I will write the implementation plan to the specified path `/home/ubuntu/work/prismatic-engine/specs/implementation-plans/GRO-1498-path-param-plan.md`.
I will write the implementation plan to `/home/ubuntu/work/prismatic-engine/specs/implementation-plans/GRO-1498-path-param-plan.md` without the `ArtifactMetadata` field since it is a codebase file, not a system-generated artifact.
I will view the first 20 lines of the written implementation plan to ensure it looks correct and is successfully formatted.
I have fleshed out and saved the implementation plan for **GRO-1498: Path Parameterization** to [GRO-1498-path-param-plan.md](file:///home/ubuntu/work/prismatic-engine/specs/implementation-plans/GRO-1498-path-param-plan.md).

### Summary of Work:
1.  **Read Context & Analysis**: Inspected the repository's path parameterization analysis doc ([path-parameterization-analysis.md](file:///home/ubuntu/work/prismatic-engine/specs/path-parameterization-analysis.md)) and the existing skeleton code for [scripts/migrate_paths.py](file:///home/ubuntu/work/prismatic-engine/scripts/migrate_paths.py) to extract exact patterns and constraints.
2.  **Authored Implementation Plan**: Created a detailed migration spec covering:
    *   **Architecture**: Interactive/recursive scanning, dry-run mode, and transactional rollback mechanisms using backup files.
    *   **File-Type Patterns**: Customized translations for Python, YAML, Shell scripts, and Markdown (including language-aware syntax mapping within code blocks).
    *   **Verification Strategy**: Detailed manual and automated testing flows, syntax compilation tests, and runtime override checks using custom environment paths.
    *   **Actionable Sub-tasks**: 5 concrete next steps for development.
