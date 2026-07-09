from plugins.pwp.theme_task_generation import (
    DISPATCH_READY_LABEL,
    ThemeTaskGenerationError,
    ThemeTaskSpec,
    generate_theme_task_inputs,
    labels_for_theme_task,
    linear_issue_input,
)


def test_generated_theme_task_gets_one_lane_agent_label_without_dispatch_by_default():
    spec = ThemeTaskSpec(
        title="Implement trust-light Astro module fixture",
        lane="astro-implementation",
        phase="9.3",
        parent_identifier="GRO-3735",
        dependency_identifiers=("GRO-3685",),
        contracts=("plugins/pwp/schemas/pwp-module.schema.json",),
        files=("plugins/pwp/themes/trust-light/src/components/Hero.astro",),
        verification_commands=("pytest plugins/pwp/tests/test_module_contracts.py -q",),
    )

    payload = linear_issue_input(spec)

    assert payload["priority"] == 2
    assert payload["parentIdentifier"] == "GRO-3735"
    assert payload["dependencyIdentifiers"] == ("GRO-3685",)
    assert payload["dispatchHeld"] is True
    assert "agent:ned-code" in payload["labels"]
    assert DISPATCH_READY_LABEL not in payload["labels"]
    assert [label for label in payload["labels"] if label.startswith("agent:")] == [
        "agent:ned-code"
    ]
    assert "Do not add `dispatch:ready`" in payload["description"]
    assert "plugins/pwp/schemas/pwp-module.schema.json" in payload["description"]


def test_dispatch_ready_requires_explicit_build_initiation():
    prebuild = ThemeTaskSpec(
        title="Deploy trust-light preview",
        lane="deploy",
        phase="9.3",
        verification_commands=("pytest tests/test_pwp_cloudflare_deploy.py -q",),
    )
    build_started = ThemeTaskSpec(
        title="Deploy trust-light preview",
        lane="deploy",
        phase="9.3",
        verification_commands=("pytest tests/test_pwp_cloudflare_deploy.py -q",),
        build_initiated=True,
    )

    assert DISPATCH_READY_LABEL not in labels_for_theme_task(prebuild)
    assert DISPATCH_READY_LABEL in labels_for_theme_task(build_started)
    assert "agent:ned-infra" in labels_for_theme_task(build_started)


def test_generation_rejects_unknown_lanes_and_manual_agent_labels():
    try:
        ThemeTaskSpec(
            title="Bad lane",
            lane="interpretive-css",
            phase="9.3",
            verification_commands=("pytest -q",),
        )
    except ThemeTaskGenerationError as exc:
        assert "unknown PWP theme lane" in str(exc)
    else:
        raise AssertionError("unknown lane should be rejected")

    try:
        ThemeTaskSpec(
            title="Manual agent label",
            lane="reviewer",
            phase="9.3",
            labels=("agent:kai",),
            verification_commands=("pytest -q",),
        )
    except ThemeTaskGenerationError as exc:
        assert "must not include agent" in str(exc)
    else:
        raise AssertionError("manual agent label should be rejected")


def test_batch_generation_rejects_ambiguous_owner_routing():
    specs = [
        ThemeTaskSpec(
            title="Review theme contract",
            lane="reviewer",
            phase="9.3",
            verification_commands=("pytest plugins/pwp/tests/test_theme_task_generation.py -q",),
        ),
        ThemeTaskSpec(
            title="Write SEO schema task",
            lane="seo-schema",
            phase="9.3",
            verification_commands=("pytest plugins/pwp/tests/test_theme_task_generation.py -q",),
        ),
    ]

    payloads = generate_theme_task_inputs(specs)

    assert [payload["labels"][0] for payload in payloads] == [
        "agent:ned-code",
        "agent:kai-content",
    ]
    assert all(DISPATCH_READY_LABEL not in payload["labels"] for payload in payloads)
