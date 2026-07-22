from __future__ import annotations

import ast
from importlib.metadata import distribution
from pathlib import Path

import numpy as np

from reachy_mini_i_spy.runtime import MotionOwner


def _class_methods(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    class_node = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {
        node.name
        for node in class_node.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _module_array(path: Path, name: str) -> np.ndarray:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    )
    assert isinstance(assignment.value, ast.Call)
    return np.asarray(ast.literal_eval(assignment.value.args[0]), dtype=np.float64)


def test_target_sdk_exposes_supported_motor_lifecycle_and_measurement_api() -> None:
    sdk_path = Path(distribution("reachy-mini").locate_file("reachy_mini/reachy_mini.py"))
    methods = _class_methods(sdk_path, "ReachyMini")
    assert {
        "enable_motors",
        "disable_motors",
        "wake_up",
        "goto_sleep",
        "get_current_head_pose",
    } <= methods
    assert np.allclose(MotionOwner.SLEEP_HEAD_POSE, _module_array(sdk_path, "SLEEP_HEAD_POSE"))
