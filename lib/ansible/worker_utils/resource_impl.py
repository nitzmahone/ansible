import dataclasses

from .message import BaseResource
from .storage import BLOB_STORE, AsyncResourceReader, AsyncResourceWriter, AsyncFileReader, AsyncFileWriter


@dataclasses.dataclass(frozen=True)
class BlobResource(BaseResource):
    key: str

    @property
    async def reader(self) -> AsyncResourceReader:
        return await BLOB_STORE.get(self.key)

    @property
    async def writer(self) -> AsyncResourceWriter:
        return await BLOB_STORE.put(self.key)


@dataclasses.dataclass(frozen=True)
class FilesystemResource(BaseResource):
    path: str

    @property
    async def reader(self) -> AsyncResourceReader:
        return await AsyncFileReader.create(self.path)

    @property
    async def writer(self) -> AsyncResourceWriter:
        return await AsyncFileWriter.create(self.path)

