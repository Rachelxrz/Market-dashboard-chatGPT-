#!/usr/bin/env python3
"""Verify that the daily generator keeps and links the newest 30 dated archives."""

import ast
import html
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_daily.py"


def load_history_helpers():
    source = GENERATOR_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {
        "list_history_days",
        "cleanup_old_history",
        "render_history_links",
        "history_page_content",
        "repair_archived_history_links",
    }
    nodes = []
    keep_days = None

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "HISTORY_KEEP_DAYS":
                    keep_days = ast.literal_eval(node.value)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            nodes.append(node)

    if keep_days != 30:
        raise AssertionError(f"HISTORY_KEEP_DAYS must be 30, got {keep_days!r}")

    module = ast.Module(body=nodes, type_ignores=[])
    namespace = {
        "HISTORY_KEEP_DAYS": keep_days,
        "Path": Path,
        "datetime": datetime,
        "html": html,
        "shutil": shutil,
        "log": lambda _message: None,
    }
    exec(compile(module, str(GENERATOR_PATH), "exec"), namespace)
    return namespace


def main():
    helpers = load_history_helpers()

    with tempfile.TemporaryDirectory() as temp_dir:
        history_dir = Path(temp_dir)
        start = date(2026, 1, 1)
        all_days = [(start + timedelta(days=offset)).isoformat() for offset in range(35)]
        for day in all_days:
            (history_dir / day).mkdir()
        (history_dir / "not-a-date").mkdir()

        helpers["HISTORY_DIR"] = history_dir
        expected = sorted(all_days, reverse=True)[:30]

        assert helpers["list_history_days"]() == expected
        rendered_links = helpers["render_history_links"]("reading.html")
        assert rendered_links.count("<a ") == 30
        assert f"./history/{expected[0]}/reading.html" in rendered_links
        assert f"./history/{expected[-1]}/reading.html" in rendered_links

        archived_links = helpers["history_page_content"](rendered_links)
        assert f"../../history/{expected[0]}/reading.html" in archived_links
        assert 'href="./history/' not in archived_links

        existing_page = history_dir / expected[0] / "reading.html"
        existing_page.write_text(rendered_links, encoding="utf-8")
        helpers["repair_archived_history_links"]()
        repaired_page = existing_page.read_text(encoding="utf-8")
        assert f"../../history/{expected[0]}/reading.html" in repaired_page
        assert 'href="./history/' not in repaired_page
        helpers["cleanup_old_history"]()

        remaining = sorted(
            path.name for path in history_dir.iterdir() if path.name != "not-a-date"
        )
        assert remaining == sorted(expected)
        assert (history_dir / "not-a-date").is_dir()

    print("History retention verified: newest 30 dated archives are kept and listed.")


if __name__ == "__main__":
    main()
