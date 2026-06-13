I will start by listing the contents of the `/home/ubuntu/work/prismatic-engine/` directory to see the structure and locate files like `pyproject.toml` and any existing packaging directories or specifications.
I will read the contents of `pyproject.toml` to see what is currently configured.
I will list the contents of the `prismatic` package directory to check what Python files and modules exist.
I will view the existing `install.sh` in the repository root to see what it does.
I will list the contents of the `specs` directory to see if there are other specs or implementation plans.
I will view the `distribution-packaging-design.md` file in the `specs` directory to understand the design requirements and context of the packaging setup.
I will list the files in the `specs/implementation-plans/` directory to see if there are any existing implementation plans or files that provide guidance.
I will view the `GRO-1494-dual-runtime-plan.md` implementation plan to see the formatting and structure used for other tasks.
I will read another implementation plan from the directory (`GRO-1498-path-param-plan.md`) to see if there is a consistent format and depth required.
I will read `GRO-1499-portable-skills-plan.md` to check if its contents also look like the text of a previous assistant's response.
I will search for the definition of `main` or other entry point functions in the `prismatic` package to verify what is actually present.
I will run a `grep` command to locate function definitions of entry points in the `prismatic` directory.
I will grep for `def cli_skills` in the `prismatic` directory to see if that exists in `skills.py`.
I will view `prismatic/skills.py` around line 200 to understand the signature of `cli_skills` and if we need to wrap it for the console script entry point.
I will view the `main` function in `prismatic/dispatcher.py` to see how subcommands (like `skills`) are routed and how the CLI arguments are handled.
I will view the `main` function in `prismatic/lock.py` to see its signature and how it parses arguments.
I will view the `main` function in `prismatic/admin.py` to see its signature and how it parses arguments.
I will search for `import` statements in the `prismatic` directory to compile a list of all required package dependencies.
I will search for `from ... import` statements in the `prismatic` directory to see if any other libraries are imported.
I will search for occurrences of `flask` in the `prismatic` directory to see which module imports it and check if it's optional or required.
I will view the import context of `flask` in `prismatic/providers/signals/http.py` to see if it is a required import or lazily loaded.
I will search for `packaging` in the `prismatic` directory to see where it is used.
I will view the imports in `prismatic/core/registry.py` to see what dependencies are imported there.
I will search for any imports or usage of `requests` and `redis` in the `prismatic` directory to verify if they are indeed used.
I will search for any reference to `requests` in the `prismatic` directory.
I will search for `urllib` or `requests` in `prismatic/providers/signals/http.py` to see what HTTP request mechanism is used.
I will list all function definitions in `prismatic/skills.py` to check if there is a wrapper or entry point function designed for CLI execution.
I will search for references to `PRISMATIC_HOME` in the `core-architecture-v1.md` specification to check for any architectural constraints related to directories.
I will list the contents of `prismatic/config` to check if there are configuration files that need to be packaged as package data.
I will search the repository for `sqlite_utils` or `sqlite-utils` to confirm whether it is actually used.
I will search the repository for `watchdog` to see if it is used.
I will search for `aiohttp` in the repository.
I will now write the completed implementation plan for **GRO-1495: Distribution Packaging** to the path `/home/ubuntu/work/prismatic-engine/specs/implementation-plans/GRO-1495-distribution-plan.md`. 

This plan addresses:
1. Necessary `pyproject.toml` changes (fixing the packaging dependency and correcting package-data).
2. The introduction of a CLI entry point wrapper for the skills CLI in `skills.py`.
3. A detailed, robust `install.sh` script featuring clean OS validations, version staging, sandboxed virtualenv creation, binary wrapper setups (to automatically export environment variables like `PYTHONPATH` and `PRISMATIC_HOME`), and fallback capabilities.
4. A complete step-by-step test procedure simulating a clean install.
5. Concrete sub-tasks structured in a clear progression with a Mermaid diagram.
I will view the first 20 lines of the newly created implementation plan to confirm it has been saved properly and formatted correctly.
I have created the implementation plan for **GRO-1495: Distribution Packaging** and saved it to the requested path: [GRO-1495-distribution-plan.md](file:///home/ubuntu/work/prismatic-engine/specs/implementation-plans/GRO-1495-distribution-plan.md).

### Summary of Work:
1. **Analyzed Existing Packaging & Constraints**: Inspected the current [pyproject.toml](file:///home/ubuntu/work/prismatic-engine/pyproject.toml) and verified CLI entry points in [skills.py](file:///home/ubuntu/work/prismatic-engine/prismatic/skills.py), [dispatcher.py](file:///home/ubuntu/work/prismatic-engine/prismatic/dispatcher.py), [lock.py](file:///home/ubuntu/work/prismatic-engine/prismatic/lock.py), and [admin.py](file:///home/ubuntu/work/prismatic-engine/prismatic/admin.py).
2. **Formulated `pyproject.toml` Corrections**:
   * Identified and added the missing `packaging` dependency (imported by [registry.py](file:///home/ubuntu/work/prismatic-engine/prismatic/core/registry.py)).
   * Corrected `package-data` to include the default configuration folder (`config/*.yaml`).
   * Updated the `prismatic-engine-skills` console script entry point to target a parameterless wrapper function.
3. **Designed Robust Isolated Installer**:
   * Authored the full contents of `install.sh`.
   * The installer implements version-specific directory staging under `$PRISMATIC_HOME/.prismatic/versions/v0.1.0/`, activates a clean virtual environment `venv_stable`, and installs the core runtime.
   * Instead of basic symlinking, the installer creates executable wrappers in `$PRISMATIC_HOME/bin/` that automatically inject the necessary environment variables (`PRISMATIC_HOME`, `PYTHONPATH`, etc.) to secure runtime isolation.
4. **Outlined a Comprehensive Test Procedure**:
   * Defined steps for starting from a clean shell, running the installer, verifying entry point execution, and validating the event loop with `prismatic-engine serve --once`.
5. **Created Concrete Sub-tasks**:
   * Structured the project into 4 actionable sub-tasks complete with a Mermaid dependencies flowchart and concrete deliverables.
