import argparse
import configparser
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


DEFAULT_STATE_FILE = ".notebooklm_sync.json"
DEFAULT_NOTEBOOK_PREFIX = "Canvas Sync - "
STATE_VERSION = 1
DEFAULT_EXCLUDED_PARTS = {
    "Reports",
    "__pycache__",
    "temp_canvas_downloads",
    "node_modules",
    "build",
    "dist",
    "submission",
}


@dataclass(frozen=True)
class SourceFile:
    path: Path
    rel_path: str
    course_name: str
    kind: str
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class PlannedSource:
    source: SourceFile
    action: str
    reason: str


def load_storage_root(config_path: Path) -> Path:
    config = configparser.ConfigParser()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    config.read(config_path)
    if not config.has_section("STORAGE") or not config.has_option("STORAGE", "LOCAL_ROOT_DIR"):
        raise ValueError("config.ini is missing [STORAGE] LOCAL_ROOT_DIR")

    raw_root = config.get("STORAGE", "LOCAL_ROOT_DIR").strip()
    if not raw_root:
        raise ValueError("[STORAGE] LOCAL_ROOT_DIR is empty")

    root = Path(raw_root)
    if not root.is_absolute():
        root = config_path.parent / root
    return root.resolve()


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": STATE_VERSION, "notebooks": {}, "files": {}}
    with path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    state.setdefault("version", STATE_VERSION)
    state.setdefault("notebooks", {})
    state.setdefault("files", {})
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_has_part(path: Path, names: Iterable[str]) -> bool:
    lowered = {name.lower() for name in names}
    return any(part.lower() in lowered for part in path.parts)


def short_upload_name(rel_path: str, kind: str) -> str:
    stem = hashlib.sha256(rel_path.replace("\\", "/").encode()).hexdigest()[:12]
    suffix = ".txt" if kind == "markdown" else Path(rel_path).suffix
    return f"{stem}{suffix}"


def notebooklm_title(rel_path: str) -> str:
    max_len = 200
    if len(rel_path) <= max_len:
        return rel_path
    digest = hashlib.sha256(rel_path.encode()).hexdigest()[:8]
    return rel_path[: max_len - 9] + "_" + digest


def is_eligible_source(
    path: Path,
    rel_path: Path,
    include_pdf_extracts: bool = False,
    include_submissions: bool = False,
) -> bool:
    if not path.is_file():
        return False
    if any(part.startswith(".") for part in rel_path.parts):
        return False
    excluded_parts = set(DEFAULT_EXCLUDED_PARTS)
    if include_submissions:
        excluded_parts.discard("submission")
    if _path_has_part(rel_path, excluded_parts):
        return False

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return True
    if suffix != ".md":
        return False
    if not include_pdf_extracts and path.name.lower().endswith("_pdf.md"):
        return False
    return True


def source_kind(path: Path) -> str:
    return "pdf" if path.suffix.lower() == ".pdf" else "markdown"


def discover_sources(
    root: Path,
    course_filter: Optional[str] = None,
    include_pdf_extracts: bool = False,
    include_submissions: bool = False,
) -> List[SourceFile]:
    if not root.exists():
        raise FileNotFoundError(f"LOCAL_ROOT_DIR does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"LOCAL_ROOT_DIR is not a directory: {root}")

    sources: List[SourceFile] = []
    for path in sorted(root.rglob("*")):
        rel_path = path.relative_to(root)
        if not is_eligible_source(
            path,
            rel_path,
            include_pdf_extracts=include_pdf_extracts,
            include_submissions=include_submissions,
        ):
            continue
        course_name = rel_path.parts[0] if len(rel_path.parts) > 1 else root.name
        if course_filter and course_name.casefold() != course_filter.casefold():
            continue
        stat = path.stat()
        sources.append(
            SourceFile(
                path=path,
                rel_path=rel_path.as_posix(),
                course_name=course_name,
                kind=source_kind(path),
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256=hash_file(path),
            )
        )
    return sources


def build_plan(sources: Iterable[SourceFile], state: dict) -> List[PlannedSource]:
    files = state.get("files", {})
    plan: List[PlannedSource] = []
    for source in sources:
        existing = files.get(source.rel_path)
        if not existing:
            plan.append(PlannedSource(source, "new", "not in sync state"))
            continue
        if existing.get("sha256") != source.sha256:
            plan.append(PlannedSource(source, "changed", "content hash changed"))
            continue
        plan.append(PlannedSource(source, "unchanged", "content hash unchanged"))
    return plan


def run_notebooklm(args: List[str]) -> dict:
    command = ["notebooklm", *args, "--json"]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "unknown NotebookLM CLI error"
        raise RuntimeError(f"notebooklm {' '.join(args)} failed: {message}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"NotebookLM returned invalid JSON: {completed.stdout!r}") from error


def get_or_create_notebook(course_name: str, state: dict, notebook_prefix: str) -> str:
    notebooks = state.setdefault("notebooks", {})
    existing = notebooks.get(course_name)
    if existing and existing.get("id"):
        return existing["id"]

    title = f"{notebook_prefix}{course_name}"
    listed = run_notebooklm(["list"])
    for notebook in listed.get("notebooks", []):
        if notebook.get("title") == title and notebook.get("id"):
            notebooks[course_name] = {"id": notebook["id"], "title": title}
            return notebook["id"]

    created = run_notebooklm(["create", title])
    notebook = created.get("notebook", {})
    notebook_id = notebook.get("id")
    if not notebook_id:
        raise RuntimeError(f"NotebookLM create returned no notebook id for {title!r}")
    notebooks[course_name] = {"id": notebook_id, "title": title}
    return notebook_id


def apply_plan(plan: Iterable[PlannedSource], state: dict, state_path: Path, notebook_prefix: str) -> int:
    uploaded = 0
    files = state.setdefault("files", {})
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        
        for item in plan:
            if item.action == "unchanged":
                continue
            
            try:
                notebook_id = get_or_create_notebook(item.source.course_name, state, notebook_prefix)
            except Exception as error:
                print(f"  [ERROR] Failed to get/create notebook for {item.source.course_name}: {error}")
                continue

            # Build a short filesystem-safe temp name and a readable NotebookLM title
            safe_name = short_upload_name(item.source.rel_path, item.source.kind)
            title = notebooklm_title(item.source.rel_path)
            
            upload_path = temp_dir_path / safe_name
            try:
                shutil.copy2(item.source.path, upload_path)
            except OSError as error:
                print(f"  [ERROR] Failed to copy {item.source.rel_path} to temp upload: {error}")
                continue

            try:
                print(f"\nUploading {item.action}: {item.source.rel_path}...")
                
                # Upload the source with a human-readable title
                result = run_notebooklm(["source", "add", str(upload_path), "--notebook", notebook_id, "--title", title])
                source_info = result.get("source", {})
                source_id = source_info.get("id")
                
                if not source_id:
                    print(f"  [ERROR] Upload returned no source ID for: {item.source.rel_path}")
                    continue
                
                # Wait for source to process successfully
                print(f"  Waiting for NotebookLM to process (ID: {source_id})...")
                run_notebooklm(["source", "wait", source_id, "-n", notebook_id, "--timeout", "120"])
                
                # Update state immediately on success
                now = dt.datetime.now(dt.timezone.utc).isoformat()
                files[item.source.rel_path] = {
                    "kind": item.source.kind,
                    "course_name": item.source.course_name,
                    "sha256": item.source.sha256,
                    "size": item.source.size,
                    "mtime_ns": item.source.mtime_ns,
                    "notebook_id": notebook_id,
                    "source_id": source_id,
                    "synced_at": now,
                }
                uploaded += 1
                
                # Save state incrementally
                save_state(state_path, state)
                print(f"  [OK] Successfully synced: {item.source.rel_path}")
                
            except Exception as error:
                print(f"  [ERROR] Failed to sync {item.source.rel_path}: {error}")
                
            finally:
                if upload_path.exists():
                    try:
                        upload_path.unlink()
                    except OSError:
                        pass
                        
    return uploaded


def print_plan(plan: List[PlannedSource], max_items: int = 100) -> None:
    counts = {"new": 0, "changed": 0, "unchanged": 0}
    kind_counts: Dict[str, int] = {"markdown": 0, "pdf": 0}
    for item in plan:
        counts[item.action] = counts.get(item.action, 0) + 1
        if item.action != "unchanged":
            kind_counts[item.source.kind] = kind_counts.get(item.source.kind, 0) + 1

    print("\nNotebookLM Sync Preview")
    print(f"New: {counts.get('new', 0)}")
    print(f"Changed: {counts.get('changed', 0)}")
    print(f"Unchanged: {counts.get('unchanged', 0)}")
    print(f"Markdown to upload: {kind_counts.get('markdown', 0)}")
    print(f"PDFs to upload: {kind_counts.get('pdf', 0)}")

    pending = [item for item in plan if item.action != "unchanged"]
    if pending:
        print("\nPending uploads:")
        for item in pending[:max_items]:
            print(f"  - [{item.action}] {item.source.rel_path} ({item.source.kind})")
        if len(pending) > max_items:
            print(f"  ... {len(pending) - max_items} more pending upload(s) not shown")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track and upload Canvas sync sources to NotebookLM.")
    parser.add_argument("--config", default="config.ini", help="Path to config.ini. Default: config.ini")
    parser.add_argument("--root", default=None, help="Override [STORAGE] LOCAL_ROOT_DIR")
    parser.add_argument("--state", default=DEFAULT_STATE_FILE, help=f"Sync state file. Default: {DEFAULT_STATE_FILE}")
    parser.add_argument("--course", default=None, help="Only sync one course folder by exact name")
    parser.add_argument("--apply", action="store_true", help="Upload new/changed sources and update state")
    parser.add_argument("--include-pdf-extracts", action="store_true", help="Also sync *_pdf.md extraction files")
    parser.add_argument("--include-submissions", action="store_true", help="Also sync files inside submission folders")
    parser.add_argument("--max-list", type=int, default=100, help="Maximum pending uploads to print. Default: 100")
    parser.add_argument("--notebook-prefix", default=DEFAULT_NOTEBOOK_PREFIX, help="Prefix for created NotebookLM notebook titles")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config).resolve()
    root = Path(args.root).resolve() if args.root else load_storage_root(config_path)
    state_path = Path(args.state).resolve()

    state = load_state(state_path)
    sources = discover_sources(
        root,
        course_filter=args.course,
        include_pdf_extracts=args.include_pdf_extracts,
        include_submissions=args.include_submissions,
    )
    plan = build_plan(sources, state)
    print_plan(plan, max_items=args.max_list)

    if not args.apply:
        print("\nDry run only. Re-run with --apply to upload new/changed sources.")
        return 0

    uploaded = apply_plan(plan, state, state_path, args.notebook_prefix)
    print(f"\nUploaded {uploaded} source(s). State saved to {state_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
