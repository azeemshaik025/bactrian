import ast
from dataclasses import dataclass


class PlanError(Exception):
    pass


class PolicyViolation(Exception):
    pass


@dataclass(frozen=True)
class Tagged:
    value: object
    sources: frozenset


USER = frozenset({"user"})

ALLOWED_NODES = (ast.Module, ast.Assign, ast.Expr, ast.Call, ast.Name, ast.Load, ast.Store, ast.Constant, ast.List)


class Interpreter:
    def __init__(self, tools, policies):
        self.tools = tools
        self.policies = policies

    def check(self, source):
        tree = ast.parse(source)
        for node in ast.walk(tree):
            where = f"line {getattr(node, 'lineno', '?')}"
            if not isinstance(node, ALLOWED_NODES):
                raise PlanError(f"{where}: {type(node).__name__} is not allowed")
            if isinstance(node, ast.Assign) and not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                raise PlanError(f"{where}: assign to one plain name")
            if isinstance(node, ast.Expr) and not isinstance(node.value, ast.Call):
                raise PlanError(f"{where}: a bare expression must be a call")
            if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in self.tools):
                raise PlanError(f"{where}: only these functions can be called: {', '.join(self.tools)}")
            if isinstance(node, ast.Constant) and not isinstance(node.value, str):
                raise PlanError(f"{where}: the only constants are strings")
        return tree

    def run(self, source):
        names = {}
        for statement in self.check(source).body:
            value = self.expr(statement.value, names)
            if isinstance(statement, ast.Assign):
                names[statement.targets[0].id] = value

    def expr(self, e, names):
        if isinstance(e, ast.Constant):
            return Tagged(e.value, USER)
        if isinstance(e, ast.Name):
            if e.id not in names:
                raise PlanError(f"line {e.lineno}: unknown name {e.id}")
            return names[e.id]
        if isinstance(e, ast.List):
            items = [self.expr(item, names) for item in e.elts]
            return Tagged([item.value for item in items], frozenset().union(*(item.sources for item in items)))
        args = [self.expr(a, names) for a in e.args]
        for policy in self.policies.get(e.func.id, []):
            policy(*args)
        return self.tools[e.func.id](*args)
