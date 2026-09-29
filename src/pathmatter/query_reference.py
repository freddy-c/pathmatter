"""Query language reference exposed in the MCP tool description."""

QUERY_LANGUAGE_REFERENCE = """Query Markdown documents in the configured vault. Call with
`{"query": { ... }}`; the inner query is a JSON object. `{}` lists all documents.
Only these keys are supported:

- `path`: whole vault-relative path pattern using `/`. Literal text matches exactly;
  `*` matches zero or more characters within one segment; `**` as its own segment
  matches zero or more directories; `{name}` captures one entire segment. For
  example, `modules/{module}/tasks/*.md` captures `module`, while
  `**/tasks/*.md` finds task files at any depth. Include `.md` in the pattern.
- `scope`: `{"directory":"modules/x","mode":"children"}` searches Markdown
  files directly in that directory. `"mode":"descendants"` includes nested files
  and is the default. Omit `scope` to search the whole vault. The directory is
  vault-relative, with no leading slash; `""` means the vault root.
- `where`: non-empty object filtering YAML frontmatter. Fields are frontmatter
  keys; dotted names such as `teacher.name` address nested mappings. Multiple
  fields in one object are ANDed. Use `{"status":"active"}` for equality;
  `{"tags":"math"}` also matches an array containing `"math"`.
- `bodyContains`: case-sensitive literal substring of the Markdown body.
- `sort`: object mapping fields to `1` (ascending) or `-1` (descending), in
  priority order, e.g. `{"due":1,"$path":1}`. Default is ascending path.
- `skip`, `limit`: non-negative integers applied after filtering and sorting.
- `projection`: non-empty object mapping dotted frontmatter or system fields
  to `1` (include) or `0` (exclude). Do not mix inclusion and exclusion except
  for `$path`. `path` and `diagnostics` remain in the response. For example,
  `{"status":1,"$params.module":1}` keeps only those selected values.
- `includeBody`, `includeAbsolutePath`: booleans, both false by default.
  `bodyContains` searches the body without returning it unless `includeBody`
  is true.

`where` field operators: `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`,
`$nin`, `$exists`, `$not`, `$elemMatch`. Examples: `{"priority":{"$gte":2}}`,
`{"tags":{"$in":["math","logic"]}}`, `{"due":{"$exists":true}}`,
`{"steps":{"$elemMatch":{"kind":"read","done":false}}}`. `$elemMatch`
requires the same array item to meet every condition. At the `where` object
level, use `$and` or `$or` with an array of filter objects, or `$not` with one
filter object. At the field level, `$not` takes an operator object, e.g.
`{"status":{"$not":{"$eq":"done"}}}`. `$ne` and `$nin` also match missing
fields; `$exists` distinguishes missing from explicit YAML null. Comparisons
compare numbers with numbers or strings with strings; they do not coerce types.

Location fields work in `where`, `sort`, and `projection`: `$path` (vault-relative
path including `.md`), `$directory`, `$filename`, and `$params.<name>` (a `path`
capture). For example:
`{"query":{"path":"modules/{module}/tasks/*.md","where":{"$params.module":"x","status":{"$ne":"done"}},"sort":{"$path":1},"limit":20}}`.

Results are returned as `{"documents":[...]}`. Each document has `path`,
`params`, `frontmatter`, and `diagnostics`, plus requested `body` or
`absolutePath`. Files with invalid frontmatter remain discoverable by path and
location fields, with parse errors in `diagnostics`; frontmatter predicates do
not match them. Queries skip symlinks and the Pathmatter trash directory."""
