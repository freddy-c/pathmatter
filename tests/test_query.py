import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pathmatter import QueryError, query_documents
from pathmatter.patterns import PathPattern, PatternError


class PatternTests(unittest.TestCase):
    def test_captures_and_globstar(self):
        pattern = PathPattern("**/modules/{module}/projects/{project}/project.md")
        self.assertEqual(
            pattern.match("modules/x/projects/demo/project.md"),
            {"module": "x", "project": "demo"},
        )
        self.assertEqual(
            pattern.match("oxford/modules/x/projects/demo/project.md"),
            {"module": "x", "project": "demo"},
        )
        self.assertIsNone(pattern.match("modules/x/projects/demo/notes.md"))
        self.assertIsNone(pattern.match("modules/x/projects/demo/nested/project.md"))

    def test_tasks_pattern_only_matches_immediate_documents(self):
        pattern = PathPattern("**/tasks/*.md")
        self.assertEqual(pattern.match("tasks/one.md"), {})
        self.assertEqual(pattern.match("x/tasks/two.md"), {})
        self.assertIsNone(pattern.match("x/tasks/nested/three.md"))

    def test_invalid_patterns(self):
        for value in (
            "/a.md",
            "a//b.md",
            "a/../b.md",
            "a/./b.md",
            "a/**x.md",
            "a/**",
            "{x}/{x}.md",
            "{bad-name}.md",
        ):
            with self.subTest(value=value), self.assertRaises(PatternError):
                PathPattern(value)


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write(
            "modules/x/project.md",
            "---\ntitle: Project\ncreated: 2026-09-28\n---\n# Body\n",
        )
        self.write("modules/x/tasks/one.md", "---\nstatus: active\n---\nFirst\n")
        self.write(
            "modules/x/tasks/nested-fields.md",
            "---\ntitle: Nested\nteacher:\n  name: Ada\nscore: 3\ntags: [math, logic]\nsteps:\n  - kind: read\n    done: true\n  - kind: write\n    done: false\nvalues: [1, 2]\nmissingNull: null\n---\nNested body\n",
        )
        self.write("modules/x/tasks/nested/two.md", "No frontmatter\n")
        self.write(
            "modules/x/tasks/bad.md", "---\nstatus: [broken\n---\nStill readable\n"
        )
        self.write("modules/x/tasks/scalar.md", "---\nhello\n---\nText\n")
        self.write("modules/x/tasks/unclosed.md", "---\nstatus: open\n")
        self.write("modules/x/tasks/image.png", "asset")
        self.write("modules/x/tasks.md", "# Same stem\n")
        (self.root / "modules/x/tasks/linked.md").symlink_to(
            self.root / "modules/x/project.md"
        )
        (self.root / "modules/x/linked-dir").symlink_to(
            self.root / "modules/x/tasks", target_is_directory=True
        )

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_path_query_and_document_fields(self):
        result = query_documents(
            self.root,
            {
                "path": "modules/{module}/project.md",
                "includeBody": True,
                "includeAbsolutePath": True,
            },
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["path"], "modules/x/project.md")
        self.assertEqual(result[0]["params"], {"module": "x"})
        self.assertEqual(
            result[0]["frontmatter"], {"title": "Project", "created": "2026-09-28"}
        )
        self.assertEqual(result[0]["body"], "# Body\n")
        self.assertEqual(
            result[0]["absolutePath"], str(self.root / "modules/x/project.md")
        )

    def test_scope_diagnostics_and_symlinks(self):
        result = query_documents(
            self.root,
            {
                "path": "**/tasks/*.md",
                "scope": {"directory": "modules/x/tasks", "mode": "children"},
            },
        )
        self.assertEqual(
            [item["path"] for item in result],
            [
                "modules/x/tasks/bad.md",
                "modules/x/tasks/nested-fields.md",
                "modules/x/tasks/one.md",
                "modules/x/tasks/scalar.md",
                "modules/x/tasks/unclosed.md",
            ],
        )
        self.assertEqual(result[0]["diagnostics"][0]["code"], "frontmatter_parse_error")
        self.assertEqual(result[2]["frontmatter"], {"status": "active"})
        self.assertEqual(result[3]["diagnostics"][0]["code"], "frontmatter_parse_error")
        self.assertEqual(result[4]["diagnostics"][0]["code"], "frontmatter_parse_error")
        self.assertNotIn("body", result[0])

    def test_scope_mode_and_absent_frontmatter(self):
        children = query_documents(
            self.root, {"scope": {"directory": "modules/x", "mode": "children"}}
        )
        self.assertEqual(
            [item["path"] for item in children],
            ["modules/x/project.md", "modules/x/tasks.md"],
        )
        self.assertEqual(children[1]["frontmatter"], {})
        descendants = query_documents(self.root, {"scope": {"directory": "modules/x"}})
        self.assertIn(
            "modules/x/tasks/nested/two.md", [item["path"] for item in descendants]
        )
        self.assertNotIn(
            "modules/x/linked-dir/one.md", [item["path"] for item in descendants]
        )

    def test_rejects_invalid_queries(self):
        for query in (
            {"where": {}},
            {"path": "x//y"},
            {"includeBody": "yes"},
            {"scope": {"directory": "../outside"}},
            {"scope": {"mode": "other"}},
        ):
            with self.subTest(query=query), self.assertRaises(QueryError):
                query_documents(self.root, query)
        with self.assertRaises(QueryError):
            query_documents(self.root, {"scope": {"directory": "modules/x/linked-dir"}})

    def test_mongodb_style_predicates(self):
        def paths(where):
            return [item["path"] for item in query_documents(self.root, {"where": where})]

        target = "modules/x/tasks/nested-fields.md"
        self.assertEqual(paths({"teacher.name": "Ada"}), [target])
        self.assertEqual(paths({"score": {"$gte": 3}}), [target])
        self.assertEqual(paths({"tags": "math"}), [target])
        self.assertEqual(paths({"values": {"$in": [2, 4]}}), [target])
        self.assertEqual(paths({"steps": {"$elemMatch": {"kind": "write", "done": False}}}), [target])
        self.assertEqual(paths({"steps": {"$elemMatch": {"kind": "read", "done": False}}}), [])
        self.assertEqual(paths({"$and": [{"score": {"$gt": 2}}, {"$or": [{"title": "Nested"}, {"title": "Other"}]}]}), [target])
        self.assertNotIn(target, paths({"$not": {"title": "Nested"}}))
        self.assertEqual(paths({"missingNull": None}), [target])
        self.assertEqual(paths({"absent": None}), [])
        self.assertIn(target, paths({"absent": {"$ne": "x"}}))
        self.assertEqual(paths({"score": {"$gte": "3"}}), [])
        self.assertEqual(query_documents(self.root, {"path": "modules/{module}/tasks/nested-fields.md", "where": {"$params.module": "x", "$filename": "nested-fields.md", "$directory": "modules/x/tasks"}})[0]["path"], target)

    def test_bad_frontmatter_only_matches_system_filters(self):
        system = query_documents(self.root, {"path": "**/bad.md", "where": {"$filename": "bad.md"}})
        self.assertEqual(len(system), 1)
        self.assertEqual(query_documents(self.root, {"path": "**/bad.md", "where": {"status": {"$ne": "done"}}}), [])

    def test_rejects_unsupported_filter_shapes(self):
        for where in ({"$wat": []}, {"score": {"$in": "not-an-array"}}, {"$or": {"x": 1}}, {"x": {"$exists": 1}}):
            with self.subTest(where=where), self.assertRaises(QueryError):
                query_documents(self.root, {"where": where})

    def test_cli_json_query(self):
        command = [
            sys.executable,
            "-m",
            "pathmatter.cli",
            "query",
            str(self.root),
            "--query",
            json.dumps({"path": "**/project.md"}),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(
            [item["path"] for item in json.loads(result.stdout)],
            ["modules/x/project.md"],
        )


if __name__ == "__main__":
    unittest.main()
