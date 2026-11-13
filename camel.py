"""A small interpreter for plans written by a model, in the style of CaMeL.

Every value remembers where it came from, and policies check that before a tool runs.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from typing import Callable


class PlanError(Exception):
    """The plan uses something the interpreter does not allow."""


class PolicyError(Exception):
    """A tool would use a value that came from somewhere it must not trust."""


class ToolError(Exception):
    """A tool failed, with a reason that is safe for the planner to read."""


@dataclass(frozen=True)
class Tagged:
    """A value, plus the sources it came from."""

    value: object
    sources: frozenset[str]


USER = frozenset({"user"})
ALLOWED_NODES = (
    ast.Module,
    ast.Assign,
    ast.Expr,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.List,
)


class Interpreter:
    def __init__(self, tools: dict[str, Callable], policies: dict[str, list[Callable]]) -> None:
        self.tools = tools
        self.policies = policies

    def check(self, source: str) -> ast.Module:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            where = f"line {getattr(node, 'lineno', '?')}"
            if not isinstance(node, ALLOWED_NODES):
                raise PlanError(f"{where}: {type(node).__name__} is not allowed")
            if isinstance(node, ast.Assign) and not (
                len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
            ):
                raise PlanError(f"{where}: assign to one plain name")
            if isinstance(node, ast.Expr) and not isinstance(node.value, ast.Call):
                raise PlanError(f"{where}: a bare expression must be a call")
            if isinstance(node, ast.Call):
                if not (isinstance(node.func, ast.Name) and node.func.id in self.tools):
                    raise PlanError(
                        f"{where}: only these functions can be called: {', '.join(self.tools)}"
                    )
                try:
                    inspect.signature(self.tools[node.func.id]).bind(*node.args)
                except TypeError as wrong:
                    raise PlanError(f"{where}: {node.func.id}: {wrong}") from None
            if isinstance(node, ast.Constant) and not isinstance(node.value, str):
                raise PlanError(f"{where}: the only constants are strings")
        return tree

    def run(self, source: str) -> None:
        names: dict[str, Tagged] = {}
        for statement in self.check(source).body:
            value = self.evaluate(statement.value, names)
            if isinstance(statement, ast.Assign):
                names[statement.targets[0].id] = value

    def evaluate(self, node: ast.expr, names: dict[str, Tagged]) -> Tagged:
        if isinstance(node, ast.Constant):
            return Tagged(node.value, USER)
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise PlanError(f"line {node.lineno}: unknown name {node.id}")
            return names[node.id]
        if isinstance(node, ast.List):
            items = [self.evaluate(item, names) for item in node.elts]
            sources = frozenset().union(*(item.sources for item in items))
            return Tagged([item.value for item in items], sources)
        args = [self.evaluate(arg, names) for arg in node.args]
        try:
            for policy in self.policies.get(node.func.id, []):
                policy(*args)
            return self.tools[node.func.id](*args)
        except (PolicyError, ToolError) as stopped:
            raise type(stopped)(f"line {node.lineno}: {stopped}") from None
