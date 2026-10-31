"""Markdown subset for the conversation list: parser and display rows."""

import unittest
from pathlib import Path

from blendercraft_assistant.conversations import Message
from blendercraft_assistant.ui import markdown

FIXTURE = (Path(__file__).parent / "fixtures" / "pernas.md").read_text(encoding="utf-8")


class ParseTest(unittest.TestCase):
    def test_inline_markup_reduced_to_text(self):
        self.assertEqual(markdown.inline("**−23 %** ❌"), "−23 % ❌")
        self.assertEqual(markdown.inline("use `measure` e [docs](https://x)"), "use measure e docs")
        self.assertEqual(markdown.inline("__forte__ e 2*3*4"), "forte e 2*3*4")

    def test_headings(self):
        blocks = markdown.parse("# Um\n### Três ###\n#semespaço")
        self.assertEqual(blocks, [("heading", 1, "Um"), ("heading", 3, "Três"), ("para", "#semespaço")])

    def test_lists(self):
        blocks = markdown.parse("- a\n  - b\n* **c**\n1. um\n2) dois")
        self.assertEqual(
            blocks,
            [
                ("item", 0, "•", "a"),
                ("item", 1, "•", "b"),
                ("item", 0, "•", "c"),
                ("item", 0, "1.", "um"),
                ("item", 0, "2.", "dois"),
            ],
        )

    def test_code_fence_keeps_spacing_and_markup(self):
        blocks = markdown.parse("antes\n```python\n  x = **y**\n\tz\n\n```\ndepois")
        self.assertEqual(
            blocks,
            [
                ("para", "antes"),
                ("code", "  x = **y**"),
                ("code", "    z"),
                ("code", ""),
                ("para", "depois"),
            ],
        )

    def test_rules_and_blanks(self):
        blocks = markdown.parse("a\n\n\n---\n***\n\nb\n\n")
        self.assertEqual(blocks, [("para", "a"), ("blank",), ("rule",), ("rule",), ("blank",), ("para", "b")])

    def test_table(self):
        blocks = markdown.parse("| a | b \\| c |\n|:--|--:|\n| 1 | **2** |\n| só um |\n")
        self.assertEqual(blocks, [("table", ["a", "b | c"], [["1", "2"], ["só um", ""]])])

    def test_pipe_without_separator_is_text(self):
        self.assertEqual(markdown.parse("a | b"), [("para", "a | b")])

    def test_user_fixture(self):
        blocks = markdown.parse(FIXTURE)
        kinds = [b[0] for b in blocks if b[0] != "blank"]
        self.assertEqual(
            kinds,
            ["heading", "para", "table", "para", "table", "rule", "item", "item", "item", "code", "code"],
        )
        self.assertEqual(blocks[0], ("heading", 2, "4. Pernas (4 iguais)"))
        (table, second) = [b for b in blocks if b[0] == "table"]
        header, rows = table[1], table[2]
        self.assertEqual(len(header), 7)
        self.assertEqual(header[5], "Margem")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0][5], "−23 % ❌")
        self.assertEqual(rows[2][5], "+12 % ✅")
        self.assertEqual(second[1], ["Opção", "Custo", "Prazo"])
        self.assertEqual(second[2][1], ["B — seção + PETG", "médio", "2 dias"])
        paras = [b[1] for b in blocks if b[0] == "para"]
        self.assertTrue(paras[0].startswith("Situação atual: as pernas"))
        items = [b for b in blocks if b[0] == "item"]
        self.assertEqual(items[1], ("item", 0, "2.", "Testar com measure e joint_test"))
        self.assertEqual(items[2][1], 1)
        codes = [b[1] for b in blocks if b[0] == "code"]
        self.assertEqual(codes[1], "    # indentação preservada")

    def test_column_fractions(self):
        fractions = markdown.column_fractions(["a", "bbbbbb"], [["cc", "d"]])
        self.assertAlmostEqual(sum(fractions), 1.0)
        self.assertAlmostEqual(fractions[0], 3 / 9)  # short columns get a minimum
        wide = markdown.column_fractions(["x" * 100, "y"], [])
        self.assertAlmostEqual(wide[0], 30 / 33)  # long cells are capped


class RowsTest(unittest.TestCase):
    def rows(self, text, role="assistant", width=600):
        from blendercraft_assistant.ui import chat_list

        return chat_list.flatten([Message(role, text)], "", "", width, 1.0)

    def test_fixture_rows(self):
        rows = self.rows(FIXTURE)
        kinds = [r[0] for r in rows]
        self.assertEqual(kinds[:2], ["CLAUDE_HEAD", "HEADING"])
        self.assertEqual(rows[1][1], "4. Pernas (4 iguais)")
        self.assertEqual(rows[1][3], "2")  # heading level
        self.assertEqual(kinds.count("TABLE_HEAD"), 2)
        self.assertEqual(kinds.count("TABLE"), 6)
        self.assertEqual(kinds.count("RULE"), 1)
        self.assertEqual(kinds.count("CODE"), 2)
        head = rows[kinds.index("TABLE_HEAD")]
        cells = head[1].split(markdown_sep())
        self.assertEqual(len(cells), 7)
        fractions = [float(f) for f in head[3].split(",")]
        self.assertEqual(len(fractions), 7)
        self.assertAlmostEqual(sum(fractions), 1.0, places=3)
        first = rows[kinds.index("TABLE_HEAD") + 1][1].split(markdown_sep())
        self.assertEqual(first[5], "−23 % ❌")
        bullets = [r[1] for r in rows if r[0] == "BULLET"]
        self.assertEqual(bullets[0], "1. Refazer as pernas frontais")
        self.assertTrue(bullets[2].startswith("    • conferir"))
        code = [r[1] for r in rows if r[0] == "CODE"]
        self.assertEqual(code[1], "    # indentação preservada")
        self.assertFalse(any("**" in r[1] for r in rows))

    def test_user_messages_render_tables_too(self):
        rows = self.rows("| a | b |\n|---|---|\n| 1 | 2 |", role="user")
        self.assertEqual([r[0] for r in rows], ["USER_HEAD", "TABLE_HEAD", "TABLE", "END"])

    def test_user_text_lines(self):
        rows = self.rows("linha 1\n\nlinha 2", role="user")
        self.assertEqual([r[0] for r in rows], ["USER_HEAD", "USER", "USER", "USER", "END"])
        self.assertEqual(rows[2][1], "")

    def test_long_bullet_wraps_with_hanging_indent(self):
        rows = self.rows("- " + "palavra " * 30, width=300)
        bullets = [r[1] for r in rows if r[0] == "BULLET"]
        self.assertGreater(len(bullets), 1)
        self.assertTrue(bullets[0].startswith("• "))
        self.assertTrue(all(b.startswith("  ") for b in bullets[1:]))

    def test_long_code_line_is_split_not_rewrapped(self):
        rows = self.rows("```\n" + "a  b" * 40 + "\n```", width=300)
        code = [r[1] for r in rows if r[0] == "CODE"]
        self.assertGreater(len(code), 1)
        self.assertEqual("".join(code), "a  b" * 40)


def markdown_sep():
    from blendercraft_assistant.ui import chat_list

    return chat_list.CELL_SEP


if __name__ == "__main__":
    unittest.main()
