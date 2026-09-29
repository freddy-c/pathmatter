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
Supported keys are `path`, `scope`, `where`, `includeBody`,
`includeAbsolutePath`, `projection`, `sort`, `skip`, `limit`, and
`bodyContains`. `where` uses a MongoDB-inspired filter subset: equality,
`$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`, `$exists`, `$and`,
`$or`, `$not`, and `$elemMatch`. Dotted paths read nested YAML mappings, while
flat properties continue to work normally. For example:

```json
{
  "path": "**/tasks/*.md",
  "scope": {"directory": "modules/x", "mode": "descendants"},
  "where": {"status": {"$ne": "done"}, "priority": {"$gte": 2}},
  "sort": {"due": 1, "$path": 1},
  "skip": 0,
  "limit": 20,
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
separators. By default, results are ordered by path and contain `path`, `params`,
`frontmatter`, and `diagnostics`; `body` and `absolutePath` are opt-in. `sort`
uses MongoDB-style `1` and `-1` directions and always uses ascending path order to
break ties unless `$path` is explicitly included. Missing and mixed-type sort
values use a deterministic ascending order: missing, null, boolean, number,
string, array, object. `skip` and `limit` apply after filtering and sorting. `projection` accepts
MongoDB-style inclusion or exclusion fields for frontmatter and system fields;
the result envelope and document path are retained. `bodyContains` performs a
case-sensitive substring search and does not require `includeBody`; it only
returns the body when `includeBody` is also true.

## Path rules and validation

Put `.pathmatter.yaml` at the vault root to apply schemas to document paths.
Rules use the same whole-path patterns as queries. For example:

```yaml
rules:
  - match: "modules/{module}/projects/{project}/project.md"
    entity: project
    schema: project
```

`schema: project` reads `schemas/project.json` from the vault root. A
vault-relative `.json` path also works. Schemas use JSON Schema Draft 2020-12;
local JSON pointer `$ref` values are supported. The rule only applies to the
matching `project.md`, not its sibling notes or assets. Documents without a
matching rule are valid unless they have a parsing error.

All matching schemas are applied. Conflicting `entity` or `template` values
among rules matching a document are configuration errors. Queries include
schema diagnostics with the document path, physical path, rule, schema, field,
and error message. Invalid documents remain queryable by path and parsed
frontmatter; unparseable frontmatter remains queryable by path.

Run `pathmatter validate ./my-vault` to scan Markdown files and print a JSON
array of documents with diagnostics. It exits with status 1 when violations are
found, 0 when the vault passes, and 2 for invalid configuration or arguments.
The Python equivalent is `validate_vault(vault_root)`.

## Creating and updating documents

Create a Markdown document from explicit frontmatter. `body` is optional and
defaults to an empty string; missing parent directories are created after the
proposed document passes its path rules and schemas.

```sh
pathmatter preview ./my-vault --input '{"operation":"create","path":"projects/demo/project.md","frontmatter":{"title":"Demo","status":"planned"}}'
pathmatter create ./my-vault --input '{"path":"projects/demo/project.md","frontmatter":{"title":"Demo","status":"planned"}}'
```

Update frontmatter with MongoDB-style `$set` and `$unset`. Dotted names address
nested properties. The tool reads the current file when the command runs, so
edits made in Obsidian are included. Omitting `body` preserves the Markdown body;
include it only when you intend to replace the body.

```sh
pathmatter preview ./my-vault --input '{"operation":"update","path":"projects/demo/project.md","update":{"$set":{"status":"active"}}}'
pathmatter update ./my-vault --input '{"path":"projects/demo/project.md","update":{"$set":{"status":"active"},"$unset":{"oldField":""}}}'
```

All three commands also accept `--input-file request.json` or
`--input-file -` for standard input. A preview shows the resulting path,
frontmatter, body, and diagnostics without writing. Creates reject occupied
paths; updates require an existing file with parseable frontmatter. Both run
validation before writing. In Python, use `preview_document`,
`create_document`, and `update_document` from `pathmatter` with the same request
objects. Patch operations may reformat YAML frontmatter, while an omitted body
is preserved.

Delete moves a Markdown document into `.pathmatter-trash` inside the vault. The
response includes `trashedPath` so it can be recovered manually. Trash is excluded
from queries and validation, and each deletion has a unique destination.

```sh
pathmatter delete ./my-vault --input '{"path":"projects/demo/project.md"}'
```

The Python equivalent is `delete_document(vault_root, {"path": "..."})`.

## Local note search

Search Markdown bodies with semantic, lexical, or hybrid retrieval. The first
search builds a local index; later searches hash the files being searched and
refresh changed notes. This includes edits made in Obsidian or through
Pathmatter. Whole-vault searches also remove deleted or renamed paths. The
Markdown files remain authoritative, and `index --rebuild` recreates their
search entries. The local MiniLM embedding model downloads on first use.

```sh
pathmatter search ./my-vault --text "storage decision" --mode hybrid
pathmatter search ./my-vault --text "storage" --mode lexical --scope '{"path":"projects/*.md","where":{"status":"active"}}' --limit 5
pathmatter index ./my-vault
pathmatter index ./my-vault --rebuild
```

An MCP agent can call `search_notes` with the same `text`, `mode`, optional
`scope` query object, and `limit` (1–20). The scope accepts `path`, `scope`,
`where`, and `bodyContains` from the query language above. Pathmatter resolves
it first; retrieval is then restricted to those exact vault-relative paths.
With no scope, the whole vault is searched. For example:

```json
{"text":"storage decision","mode":"hybrid","scope":{"path":"projects/*.md","where":{"status":"active"}},"limit":5}
```

Each result has `path`, `heading`, `bodyStartLine`, `bodyEndLine`, `excerpt`,
`retrievalMode`, and `distance` (semantic) or `score` (lexical or hybrid).
Lines are 1-based within the Markdown body, after frontmatter. Chunks are
packed from consecutive Markdown blocks with `semantic-text-splitter`; short
notes stay in one chunk. Hybrid uses reciprocal rank fusion of semantic and
SQLite FTS5 lexical rankings. Scores from different modes are not directly
comparable. The rebuildable index lives in the user cache directory outside
the vault (`~/Library/Caches/pathmatter` on macOS, `$XDG_CACHE_HOME/pathmatter`
or `~/.cache/pathmatter` elsewhere). Set `PATHMATTER_INDEX_HOME` to move it.

## Local ChatGPT desktop MCP server

The `pathmatter-mcp` command starts a local STDIO server bound to one vault. It
exposes `query_documents`, `get_document`, `search_notes`, `create_document`,
`update_document`, and `delete_document`. The vault path is set when the server starts, not supplied
by tool calls. Deletes move files to the recoverable trash described above.
The `query_documents` tool description includes the query language reference and
examples, so MCP clients receive the syntax when they list the available tools.

```sh
uv sync
.venv/bin/pathmatter-mcp /absolute/path/to/my-vault
```

The command waits for an MCP client on standard input; run it from ChatGPT
desktop rather than typing into it directly. In **Settings → MCP servers**, add a
server named `pathmatter`, choose **STDIO**, set the command to this project's
absolute `.venv/bin/pathmatter-mcp` path, and pass the vault's absolute path as
its argument. Save and restart the server. Type `/mcp` in the composer to check
that the six tools are available.

For explicit approval of write tools, configure the same server in
`~/.codex/config.toml` with `default_tools_approval_mode = "writes"`:

```toml
[mcp_servers.pathmatter]
command = "/absolute/path/to/pathmatter/.venv/bin/pathmatter-mcp"
args = ["/absolute/path/to/my-vault"]
default_tools_approval_mode = "writes"
```

The server is local to the computer running ChatGPT desktop. A remote ChatGPT
connection would require a reachable MCP endpoint or a private tunnel and
appropriate authentication; the CRUD operations can remain the same.

## Development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). This project targets Python 3.13. From the project root, install Python and create the environment:

```sh
uv python install 3.13
uv sync
```

Run Python inside that environment with `uv run python`. Add project dependencies with `uv add <package>`. The Python package lives in `src/pathmatter`.
