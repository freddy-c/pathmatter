"""Rebuildable local search over authoritative Markdown bodies."""

import fcntl
import hashlib
import os
import re
import sqlite3
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

import chromadb
from chromadb.config import Settings

from .chunking import chunk_markdown
from .documents import read_document
from .query import _iter_markdown_files, query_documents


class SearchError(ValueError):
    """A search request or its local index cannot be served."""


_CHUNKER_VERSION = "1"
_RESULT_LIMIT = 20
_PATH_BATCH = 128


class LocalEmbedder:
    """One reusable instance of Chroma's local MiniLM ONNX embedder."""

    max_tokens = 256

    def __init__(self):
        from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import (
            ONNXMiniLM_L6_V2,
        )

        self._implementation = ONNXMiniLM_L6_V2()
        self.identity = (
            f"onnx-minilm-l6-v2:{ONNXMiniLM_L6_V2._MODEL_SHA256}:"
            f"chromadb-{chromadb.__version__}"
        )
        self._tokenizer = None

    def _load_tokenizer(self):
        if self._tokenizer is None:
            from tokenizers import Tokenizer

            # The embedding function downloads its exact model and tokenizer
            # together. Use an unpadded, untruncated copy to count source text.
            self._implementation(["warmup"])
            model = type(self._implementation)
            tokenizer_path = (
                model.DOWNLOAD_PATH / model.EXTRACTED_FOLDER_NAME / "tokenizer.json"
            )
            self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
            self._tokenizer.no_truncation()
            self._tokenizer.no_padding()
        return self._tokenizer

    def token_count(self, text: str) -> int:
        return len(self._load_tokenizer().encode(text).ids)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vector.tolist() for vector in self._implementation(texts)]


def _cache_root(root: Path) -> Path:
    configured = os.environ.get("PATHMATTER_INDEX_HOME")
    if configured:
        base = Path(configured).expanduser()
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "pathmatter"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "pathmatter"
    return base / hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:20]


def _database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            dirty INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS chunks (
            rowid INTEGER PRIMARY KEY,
            id TEXT NOT NULL UNIQUE,
            path TEXT NOT NULL,
            heading TEXT,
            body_start_line INTEGER NOT NULL,
            body_end_line INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text, content='chunks', content_rowid='rowid', tokenize='unicode61'
        );
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, text) VALUES (new.rowid, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text)
            VALUES ('delete', old.rowid, old.text);
        END;
        """
    )


class SearchIndex:
    def __init__(self, vault_root: str | Path, *, index_dir: Path | None = None, embedder=None):
        root = Path(vault_root)
        if root.is_symlink() or not root.is_dir():
            raise SearchError("vault root must be an existing, non-symlink directory")
        self.root = root.absolute()
        self.embedder = embedder if embedder is not None else LocalEmbedder()
        base = index_dir if index_dir is not None else _cache_root(self.root)
        generation = hashlib.sha256(
            f"{self.embedder.identity}:{_CHUNKER_VERSION}".encode()
        ).hexdigest()[:16]
        self.directory = base / generation
        self.directory.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(self.directory / "chroma"),
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self._client.get_or_create_collection(
            name="body_chunks",
            embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
        )
        self.db = sqlite3.connect(self.directory / "manifest.sqlite")
        self.db.row_factory = sqlite3.Row
        _database(self.db)

    def close(self):
        self.db.close()

    @contextmanager
    def _locked(self):
        with (self.directory / "index.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _replace(self, path: str, raw: bytes | None, digest: str | None):
        self.db.execute(
            "INSERT INTO files(path,sha256,dirty) VALUES (?, '', 1) "
            "ON CONFLICT(path) DO UPDATE SET dirty=1",
            (path,),
        )
        self.db.commit()
        self.collection.delete(where={"path": path})
        with self.db:
            self.db.execute("DELETE FROM chunks WHERE path=?", (path,))
        if raw is None:
            with self.db:
                self.db.execute("DELETE FROM files WHERE path=?", (path,))
            return

        try:
            source = raw.decode("utf-8-sig")
        except UnicodeError as error:
            raise SearchError(f"cannot decode document {path}: {error}") from error
        document = read_document(
            self.root / path,
            path,
            include_body=True,
            include_absolute_path=False,
            source=source,
        )
        chunks = chunk_markdown(
            document["body"], self.embedder.token_count, max_tokens=self.embedder.max_tokens
        )
        rows = []
        for index, chunk in enumerate(chunks):
            chunk_id = hashlib.sha256(
                f"{path}\0{_CHUNKER_VERSION}\0{chunk.start_offset}\0{chunk.end_offset}\0{index}".encode()
            ).hexdigest()
            rows.append((chunk_id, path, chunk.heading, chunk.body_start_line, chunk.body_end_line, chunk.text))
        for offset in range(0, len(rows), 32):
            batch = rows[offset : offset + 32]
            vectors = self.embedder.embed([row[5] for row in batch])
            self.collection.upsert(
                ids=[row[0] for row in batch],
                embeddings=vectors,
                documents=[row[5] for row in batch],
                metadatas=[
                    {
                        "path": row[1],
                        "heading": row[2] or "",
                        "bodyStartLine": row[3],
                        "bodyEndLine": row[4],
                    }
                    for row in batch
                ],
            )
        if hashlib.sha256((self.root / path).read_bytes()).hexdigest() != digest:
            raise SearchError(f"document changed during indexing; retry: {path}")
        with self.db:
            self.db.executemany(
                "INSERT INTO chunks(id,path,heading,body_start_line,body_end_line,text) "
                "VALUES (?,?,?,?,?,?)",
                rows,
            )
            self.db.execute(
                "UPDATE files SET sha256=?, dirty=0 WHERE path=?", (digest, path)
            )

    def reconcile(self, paths: Mapping[str, Path], *, full: bool, rebuild: bool = False) -> dict:
        changed = 0
        removed = 0
        if full:
            indexed = {row[0] for row in self.db.execute("SELECT path FROM files")}
            for path in sorted(indexed - paths.keys()):
                self._replace(path, None, None)
                removed += 1
        for path, physical in sorted(paths.items()):
            try:
                raw = physical.read_bytes()
            except OSError as error:
                raise SearchError(f"cannot read document {path}: {error}") from error
            digest = hashlib.sha256(raw).hexdigest()
            row = self.db.execute(
                "SELECT sha256,dirty FROM files WHERE path=?", (path,)
            ).fetchone()
            if not rebuild and row is not None and row["sha256"] == digest and not row["dirty"]:
                continue
            self._replace(path, raw, digest)
            changed += 1
        return {"indexed": changed, "removed": removed, "documents": len(paths)}

    def _semantic(self, text: str, paths: list[str] | None, count: int) -> list[tuple[str, float]]:
        if not self.collection.count():
            return []
        vector = self.embedder.embed([text])[0]
        batches = [None] if paths is None else [paths[i : i + _PATH_BATCH] for i in range(0, len(paths), _PATH_BATCH)]
        found: dict[str, float] = {}
        for batch in batches:
            where = None if batch is None else (
                {"path": batch[0]} if len(batch) == 1 else {"path": {"$in": batch}}
            )
            result = self.collection.query(
                query_embeddings=[vector],
                n_results=count,
                where=where,
                include=["distances"],
            )
            for chunk_id, distance in zip(result["ids"][0], result["distances"][0]):
                found[chunk_id] = min(found.get(chunk_id, float("inf")), distance)
        return sorted(found.items(), key=lambda item: (item[1], item[0]))[:count]

    def _lexical(self, text: str, paths: list[str] | None, count: int) -> list[tuple[str, float]]:
        terms = list(dict.fromkeys(re.findall(r"\w+", text, flags=re.UNICODE)))
        if not terms:
            return []
        expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        batches = [None] if paths is None else [paths[i : i + _PATH_BATCH] for i in range(0, len(paths), _PATH_BATCH)]
        found: dict[str, float] = {}
        for batch in batches:
            constraint = "" if batch is None else f" AND chunks.path IN ({','.join('?' for _ in batch)})"
            query = (
                "SELECT chunks.id, bm25(chunks_fts) AS rank FROM chunks_fts "
                "JOIN chunks ON chunks.rowid=chunks_fts.rowid "
                "WHERE chunks_fts MATCH ?" + constraint + " ORDER BY rank, chunks.id LIMIT ?"
            )
            args = [expression, *(batch or []), count]
            for row in self.db.execute(query, args):
                found[row["id"]] = min(found.get(row["id"], float("inf")), row["rank"])
        return sorted(found.items(), key=lambda item: (item[1], item[0]))[:count]

    def _ranked(self, text: str, mode: str, paths: list[str] | None, limit: int) -> list[dict]:
        count = min(100, max(20, limit * 5))
        semantic = self._semantic(text, paths, count) if mode in ("semantic", "hybrid") else []
        lexical = self._lexical(text, paths, count) if mode in ("lexical", "hybrid") else []
        if mode == "semantic":
            ranked = [(chunk_id, "distance", distance) for chunk_id, distance in semantic]
        elif mode == "lexical":
            ranked = [(chunk_id, "score", -rank) for chunk_id, rank in lexical]
        else:
            scores: dict[str, float] = {}
            for results in (semantic, lexical):
                for rank, (chunk_id, _) in enumerate(results, 1):
                    scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (60 + rank)
            ranked = [(chunk_id, "score", score) for chunk_id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]
        output = []
        for chunk_id, score_name, score in ranked[:limit]:
            row = self.db.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()
            if row is None:
                raise SearchError("search index is inconsistent; rebuild it")
            output.append(
                {
                    "path": row["path"],
                    "heading": row["heading"],
                    "bodyStartLine": row["body_start_line"],
                    "bodyEndLine": row["body_end_line"],
                    "excerpt": row["text"],
                    "retrievalMode": mode,
                    score_name: score,
                }
            )
        return output

    def search(self, text: str, mode: str, scope: Mapping | None = None, limit: int = 8) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise SearchError("text must be a non-empty string")
        if mode not in ("semantic", "lexical", "hybrid"):
            raise SearchError("mode must be semantic, lexical, or hybrid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _RESULT_LIMIT:
            raise SearchError(f"limit must be between 1 and {_RESULT_LIMIT}")
        if scope is not None and not isinstance(scope, Mapping):
            raise SearchError("scope must be a Pathmatter query object")

        for attempt in range(2):
            with self._locked():
                if scope is None:
                    files = {relative: physical for physical, relative in _iter_markdown_files(self.root, self.root)}
                else:
                    unsupported = set(scope) - {"path", "scope", "where", "bodyContains"}
                    if unsupported:
                        raise SearchError(
                            "search scope supports path, scope, where, and bodyContains"
                        )
                    selected = query_documents(self.root, scope)
                    files = {item["path"]: self.root / item["path"] for item in selected}
                self.reconcile(files, full=scope is None)
                if not files:
                    return {"results": []}
                results = self._ranked(text, mode, None if scope is None else sorted(files), limit)
                try:
                    for result in results:
                        raw = (self.root / result["path"]).read_bytes()
                        digest = hashlib.sha256(raw).hexdigest()
                        indexed = self.db.execute(
                            "SELECT sha256 FROM files WHERE path=?", (result["path"],)
                        ).fetchone()
                        if indexed is None or digest != indexed["sha256"]:
                            raise SearchError("a result changed during search")
                except (OSError, SearchError):
                    if attempt == 0:
                        continue
                    raise SearchError("vault changed during search; retry")
                return {"results": results}
        raise SearchError("vault changed during search; retry")


def search_notes(
    vault_root: str | Path,
    text: str,
    mode: str,
    scope: Mapping | None = None,
    limit: int = 8,
    *,
    index_dir: Path | None = None,
    embedder=None,
) -> dict:
    index = SearchIndex(vault_root, index_dir=index_dir, embedder=embedder)
    try:
        return index.search(text, mode, scope, limit)
    finally:
        index.close()


def index_vault(
    vault_root: str | Path, *, rebuild: bool = False, index_dir: Path | None = None, embedder=None
) -> dict:
    index = SearchIndex(vault_root, index_dir=index_dir, embedder=embedder)
    try:
        with index._locked():
            files = {relative: physical for physical, relative in _iter_markdown_files(index.root, index.root)}
            return index.reconcile(files, full=True, rebuild=rebuild)
    finally:
        index.close()
