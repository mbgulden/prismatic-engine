"""
Manifest schema validation for Prismatic Engine plugins.
Defines validate_manifest to validate plugin-manifest.yaml structure,
supporting both v1.0.0 and v1.1.0 schemas.
"""

from __future__ import annotations

import logging
from typing import Any
from prismatic.interface.plugin import PluginValidationError

logger = logging.getLogger("prismatic.interface.manifest_schema")


def _is_list_of_strings(val: Any) -> bool:
    if not isinstance(val, list):
        return False
    return all(isinstance(x, str) for x in val)


def validate_manifest(manifest: Any) -> None:
    """
    Validates a plugin manifest dict against the schema constraints.
    Raises PluginValidationError if any validation fails.
    """
    if not isinstance(manifest, dict):
        raise PluginValidationError("Manifest must be a dictionary.")

    # 1. Required fields
    required_fields = ["name", "version", "entry_point", "core_version_constraint"]
    for field in required_fields:
        if field not in manifest:
            raise PluginValidationError(f"Missing required field in manifest: {field}")
        if not isinstance(manifest[field], str):
            raise PluginValidationError(f"Field '{field}' must be a string.")

    name = manifest["name"]
    # Alphanumeric, lowercase, dashes/underscores per spec
    for char in name:
        if not (char.isalnum() and char.islower() or char in "-_"):
            raise PluginValidationError(
                f"Plugin name '{name}' must contain only alphanumeric lowercase characters, dashes, or underscores."
            )

    entry_point = manifest["entry_point"]
    if ":" not in entry_point:
        raise PluginValidationError(
            f"Invalid entry_point '{entry_point}'. Must be in the format 'module:class'."
        )

    # 2. Optional basic fields
    if "schema_version" in manifest and not isinstance(manifest["schema_version"], str):
        raise PluginValidationError("Field 'schema_version' must be a string.")

    if "description" in manifest and not isinstance(manifest["description"], str):
        raise PluginValidationError("Field 'description' must be a string.")

    if "author" in manifest and not isinstance(manifest["author"], str):
        raise PluginValidationError("Field 'author' must be a string.")

    if "hooks" in manifest:
        if not _is_list_of_strings(manifest["hooks"]):
            raise PluginValidationError("Field 'hooks' must be a list of strings.")

    if "required_capabilities" in manifest:
        if not _is_list_of_strings(manifest["required_capabilities"]):
            raise PluginValidationError("Field 'required_capabilities' must be a list of strings.")

    if "blocked_providers" in manifest:
        if not _is_list_of_strings(manifest["blocked_providers"]):
            raise PluginValidationError("Field 'blocked_providers' must be a list of strings.")

    if "provider_constraints" in manifest:
        if not isinstance(manifest["provider_constraints"], dict):
            raise PluginValidationError("Field 'provider_constraints' must be a dictionary.")
        for k, v in manifest["provider_constraints"].items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise PluginValidationError("Field 'provider_constraints' must map strings to strings.")

    for profile in ["hardware_profile", "execution_profile"]:
        if profile in manifest and not isinstance(manifest[profile], str):
            raise PluginValidationError(f"Field '{profile}' must be a string.")

    # 3. Personas validation
    if "personas" in manifest:
        personas = manifest["personas"]
        if not isinstance(personas, list):
            raise PluginValidationError("Field 'personas' must be a list.")
        for i, persona in enumerate(personas):
            if not isinstance(persona, dict):
                raise PluginValidationError(f"Persona at index {i} must be a dictionary.")
            if "id" not in persona or not isinstance(persona["id"], str):
                raise PluginValidationError(f"Persona at index {i} must have a string 'id'.")
            if "displayName" not in persona or not isinstance(persona["displayName"], str):
                raise PluginValidationError(f"Persona at index {i} must have a string 'displayName'.")
            if "systemPrompt" not in persona or not isinstance(persona["systemPrompt"], str):
                raise PluginValidationError(f"Persona at index {i} must have a string 'systemPrompt'.")
            if "defaultAllowedDirectories" in persona:
                if not _is_list_of_strings(persona["defaultAllowedDirectories"]):
                    raise PluginValidationError(
                        f"Persona '{persona['id']}' defaultAllowedDirectories must be a list of strings."
                    )
            if "defaultReadOnlyDirectories" in persona:
                if not _is_list_of_strings(persona["defaultReadOnlyDirectories"]):
                    raise PluginValidationError(
                        f"Persona '{persona['id']}' defaultReadOnlyDirectories must be a list of strings."
                    )
            if "preferredHead" in persona and not isinstance(persona["preferredHead"], str):
                raise PluginValidationError(f"Persona '{persona['id']}' preferredHead must be a string.")
            if "maxActions" in persona and not isinstance(persona["maxActions"], int):
                raise PluginValidationError(f"Persona '{persona['id']}' maxActions must be an integer.")

    # 4. Warns on legacy interactive_mode field
    if "interactive_mode" in manifest:
        logger.warning(
            "Manifest contains legacy field 'interactive_mode' for v1.0 backwards compatibility."
        )

    # 5. New v1.1.0 fields
    # Validates modes: [headless|interactive|both]
    if "modes" in manifest:
        modes = manifest["modes"]
        if not isinstance(modes, str) or modes not in ["headless", "interactive", "both"]:
            raise PluginValidationError("Field 'modes' must be one of: headless, interactive, both")

    # Validates ui: { surfaces, web, chat, interrupt_points, header }
    if "ui" in manifest:
        ui = manifest["ui"]
        if not isinstance(ui, dict):
            raise PluginValidationError("Field 'ui' must be a dictionary.")
        allowed_ui_keys = {"surfaces", "web", "chat", "interrupt_points", "header"}
        extra_keys = set(ui.keys()) - allowed_ui_keys
        if extra_keys:
            raise PluginValidationError(f"Field 'ui' contains invalid keys: {', '.join(extra_keys)}")

        if "surfaces" in ui:
            if not _is_list_of_strings(ui["surfaces"]):
                raise PluginValidationError("Field 'ui.surfaces' must be a list of strings.")
        if "interrupt_points" in ui:
            if not _is_list_of_strings(ui["interrupt_points"]):
                raise PluginValidationError("Field 'ui.interrupt_points' must be a list of strings.")
        for k in ["web", "chat", "header"]:
            if k in ui:
                val = ui[k]
                if not isinstance(val, (bool, dict, str)):
                    raise PluginValidationError(f"Field 'ui.{k}' must be a boolean, dictionary, or string.")

    # Validates permissions: { network, filesystem, secrets, bus }
    if "permissions" in manifest:
        perms = manifest["permissions"]
        if not isinstance(perms, dict):
            raise PluginValidationError("Field 'permissions' must be a dictionary.")
        allowed_perm_keys = {"network", "filesystem", "secrets", "bus"}
        extra_keys = set(perms.keys()) - allowed_perm_keys
        if extra_keys:
            raise PluginValidationError(f"Field 'permissions' contains invalid keys: {', '.join(extra_keys)}")

        for k in allowed_perm_keys:
            if k in perms:
                val = perms[k]
                if not (isinstance(val, (bool, dict, str)) or _is_list_of_strings(val)):
                    raise PluginValidationError(
                        f"Field 'permissions.{k}' must be a boolean, dictionary, string, or list of strings."
                    )

    # Validates dependencies: { pip, plugins, system }
    if "dependencies" in manifest:
        deps = manifest["dependencies"]
        if not isinstance(deps, dict):
            raise PluginValidationError("Field 'dependencies' must be a dictionary.")
        allowed_dep_keys = {"pip", "plugins", "system"}
        extra_keys = set(deps.keys()) - allowed_dep_keys
        if extra_keys:
            raise PluginValidationError(f"Field 'dependencies' contains invalid keys: {', '.join(extra_keys)}")

        for k in allowed_dep_keys:
            if k in deps:
                if not _is_list_of_strings(deps[k]):
                    raise PluginValidationError(f"Field 'dependencies.{k}' must be a list of strings.")

    # Validates events_published/subscribed: List[str]
    for ev_field in ["events_published", "events_subscribed"]:
        if ev_field in manifest:
            if not _is_list_of_strings(manifest[ev_field]):
                raise PluginValidationError(f"Field '{ev_field}' must be a list of strings.")
