# Pathmatter

Pathmatter is a Python project for a custom query language over Markdown files and their frontmatter on the filesystem.

The query syntax, indexing approach, and public API are still to be designed.

## Development

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). This project targets the latest stable Python 3.14 patch release. From the project root, install Python and create the environment:

```sh
uv python install 3.14
uv sync
```

Run Python inside that environment with `uv run python`. Add project dependencies with `uv add <package>`. The Python package lives in `src/pathmatter`.
