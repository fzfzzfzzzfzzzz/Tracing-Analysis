from __future__ import annotations

from pathlib import Path

from scripts.check_plain_language import check_links, check_words


def test_plain_language_checker_ignores_code_and_finds_visible_term(tmp_path: Path) -> None:
    path = tmp_path / "说明.md"
    path.write_text("正文 lifecycle\n`lifecycle`\n```text\nlifecycle\n```\n", encoding="utf-8")
    findings = check_words(path)
    assert len(findings) == 1
    assert findings[0].line == 1


def test_markdown_link_checker(tmp_path: Path) -> None:
    target = tmp_path / "存在.md"
    target.write_text("# 存在\n", encoding="utf-8")
    source = tmp_path / "入口.md"
    source.write_text("[好](存在.md)\n[坏](缺少.md)\n", encoding="utf-8")
    findings = check_links(source)
    assert len(findings) == 1
    assert "缺少.md" in findings[0].message

