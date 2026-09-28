import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from pathmatter import create_document, update_document
from pathmatter.writes import WriteError, WriteValidationError, preview_document


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".pathmatter.yaml").write_text(
            "rules:\n  - match: 'projects/{project}/project.md'\n    schema: project\n",
            encoding="utf-8",
        )
        (self.root / "schemas").mkdir()
        (self.root / "schemas/project.json").write_text(
            json.dumps({"type": "object", "required": ["title"]}), encoding="utf-8"
        )

    def test_create_preview_validates_without_creating_parent(self):
        preview = preview_document(
            self.root,
            {
                "operation": "create",
                "path": "projects/demo/project.md",
                "frontmatter": {},
                "body": "# Demo\n",
            },
        )
        self.assertEqual(preview["diagnostics"][0]["field"], "title")
        self.assertEqual(preview["absolutePath"], str(self.root / preview["path"]))
        self.assertFalse((self.root / "projects").exists())

    def test_create_preview_reports_occupied_path(self):
        (self.root / "taken.md").write_text("original", encoding="utf-8")
        preview = preview_document(
            self.root,
            {"operation": "create", "path": "taken.md", "frontmatter": {}, "body": ""},
        )
        self.assertEqual(preview["diagnostics"][0]["code"], "occupied_target")

    def test_create_preview_defaults_to_empty_body(self):
        preview = preview_document(
            self.root,
            {"operation": "create", "path": "notes.md", "frontmatter": {"title": "Notes"}},
        )
        self.assertEqual(preview["body"], "")

    def test_preview_rejects_unsafe_paths_and_bad_inputs(self):
        (self.root / "linked").symlink_to(self.root, target_is_directory=True)
        for path in ("../outside.md", "/outside.md", "a//b.md", "asset.png", "linked/a.md", "C:/outside.md"):
            with self.subTest(path=path), self.assertRaises(WriteError):
                preview_document(
                    self.root,
                    {"operation": "create", "path": path, "frontmatter": {}, "body": ""},
                )
        with self.assertRaises(WriteError):
            preview_document(
                self.root,
                {"operation": "create", "path": "a.md", "frontmatter": {"x": object()}, "body": ""},
            )

    def test_create_nested_document_without_body_and_serialize_yaml_safely(self):
        result = create_document(
            self.root,
            {
                "path": "projects/demo/project.md",
                "frontmatter": {"title": "2026-09-28", "nullable": None, "nested": {"x": "a: b"}},
            },
        )
        self.assertEqual(result["body"], "")
        written = (self.root / result["path"]).read_text(encoding="utf-8")
        self.assertEqual(
            yaml.safe_load(written.split("---\n")[1]), result["frontmatter"]
        )
        self.assertTrue(written.endswith("---\n"))

    def test_invalid_create_leaves_vault_unchanged_and_occupied_path_is_preserved(self):
        request = {"path": "projects/demo/project.md", "frontmatter": {}}
        with self.assertRaises(WriteValidationError) as caught:
            create_document(self.root, request)
        self.assertEqual(caught.exception.preview["diagnostics"][0]["field"], "title")
        self.assertFalse((self.root / "projects").exists())
        create_document(self.root, {**request, "frontmatter": {"title": "First"}})
        with self.assertRaises(WriteValidationError) as caught:
            create_document(self.root, {**request, "frontmatter": {"title": "Second"}})
        self.assertEqual(caught.exception.preview["diagnostics"][0]["code"], "occupied_target")
        self.assertIn("First", (self.root / request["path"]).read_text(encoding="utf-8"))

    def test_update_patches_frontmatter_and_preserves_body_when_omitted(self):
        path = "projects/demo/project.md"
        create_document(
            self.root,
            {"path": path, "frontmatter": {"title": "Demo", "nested": {"old": 1}}, "body": "# Body\r\n\r\nText\r\n"},
        )
        request = {
            "path": path,
            "update": {"$set": {"nested.new": "ready", "nullable": None}, "$unset": {"nested.old": ""}},
        }
        preview = preview_document(self.root, {"operation": "update", **request})
        self.assertEqual(preview["body"], "# Body\r\n\r\nText\r\n")
        self.assertEqual(preview["frontmatter"]["nested"], {"new": "ready"})
        result = update_document(self.root, request)
        self.assertEqual(result["body"], preview["body"])
        self.assertTrue((self.root / path).read_bytes().endswith(preview["body"].encode()))
        result = update_document(self.root, {"path": path, "body": "Replacement\n"})
        self.assertEqual(result["body"], "Replacement\n")
        self.assertEqual(result["frontmatter"], preview["frontmatter"])

    def test_update_validation_and_bad_patch_do_not_change_file(self):
        path = "projects/demo/project.md"
        create_document(self.root, {"path": path, "frontmatter": {"title": "Demo"}})
        target = self.root / path
        before = target.read_bytes()
        with self.assertRaises(WriteValidationError):
            update_document(self.root, {"path": path, "update": {"$unset": {"title": ""}}})
        for patch in (
            {"$set": {"x": 1, "x.y": 2}},
            {"$set": {"title.x": 1}},
            {"$pull": {"title": "Demo"}},
            {},
        ):
            with self.subTest(patch=patch), self.assertRaises(WriteError):
                update_document(self.root, {"path": path, "update": patch})
        self.assertEqual(target.read_bytes(), before)

    def test_update_can_repair_schema_invalid_but_parseable_frontmatter(self):
        path = "projects/demo/project.md"
        target = self.root / path
        target.parent.mkdir(parents=True)
        target.write_text("---\nstatus: planned\n---\n# Body\n", encoding="utf-8")
        result = update_document(
            self.root, {"path": path, "update": {"$set": {"title": "Repaired"}}}
        )
        self.assertEqual(result["diagnostics"], [])
        self.assertEqual(result["body"], "# Body\n")

    def test_update_uses_latest_external_edit_and_rejects_bad_yaml(self):
        path = "projects/demo/project.md"
        create_document(self.root, {"path": path, "frontmatter": {"title": "Demo"}})
        target = self.root / path
        target.write_text("---\ntitle: Changed in Obsidian\n---\nNew body\n", encoding="utf-8")
        result = update_document(self.root, {"path": path, "update": {"$set": {"status": "active"}}})
        self.assertEqual(result["frontmatter"]["title"], "Changed in Obsidian")
        self.assertEqual(result["body"], "New body\n")
        target.write_text("---\ntitle: [bad\n---\nBody\n", encoding="utf-8")
        before = target.read_bytes()
        with self.assertRaisesRegex(WriteError, "invalid frontmatter"):
            update_document(self.root, {"path": path, "body": "Other"})
        self.assertEqual(target.read_bytes(), before)

    def test_cli_create_preview_and_update_with_optional_body(self):
        def call(command, request):
            return subprocess.run(
                [sys.executable, "-m", "pathmatter.cli", command, str(self.root), "--input", json.dumps(request)],
                capture_output=True,
                text=True,
            )

        request = {"path": "projects/demo/project.md", "frontmatter": {"title": "Demo"}}
        preview = call("preview", {"operation": "create", **request})
        self.assertEqual(preview.returncode, 0)
        self.assertEqual(json.loads(preview.stdout)["body"], "")
        self.assertFalse((self.root / "projects").exists())
        created = call("create", request)
        self.assertEqual(created.returncode, 0, created.stderr)
        updated = call("update", {"path": request["path"], "update": {"$set": {"status": "active"}}})
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertEqual(json.loads(updated.stdout)["body"], "")
        invalid = call("update", {"path": request["path"], "update": {"$unset": {"title": ""}}})
        self.assertEqual(invalid.returncode, 1)
        self.assertEqual(json.loads(invalid.stdout)["diagnostics"][0]["field"], "title")
