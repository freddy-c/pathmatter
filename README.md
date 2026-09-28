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
Supported keys are `path`, `scope`, `where`, `includeBody`, and
`includeAbsolutePath`. `where` uses a MongoDB-inspired filter subset: equality,
`$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`, `$exists`, `$and`,
`$or`, `$not`, and `$elemMatch`. Dotted paths read nested YAML mappings, while
flat properties continue to work normally. For example:

```json
{
  "path": "**/tasks/*.md",
  "scope": {"directory": "modules/x", "mode": "descendants"},
  "where": {"status": {"$ne": "done"}, "priority": {"$gte": 2}},
  "includeBody": true
}
```

Array values support scalar membership (`{"tags": "math"}`), and `$elemMatch`
requires all nested conditions to match the same array item. `$exists` distinguishes
a missing property from an explicit YAML `null`; `$ne` and `$nin` also match missing
properties. Numeric comparisons do not convert strings to numbers. Location fields
include `$path`, `$directory`, `$filename`, and captured `$params.<name>` values.
Malformed frontmatter remains discoverable by path and can still be filtered by
location, but does not match predicates that require frontmatter.

`scope.mode` can be `children` for only immediate Markdown files or `descendants`
for all nested Markdown files (the default). Paths always include `.md` and use `/`
separators. Results are ordered by path and contain `path`, `params`,
`frontmatter`, and `diagnostics`; `body` and `absolutePath` are opt-in. Projection,
sorting, and writes are planned for later slices.

## Development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). This project targets the latest stable Python 3.14 patch release. From the project root, install Python and create the environment:

```sh
uv python install 3.14
uv sync
```

Run Python inside that environment with `uv run python`. Add project dependencies with `uv add <package>`. The Python package lives in `src/pathmatter`.
