import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pathmatter import QueryError, query_documents, validate_vault


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write(
            "schemas/project.json",
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "required": ["title", "status"],
                    "properties": {
                        "title": {"type": "string", "minLength": 1},
                        "status": {"enum": ["planned", "active", "completed"]},
                        "priority": {"type": "integer", "minimum": 0},
                    },
                }
            ),
        )
        self.write(
            ".pathmatter.yaml",
            "rules:\n"
            "  - match: 'modules/{module}/projects/{project}/project.md'\n"
            "    entity: project\n"
            "    schema: project\n",
        )
        self.write(
            "modules/x/projects/good/project.md",
            "---\ntitle: Good\nstatus: active\npriority: 2\n---\n# Good\n",
        )
        self.write(
            "modules/x/projects/bad/project.md",
            "---\ntitle: ''\nstatus: later\npriority: -1\n---\n# Bad\n",
        )
        self.write(
            "modules/x/projects/missing/project.md",
            "---\ntitle: Missing status\n---\n",
        )
        self.write(
            "modules/x/projects/broken/project.md",
            "---\ntitle: [invalid\n---\nStill searchable\n",
        )
        self.write("modules/x/projects/bad/notes.md", "# Independent note\n")

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_query_includes_schema_diagnostics_without_excluding_invalid_frontmatter(self):
        results = query_documents(
            self.root,
            {
                "path": "modules/{module}/projects/{project}/project.md",
                "where": {"status": "later"},
            },
        )
        self.assertEqual([item["path"] for item in results], ["modules/x/projects/bad/project.md"])
        self.assertEqual(results[0]["params"], {"module": "x", "project": "bad"})
        self.assertEqual(
            {item["field"] for item in results[0]["diagnostics"]},
            {"title", "status", "priority"},
        )
        for diagnostic in results[0]["diagnostics"]:
            self.assertEqual(diagnostic["rule"], "modules/{module}/projects/{project}/project.md")
            self.assertEqual(diagnostic["schema"], "schemas/project.json")
            self.assertEqual(diagnostic["absolutePath"], str(self.root / results[0]["path"]))

    def test_validate_vault_reports_all_violations_and_preserves_parse_errors(self):
        violations = validate_vault(self.root)
        self.assertEqual(
            [item["path"] for item in violations],
            [
                "modules/x/projects/bad/project.md",
                "modules/x/projects/broken/project.md",
                "modules/x/projects/missing/project.md",
            ],
        )
        self.assertEqual(
            [item["code"] for item in violations[1]["diagnostics"]],
            ["frontmatter_parse_error"],
        )
        self.assertEqual(violations[2]["diagnostics"][0]["field"], "status")
        self.assertTrue(all("absolutePath" in item for item in violations))
        self.assertEqual(
            query_documents(self.root, {"path": "**/notes.md"})[0]["diagnostics"], []
        )

    def test_overlapping_rules_all_validate(self):
        self.write(
            "schemas/common.json",
            json.dumps({"type": "object", "required": ["owner"]}),
        )
        self.write(
            ".pathmatter.yaml",
            (self.root / ".pathmatter.yaml").read_text()
            + "  - match: '**/project.md'\n"
            + "    entity: project\n"
            + "    schema: schemas/common.json\n",
        )
        bad = query_documents(self.root, {"path": "**/bad/project.md"})[0]
        self.assertEqual(
            {item["field"] for item in bad["diagnostics"]},
            {"title", "status", "priority", "owner"},
        )

    def test_conflicting_classifications_are_configuration_errors(self):
        self.write(
            ".pathmatter.yaml",
            (self.root / ".pathmatter.yaml").read_text()
            + "  - match: '**/project.md'\n"
            + "    entity: something_else\n",
        )
        with self.assertRaisesRegex(QueryError, "conflicting entity values"):
            validate_vault(self.root)

    def test_invalid_schema_and_unsafe_paths_fail_with_context(self):
        self.write("schemas/project.json", json.dumps({"type": "not-a-type"}))
        with self.assertRaisesRegex(QueryError, "schemas/project.json"):
            validate_vault(self.root)
        self.write("schemas/project.json", json.dumps({"type": "object"}))
        self.write(
            ".pathmatter.yaml",
            "rules:\n  - match: '**/project.md'\n    schema: ../outside.json\n",
        )
        with self.assertRaisesRegex(QueryError, "invalid schema path"):
            validate_vault(self.root)

    def test_local_schema_references_and_unresolved_reference(self):
        self.write(
            "schemas/project.json",
            json.dumps(
                {
                    "type": "object",
                    "$defs": {"title": {"type": "string", "minLength": 1}},
                    "properties": {"title": {"$ref": "#/$defs/title"}},
                }
            ),
        )
        bad = query_documents(self.root, {"path": "**/bad/project.md"})[0]
        self.assertEqual(bad["diagnostics"][0]["field"], "title")
        self.write("schemas/project.json", json.dumps({"$ref": "#/$defs/missing"}))
        with self.assertRaisesRegex(QueryError, "unresolved local schema reference"):
            validate_vault(self.root)

    def test_schema_symlink_is_rejected(self):
        (self.root / "schemas/project.json").unlink()
        outside = Path(self.temp.name).parent / "outside-schema.json"
        (self.root / "schemas/project.json").symlink_to(outside)
        with self.assertRaisesRegex(QueryError, "schema path contains a symlink"):
            validate_vault(self.root)

    def test_cli_validate(self):
        result = subprocess.run(
            [sys.executable, "-m", "pathmatter.cli", "validate", str(self.root)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(json.loads(result.stdout)), 3)


if __name__ == "__main__":
    unittest.main()
