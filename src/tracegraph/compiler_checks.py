"""Independent invariant checks for GDSC compilation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .decision_state import DecisionStateGraph
from .prompt_bundle import PromptBundle
from .provider_cost import ProtocolClosure, PromptBudget
from .representations import RepresentationCandidate, RepresentationType


@dataclass(frozen=True, slots=True)
class CompilerCheck:
    name: str
    ok: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompilerVerification:
    ok: bool
    checks: tuple[CompilerCheck, ...]

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(error for check in self.checks for error in check.errors)


def check_hard_coverage(
    state: DecisionStateGraph,
    selections: Iterable[RepresentationCandidate],
) -> CompilerCheck:
    covered: set[str] = set()
    for candidate in selections:
        if candidate.representation_type != RepresentationType.OMIT:
            covered.update(candidate.covered_atoms)
    required = {atom.atom_id for atom in state.atoms if atom.hard}
    missing = sorted(required.difference(covered))
    return CompilerCheck(
        "hard_coverage",
        not missing,
        tuple(f"hard atom omitted: {atom_id}" for atom_id in missing),
    )


def check_provenance(
    state: DecisionStateGraph,
    selections: Iterable[RepresentationCandidate],
    *,
    event_ids: set[str],
) -> CompilerCheck:
    atom_ids = {atom.atom_id for atom in state.atoms}
    errors: list[str] = []
    for candidate in selections:
        missing_sources = set(candidate.source_ids).difference(event_ids)
        if missing_sources:
            errors.append(
                f"{candidate.representation_id} missing sources: {sorted(missing_sources)}"
            )
        unknown_coverage = set(candidate.covered_atoms).difference(atom_ids)
        if unknown_coverage:
            errors.append(
                f"{candidate.representation_id} unknown coverage: {sorted(unknown_coverage)}"
            )
    return CompilerCheck("provenance", not errors, tuple(errors))


def check_protocol(closure: ProtocolClosure) -> CompilerCheck:
    return CompilerCheck("protocol", closure.valid, closure.errors)


def check_budget(bundle: PromptBundle, budget: PromptBudget) -> CompilerCheck:
    errors: list[str] = []
    if budget.hard_limit is not None and bundle.serialized_token_cost > budget.hard_limit:
        errors.append(
            f"serialized request {bundle.serialized_token_cost} exceeds hard limit {budget.hard_limit}"
        )
    if (
        budget.soft_limit is not None
        and bundle.serialized_token_cost > budget.soft_limit
        and not bundle.budget_infeasible
    ):
        errors.append(
            f"serialized request {bundle.serialized_token_cost} exceeds unmarked soft limit "
            f"{budget.soft_limit}"
        )
    return CompilerCheck("budget", not errors, tuple(errors))


def verify_compilation(
    *,
    state: DecisionStateGraph,
    selections: Iterable[RepresentationCandidate],
    event_ids: set[str],
    closure: ProtocolClosure,
    bundle: PromptBundle,
    budget: PromptBudget,
) -> CompilerVerification:
    checks = (
        check_hard_coverage(state, selections),
        check_provenance(state, selections, event_ids=event_ids),
        check_protocol(closure),
        check_budget(bundle, budget),
    )
    return CompilerVerification(all(check.ok for check in checks), checks)
