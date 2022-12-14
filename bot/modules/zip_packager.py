"""ZIP packaging for cloned websites"""
import os
import zipfile
import logging

logger = logging.getLogger(__name__)


def create_zip(source_dir: str, zip_path: str) -> str:
    """Package a directory into a ZIP file. Returns the zip path."""
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(source_dir):
            for fname in files:
                full = os.path.join(root, fname)
                arcname = os.path.relpath(full, source_dir)
                try:
                    zf.write(full, arcname)
                except Exception as e:
                    logger.warning(f"Skipped {arcname}: {e}")
    return zip_path


def get_dir_size(path: str) -> int:
    """Get total size of directory in bytes"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return total


def format_size(size_bytes: int) -> str:
    """Human-readable file size"""
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
