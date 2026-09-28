# Pathmatter

Pathmatter is a Python project for a custom query language over Markdown files and their frontmatter on the filesystem.

The first read-only query interface accepts JSON objects. It selects Markdown files by
vault-relative path, parses YAML frontmatter, and reports parse diagnostics. Files
with invalid frontmatter remain discoverable. Symlinks are skipped.

```sh
pathmatter query ./my-vault --query '{"path":"**/project.md"}'
pathmatter query ./my-vault --query-file query.json
pathmatter query ./my-vault --query-file - < query.json
```

The same interface is available from Python as `query_documents(vault_root, query)`.
Supported keys in this first slice are `path`, `scope`, `includeBody`, and
`includeAbsolutePath`. For example:

```json
{
  "path": "**/tasks/*.md",
  "scope": {"directory": "modules/x", "mode": "descendants"},
  "includeBody": true
}
```

`scope.mode` can be `children` for only immediate Markdown files or `descendants`
for all nested Markdown files (the default). Paths always include `.md` and use `/`
separators. Results are ordered by path and contain `path`, `params`,
`frontmatter`, and `diagnostics`; `body` and `absolutePath` are opt-in. Predicates,
projection, sorting, and writes are planned for later slices.

## Development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). This project targets the latest stable Python 3.14 patch release. From the project root, install Python and create the environment:

```sh
uv python install 3.14
uv sync
```

Run Python inside that environment with `uv run python`. Add project dependencies with `uv add <package>`. The Python package lives in `src/pathmatter`.
