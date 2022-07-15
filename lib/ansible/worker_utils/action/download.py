import logging

from . import AsyncAction
from ..storage import AsyncFileWriter

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class DownloadAction(AsyncAction):
    uses_plugin_type_names = frozenset({'connection'})

    async def run(self) -> dict[str, ...]:
        connection = await self.context.connection

        dst = await AsyncFileWriter.create(self.context.action_args['dst'])
        logger.debug(f'running fetch_file start')
        await connection.fetch_file(self.context.action_args['src'], dst)
        logger.debug(f'running fetch_file done')
        return {'changed': True}
