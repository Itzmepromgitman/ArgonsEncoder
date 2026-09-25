import ast
import importlib
import inspect
from pathlib import Path

from bot.func.encode import _upload_video


def test_upload_retry_metadata_is_an_accepted_upload_parameter():
    assert "created_at" in inspect.signature(_upload_video).parameters


def test_safe_callback_helpers_receive_the_query_object():
    root = Path(__file__).parents[1]
    for relative in ("plugins/start.py", "plugins/settings.py"):
        module_name = relative.removesuffix(".py").replace("/", ".")
        signature = inspect.signature(
            importlib.import_module(module_name)._safe_callback_answer
        )
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "_safe_callback_answer":
                continue
            assert node.args, f"{relative}:{node.lineno} must answer callbacks"
            assert len(node.args) <= 2, (
                f"{relative}:{node.lineno} has too many positional arguments"
            )
            assert isinstance(node.args[0], ast.Name)
            assert node.args[0].id == "callback_query", (
                f"{relative}:{node.lineno} must pass callback_query explicitly"
            )
            if len(node.args) == 2:
                assert not (
                    isinstance(node.args[1], ast.Name)
                    and node.args[1].id == "callback_query"
                ), f"{relative}:{node.lineno} duplicate callback_query"
            keyword_names = [keyword.arg for keyword in node.keywords]
            assert "callback_query" not in keyword_names
            assert set(keyword_names) <= {"text", "show_alert"}
            assert len(keyword_names) == len(set(keyword_names))
            if len(node.args) == 2:
                assert "text" not in keyword_names
            signature.bind(
                *(object() for _ in node.args),
                **{keyword.arg: object() for keyword in node.keywords},
            )
