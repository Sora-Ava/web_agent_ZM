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
    original_name: str = ''
    task_id: str | None = None
    row_index: int | None = None

    def public(self):
        return {'id': self.id, 'name': self.name, 'size': self.size, 'original_name': self.original_name,
                'task_id': self.task_id, 'row_index': self.row_index}


class DownloadStore:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.files: dict[str, ExportedFile] = {}
        self.repository = None
        self.task_context = (None, None)

    def bind_repository(self, repository):
        self.repository = repository
        for metadata in repository.files():
            path = Path(metadata['path']).resolve()
            # Only the configured export directory is exposed by file endpoints.
            if path.parent == self.directory:
                self.files[metadata['id']] = ExportedFile(metadata['id'], metadata['name'], path, metadata['size'],
                    metadata.get('original_name', ''), metadata.get('task_id'), metadata.get('row_index'))

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
        task_id, row_index = self.task_context
        entry = ExportedFile(identity, path.name, path, path.stat().st_size, raw, task_id, row_index)
        if self.repository:
            self.repository.register_file({**entry.public(), 'path': str(path)})
        self.files[identity] = entry
        return identity

    def list(self):
        return [entry.public() for entry in self.files.values()]

    def get(self, identity: str):
        return self.files.get(identity)
