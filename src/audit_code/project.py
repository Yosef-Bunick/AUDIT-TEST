"""Project detection — resolve the target repository root."""

from pathlib import Path


def find_target_root(user_path: str | None = None) -> Path:
    """Resolve the target repository to audit.

    Args:
        user_path: explicit path from CLI, or None for cwd.

    Returns:
        Absolute path to the target project root.

    Raises:
        SystemExit: if the path doesn't exist or isn't a directory.
    """
    target = Path(user_path).resolve() if user_path else Path.cwd()

    if not target.exists():
        print(f"audit-code: target path does not exist: {target}")
        raise SystemExit(2)

    if not target.is_dir():
        print(f"audit-code: target path is not a directory: {target}")
        raise SystemExit(2)

    return target
