import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pathmatter.mcp_server import build_server


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_stdio_crud_and_tool_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = build_server(root)
            tools = {tool.name: tool for tool in await server.list_tools()}
            self.assertEqual(
                set(tools),
                {
                    "query_documents",
                    "get_document",
                    "create_document",
                    "update_document",
                    "delete_document",
                },
            )
            self.assertTrue(tools["get_document"].annotations.readOnlyHint)
            self.assertFalse(tools["create_document"].annotations.readOnlyHint)
            self.assertTrue(tools["delete_document"].annotations.destructiveHint)

            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "pathmatter.mcp_server", str(root)],
            )
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    listed = {tool.name for tool in (await session.list_tools()).tools}
                    self.assertEqual(listed, set(tools))

                    created = await session.call_tool(
                        "create_document",
                        {"path": "projects/demo.md", "frontmatter": {"title": "Demo"}, "body": "First\n"},
                    )
                    self.assertFalse(created.isError)
                    self.assertEqual(created.structuredContent["path"], "projects/demo.md")

                    queried = await session.call_tool(
                        "query_documents", {"query": {"where": {"title": "Demo"}}}
                    )
                    self.assertEqual(
                        [item["path"] for item in queried.structuredContent["documents"]],
                        ["projects/demo.md"],
                    )

                    updated = await session.call_tool(
                        "update_document",
                        {"path": "projects/demo.md", "update": {"$set": {"status": "active"}}},
                    )
                    self.assertFalse(updated.isError)
                    self.assertEqual(updated.structuredContent["body"], "First\n")

                    read = await session.call_tool("get_document", {"path": "projects/demo.md"})
                    self.assertEqual(read.structuredContent["frontmatter"]["status"], "active")
                    self.assertEqual(read.structuredContent["body"], "First\n")

                    deleted = await session.call_tool("delete_document", {"path": "projects/demo.md"})
                    self.assertFalse(deleted.isError)
                    self.assertEqual(
                        (root / deleted.structuredContent["trashedPath"]).read_text(),
                        "---\ntitle: Demo\nstatus: active\n---\nFirst\n",
                    )
                    missing = await session.call_tool("get_document", {"path": "projects/demo.md"})
                    self.assertTrue(missing.isError)

    async def test_rejected_write_returns_validation_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".pathmatter.yaml").write_text(
                "rules:\n  - match: 'project.md'\n    schema: project\n",
                encoding="utf-8",
            )
            (root / "schemas").mkdir()
            (root / "schemas/project.json").write_text(
                '{"type":"object","required":["title"]}', encoding="utf-8"
            )
            server = build_server(root)
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "pathmatter.mcp_server", str(root)],
            )
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    invalid = await session.call_tool(
                        "create_document", {"path": "project.md", "frontmatter": {}}
                    )
                    self.assertTrue(invalid.isError)
                    self.assertIn("title", invalid.content[0].text)
                    self.assertFalse((root / "project.md").exists())

            self.assertEqual(len(await server.list_tools()), 5)
