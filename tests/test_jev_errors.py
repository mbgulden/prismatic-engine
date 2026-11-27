"""Tests for the Jev typed-decision error taxonomy (prismatic/jev/errors.py).

Coverage audit (first-hand, 2026-11-26, before this file was written)
-----------------------------------------------------------------------
Branch base: origin/main @ a92eab12 (local head 8e002886; prismatic/jev/errors.py
is byte-identical on both — verified with ``git show origin/main:... | diff``).

No dedicated errors-module test exists on the branch base:

- ``git ls-tree -r --name-only origin/main -- tests/ | grep -iE 'error|exception|jev'``
  returns the 17 test_jev_*.py module tests but no test_jev_errors.py.
- ``git grep -l 'jev.errors' origin/main -- tests/`` → zero hits: no test file
  imports the errors module directly as the module under test.
- The 7 classes are imported *incidentally* from other jev test modules, each
  exercising at most 2–3 of the 7 classes as a dependency of a behavior
  under test, never the taxonomy itself:
    - DecisionError            — test_jev_backends, test_jev_client, test_jev_config,
                                 test_jev_gates, test_jev_maybe_decide, test_jev_prompts
    - NoBackendError           — test_jev_backends, test_jev_client
    - MissingCredentialError   — test_jev_backends
    - SchemaViolationError     — test_jev_questions
    - TransportError           — test_jev_budgets, test_jev_maybe_decide,
                                 test_jev_resilience
    - CircuitOpenError         — none (zero imports anywhere in tests/)
    - BudgetExceededError      — test_jev_budgets (imported, exercised as a
                                 budget-exceeded behavior; the class's own
                                 construction/identity is not asserted)

Conclusion: AC1 coverage is ABSENT as a dedicated taxonomy suite. This file
provides it: construction, base-class identity, and raise/catch behavior for
all 7 exception classes, plus a module-level export-surface guard so a future
rename of any of the 7 public names fails here.

Constructor conventions (read first-hand from the module):
- 5 of the 7 (DecisionError, NoBackendError, SchemaViolationError,
  CircuitOpenError, BudgetExceededError) construct with no arguments.
- MissingCredentialError REQUIRES env_var (the only field it carries).
- TransportError REQUIRES message; its four classification fields
  (status/transient/retry_after_s/trip_breaker) are keyword-only.
  (Their required-argument behavior is pinned in
  test_missing_credential_requires_env_var_name and
  test_transport_error_keyword_only_classification_fields.)

Conventions followed (read first-hand from tests/): flat tests/test_*.py under
the root tests/ package (tests/__init__.py makes it a regular package),
``from prismatic.jev.errors import ...`` direct-import style, docstring-led
modules, no fixture needed (the autouse gateway fixtures in tests/conftest.py
are inert for non-gateway tests).
"""

from __future__ import annotations

import pytest

from prismatic.jev import errors
from prismatic.jev.errors import (
    BudgetExceededError,
    CircuitOpenError,
    DecisionError,
    MissingCredentialError,
    NoBackendError,
    SchemaViolationError,
    TransportError,
)


# ---------------------------------------------------------------------------
# Class inventory
# ---------------------------------------------------------------------------

ALL_EXCEPTION_CLASSES = (
    NoBackendError,
    MissingCredentialError,
    SchemaViolationError,
    TransportError,
    CircuitOpenError,
    BudgetExceededError,
)

ALL_SEVEN_CLASSES = (DecisionError, *ALL_EXCEPTION_CLASSES)

NO_ARG_CLASSES = (
    DecisionError,
    NoBackendError,
    SchemaViolationError,
    CircuitOpenError,
    BudgetExceededError,
)

# The 7 public names the module must keep exporting (base first, then
# subclasses — the module's own import-order convention in __init__).
EXPECTED_PUBLIC_NAMES = (
    "DecisionError",
    "NoBackendError",
    "MissingCredentialError",
    "SchemaViolationError",
    "TransportError",
    "CircuitOpenError",
    "BudgetExceededError",
)


def _construct(cls: type):
    """Construct each class the way its documented signature allows: no-arg
    for the 5 plain classes, the real required argument for the 2 that have
    a custom __init__."""
    if cls is MissingCredentialError:
        return cls("SWARMJEV_API_KEY")
    if cls is TransportError:
        return cls("upstream failure")
    return cls()


# ---------------------------------------------------------------------------
# Module export surface
# ---------------------------------------------------------------------------


def test_module_exports_all_seven_exception_classes() -> None:
    """All 7 taxonomy names must be importable from prismatic.jev.errors.

    Guards against a silent rename of any public name — every caller that
    does ``from prismatic.jev.errors import X`` depends on exactly these 7.
    """
    for name in EXPECTED_PUBLIC_NAMES:
        assert hasattr(errors, name), f"prismatic.jev.errors lost public name {name!r}"


def test_exported_names_are_exception_classes() -> None:
    """Every exported taxonomy name must name a real exception class."""
    for name in EXPECTED_PUBLIC_NAMES:
        obj = getattr(errors, name)
        assert isinstance(obj, type), f"{name!r} is not a class"
        assert issubclass(obj, BaseException), f"{name!r} is not an exception class"


# ---------------------------------------------------------------------------
# Base-class identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", ALL_SEVEN_CLASSES, ids=lambda c: c.__name__)
def test_every_exception_is_a_decision_error(cls: type) -> None:
    """Every one of the 7 classes derives from DecisionError (or IS it)."""
    assert issubclass(cls, DecisionError), (
        f"{cls.__name__} must subclass DecisionError"
    )


@pytest.mark.parametrize("cls", ALL_EXCEPTION_CLASSES, ids=lambda c: c.__name__)
def test_every_concrete_error_derives_from_exception(cls: type) -> None:
    """Every class is an Exception subclass (plain Exception, not BaseException)."""
    assert issubclass(cls, Exception), (
        f"{cls.__name__} must subclass Exception"
    )


def test_decision_error_is_the_base_not_a_subclass() -> None:
    """DecisionError itself is the root of the taxonomy: its only exception
    base is Exception — it must not itself derive from another taxonomy class.
    A regression that re-parents DecisionError (e.g. to NoBackendError) would
    corrupt the whole tree and this is the class where that would show.
    """
    bases = [b for b in DecisionError.__mro__ if issubclass(b, BaseException)]
    assert bases == [DecisionError, Exception, BaseException], (
        f"DecisionError MRO exception bases changed: {bases}"
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", NO_ARG_CLASSES, ids=lambda c: c.__name__)
def test_plain_construction_with_no_args(cls: type) -> None:
    """The 5 plain classes construct with no arguments and no message."""
    exc = cls()
    assert isinstance(exc, cls)
    assert isinstance(exc, DecisionError)
    assert str(exc) == ""  # no args → no message


def test_missing_credential_requires_env_var_name() -> None:
    """MissingCredentialError.__init__ takes the env var name (positional or
    keyword) and refuses to proceed without it."""
    by_position = MissingCredentialError("SWARMJEV_API_KEY")
    assert by_position.env_var == "SWARMJEV_API_KEY"

    by_keyword = MissingCredentialError(env_var="OPENROUTER_API_KEY")
    assert by_keyword.env_var == "OPENROUTER_API_KEY"

    with pytest.raises(TypeError):
        MissingCredentialError()  # required argument missing


def test_missing_credential_message_carries_name_only_not_value() -> None:
    """The docstring contract: the message carries the env var *name* only.

    We cannot assert the absence of a value we never passed, but we can pin
    the exact message format the module documents — a regression that starts
    echoing a secret would have to change this string.
    """
    exc = MissingCredentialError("SWARMJEV_API_KEY")
    assert (
        str(exc)
        == "missing credential: environment variable SWARMJEV_API_KEY is not set "
        "(backend requires it; refusing to proceed)"
    )


def test_transport_error_keyword_only_classification_fields() -> None:
    """TransportError carries the retry-loop classification: status, transient,
    retry_after_s, trip_breaker. All four are keyword-only after the message."""
    exc = TransportError(
        "upstream timeout",
        status=None,
        transient=True,
        retry_after_s=None,
        trip_breaker=True,
    )
    assert str(exc) == "upstream timeout"
    assert exc.status is None
    assert exc.transient is True
    assert exc.retry_after_s is None
    assert exc.trip_breaker is True


def test_transport_error_defaults_are_none_and_false() -> None:
    """Defaults: message required, all four classification fields default to
    None/False (no classification asserted)."""
    exc = TransportError("boom")
    assert exc.status is None
    assert exc.transient is False
    assert exc.retry_after_s is None
    assert exc.trip_breaker is False


def test_transport_error_classification_is_keyword_only() -> None:
    """The classification fields are keyword-only (``*`` in the signature).
    Positional passing must fail — callers that pass them positionally are
    relying on an undocumented order and would silently mislabel a 5xx as
    throttled if the signature ever drifted.
    """
    with pytest.raises(TypeError):
        TransportError("boom", None, True)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# raise / catch behavior
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", ALL_SEVEN_CLASSES, ids=lambda c: c.__name__)
def test_raise_and_catch_as_decision_error(cls: type) -> None:
    """Catch-all contract: any of the 7 raised is caught as DecisionError.
    This is the pattern every Jev call site relies on — 'callers either get a
    validated answer or one of these' — so a class that escaped the taxonomy
    (e.g. re-parented to bare Exception) would break every on_error handler.
    """
    with pytest.raises(DecisionError) as exc_info:
        raise _construct(cls)
    assert exc_info.value is not None
    assert isinstance(exc_info.value, cls)


def test_catch_individual_class_catches_only_its_own_type() -> None:
    """catch(NoBackendError) must not swallow a sibling MissingCredentialError.
    Sibling isolation is what lets the fallback backend distinguish 'no
    network, made no decision' from 'backend selected but key unset'."""
    with pytest.raises(MissingCredentialError):
        try:
            try:
                raise MissingCredentialError("SWARMJEV_API_KEY")
            except NoBackendError:
                pass  # must NOT catch the sibling
        except MissingCredentialError:
            raise
    # (If the inner except had caught it, the outer raise never fires and
    # pytest.raises fails the test.)


def test_catch_base_does_not_catch_unrelated_exceptions() -> None:
    """DecisionError must not leak: catching it must not catch ValueError,
    and raising ValueError must not satisfy a DecisionError handler."""
    caught = []

    try:
        raise ValueError("not a jev error")
    except DecisionError:
        caught.append("decision")
    except ValueError:
        caught.append("value")

    assert caught == ["value"]


def test_missing_credential_catch_preserves_env_var_field() -> None:
    """A handler catching MissingCredentialError must be able to read
    exc.env_var — that is the field callers (config diagnostics) print."""
    try:
        raise MissingCredentialError("MY_BACKEND_KEY")
    except MissingCredentialError as exc:
        assert exc.env_var == "MY_BACKEND_KEY"
        assert isinstance(exc, DecisionError)
    else:
        raise AssertionError("MissingCredentialError was not raised")


def test_transport_error_catch_preserves_classification() -> None:
    """A retry-loop handler catching TransportError must see all four
    classification fields intact after the catch."""
    try:
        raise TransportError(
            "HTTP 503", status=503, transient=True, retry_after_s=2.5, trip_breaker=True
        )
    except TransportError as exc:
        assert exc.status == 503
        assert exc.transient is True
        assert exc.retry_after_s == 2.5
        assert exc.trip_breaker is True
    else:
        raise AssertionError("TransportError was not raised")


def test_each_class_is_its_own_type_not_another_tax_class() -> None:
    """type() identity: each constructed instance has exactly its own class
    as its type, and no concrete class is an instance of a sibling class.
    Catches the 'wrong base class' plant: e.g. if CircuitOpenError were
    accidentally defined as a subclass of TransportError, the sibling
    non-instantiation below fails.
    """
    for cls in ALL_SEVEN_CLASSES:
        inst = _construct(cls)
        assert type(inst) is cls, f"type of {cls.__name__} instance != {cls.__name__}"
    # Sibling non-instantiation (all concrete classes are distinct types).
    assert not isinstance(NoBackendError(), CircuitOpenError)
    assert not isinstance(CircuitOpenError(), BudgetExceededError)
    assert not isinstance(TransportError("x"), MissingCredentialError)
    assert not isinstance(SchemaViolationError(), NoBackendError)
    assert not isinstance(BudgetExceededError(), SchemaViolationError)


def test_documented_semantics_of_each_class() -> None:
    """Pin the first docstring line of each class so a docstring/semantics
    swap (e.g. CircuitOpenError becoming a retryable transport error) is caught
    by a human-readable diff here. These mirror the module docstrings verbatim.
    """
    expected = {
        DecisionError: "Base error for every swarmjev failure: transport, schema, config.",
        NoBackendError: "Raised by the fallback backend: no network access, no decision made.",
        MissingCredentialError: (
            "A network backend was selected but its API key env var is unset."
        ),
        SchemaViolationError: "A backend returned a malformed or partial answer payload.",
        TransportError: "An HTTP/transport failure talking to a network backend.",
        CircuitOpenError: "The circuit breaker is open: fail fast to the deterministic path.",
        BudgetExceededError: "A per-call cost or latency budget was exceeded.",
    }
    for cls, want in expected.items():
        doc = (cls.__doc__ or "").strip()
        first_line = doc.splitlines()[0] if doc else ""
        assert first_line == want, (
            f"{cls.__name__} first docstring line changed: {first_line!r} != {want!r}"
        )
