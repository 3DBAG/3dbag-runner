from pathlib import Path


class FileHandle:
    path: Path
    must_dispose: bool

    def __init__(self, path: Path, must_dispose: bool = False) -> None:
        self.path = path
        self.must_dispose = must_dispose
