from typing import TypedDict
from pathlib import Path

class Issue(TypedDict):
    """Github Issue data and relevant info for run"""

    url: str    # Issue URL
    repo: str   # Repo URL
    dir: Path   # local path for run
    desc: str   # markdown document with issue title, description