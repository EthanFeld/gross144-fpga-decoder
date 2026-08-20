"""Canonical graph representation shared by compiler and reference models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .matrix_io import Matrix, validate_matrix


class GraphValidationError(ValueError):
    def __init__(self, issues: Sequence[str]):
        self.issues = tuple(issues)
        super().__init__("; ".join(self.issues))


@dataclass(frozen=True)
class Variable:
    id: int
    prior_class: int = 0


@dataclass(frozen=True)
class Check:
    id: int
    neighbors: tuple[int, ...]
    check_type: str = "static"
    edge_offset: int = 0
    sign_offset: int = 0
    flags: int = 0


@dataclass(frozen=True)
class LogicalActionMetadata:
    seed_logicals: tuple[tuple[int, ...], ...] = ()
    logical_classes: tuple[int, ...] = ()


@dataclass(frozen=True)
class GroupActionMetadata:
    variable_permutations: tuple[tuple[int, ...], ...] = ()
    check_permutations: tuple[tuple[int, ...], ...] = ()


@dataclass(frozen=True)
class Graph:
    num_variables: int
    checks: tuple[Check, ...]
    variables: tuple[Variable, ...] = ()
    syndrome: tuple[int, ...] = ()
    logical: LogicalActionMetadata = field(default_factory=LogicalActionMetadata)
    group: GroupActionMetadata = field(default_factory=GroupActionMetadata)

    def __post_init__(self) -> None:
        if not self.variables:
            object.__setattr__(self, "variables",
                               tuple(Variable(index) for index in range(self.num_variables)))
        if not self.syndrome:
            object.__setattr__(self, "syndrome", tuple(0 for _ in self.checks))
        issues = self.validation_issues()
        structural = [issue for issue in issues if "disconnected" not in issue
                       and "zero degree" not in issue]
        if structural:
            raise GraphValidationError(structural)

    @classmethod
    def from_neighbors(
        cls,
        num_variables: int,
        neighbors: Sequence[Sequence[int]],
        *,
        prior_classes: Sequence[int] | None = None,
        syndrome: Sequence[int] | None = None,
        check_types: Sequence[str] | None = None,
    ) -> "Graph":
        if num_variables < 1:
            raise ValueError("num_variables must be positive")
        if prior_classes is None:
            prior_classes = [0] * num_variables
        if len(prior_classes) != num_variables:
            raise ValueError("prior_classes length must equal num_variables")
        if any(not 0 <= int(value) < 16 for value in prior_classes):
            raise ValueError("prior classes must be 4-bit indices")
        if syndrome is None:
            syndrome = [0] * len(neighbors)
        if len(syndrome) != len(neighbors) or any(int(bit) not in (0, 1) for bit in syndrome):
            raise ValueError("syndrome must contain one binary bit per check")
        if check_types is None:
            check_types = ["static"] * len(neighbors)
        if len(check_types) != len(neighbors):
            raise ValueError("check_types length must equal check count")
        checks: list[Check] = []
        edge_offset = 0
        for check_id, raw_neighbors in enumerate(neighbors):
            stable = tuple(int(value) for value in raw_neighbors)
            if len(set(stable)) != len(stable):
                raise ValueError(f"check {check_id} has duplicate neighbors")
            if any(value < 0 or value >= num_variables for value in stable):
                raise ValueError(f"check {check_id} has out-of-range neighbor")
            checks.append(Check(check_id, stable, str(check_types[check_id]), edge_offset, edge_offset))
            edge_offset += len(stable)
        return cls(
            num_variables=num_variables,
            checks=tuple(checks),
            variables=tuple(Variable(i, int(prior_classes[i])) for i in range(num_variables)),
            syndrome=tuple(int(bit) for bit in syndrome),
        )

    @classmethod
    def from_matrix(cls, matrix: Sequence[Sequence[int]]) -> "Graph":
        matrix = validate_matrix(matrix)
        neighbors = [[column for column, bit in enumerate(row) if bit]
                     for row in matrix]
        return cls.from_neighbors(len(matrix[0]), neighbors)

    def to_matrix(self) -> Matrix:
        return tuple(tuple(int(column in check.neighbors) for column in range(self.num_variables))
                     for check in self.checks)

    def validation_issues(self) -> tuple[str, ...]:
        issues: list[str] = []
        if len(self.variables) != self.num_variables:
            issues.append("variable table length mismatch")
        if len(self.syndrome) != len(self.checks):
            issues.append("syndrome length mismatch")
        for check_id, check in enumerate(self.checks):
            if check.id != check_id:
                issues.append(f"check id mismatch at index {check_id}")
            if len(set(check.neighbors)) != len(check.neighbors):
                issues.append(f"check {check_id} has duplicate neighbors")
            if any(value < 0 or value >= self.num_variables for value in check.neighbors):
                issues.append(f"check {check_id} has out-of-range neighbor")
            if not check.neighbors:
                issues.append(f"check {check_id} has zero degree")
        connected = {variable for check in self.checks for variable in check.neighbors}
        issues.extend(f"variable {variable} disconnected"
                      for variable in range(self.num_variables) if variable not in connected)
        return tuple(issues)

    def assert_valid(self) -> None:
        issues = self.validation_issues()
        if issues:
            raise GraphValidationError(issues)

    def edge_count(self) -> int:
        return sum(len(check.neighbors) for check in self.checks)

    def preserves_graph(
        self,
        variable_permutation: Sequence[int],
        check_permutation: Sequence[int] | None = None,
    ) -> bool:
        _validate_permutation(variable_permutation, self.num_variables, "variable")
        if check_permutation is None:
            check_permutation = tuple(range(len(self.checks)))
        _validate_permutation(check_permutation, len(self.checks), "check")
        for old_check in self.checks:
            new_id = check_permutation[old_check.id]
            expected = sorted(variable_permutation[var] for var in old_check.neighbors)
            actual = sorted(self.checks[new_id].neighbors)
            if expected != actual:
                return False
        return True


def _validate_permutation(permutation: Sequence[int], size: int, label: str) -> None:
    values = tuple(int(value) for value in permutation)
    if len(values) != size or set(values) != set(range(size)):
        raise ValueError(f"{label} permutation must contain each ID 0..{size - 1} once")
