import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass
class ExportedFile:
    id: str
    name: str
    path: Path
    size: int

    def public(self):
        return {'id': self.id, 'name': self.name, 'size': self.size}


class DownloadStore:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.files: dict[str, ExportedFile] = {}

    async def save(self, download, name_parts: list[str]) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        identity = uuid4().hex
        raw = Path(download.suggested_filename).name
        suffix = re.sub(r'[^a-zA-Z0-9.]', '', Path(raw).suffix)[:16] or '.bin'
        parts = name_parts or [Path(raw).stem]
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', '_'.join(parts)).strip(' .')[:140] or 'export'
        # UUID suffix also avoids Windows reserved device names and collisions.
        path = self.directory / f'{stem}_{identity}{suffix}'
        try:
            await download.save_as(path)
            failure = await download.failure()
            if failure or not path.exists() or path.stat().st_size == 0:
                raise ValueError('下载失败或文件为空')
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self.files[identity] = ExportedFile(identity, path.name, path, path.stat().st_size)
        return identity

    def list(self):
        return [entry.public() for entry in self.files.values()]

    def get(self, identity: str):
        return self.files.get(identity)
