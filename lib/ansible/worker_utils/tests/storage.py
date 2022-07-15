from ..storage import _get_aio_streamreader


async def test_aio_read():
    sr = _get_aio_streamreader('../blobs/payload.py')
    content = await sr.readline()
    print(content)
    assert bool(content)
