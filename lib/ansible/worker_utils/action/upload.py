import logging

from . import AsyncAction
from ..storage import AsyncFileReader

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class UploadAction(AsyncAction):
    uses_plugin_type_names = frozenset({'connection'})

    async def run(self) -> dict[str, ...]:
        connection = await self.context.connection

        src = await AsyncFileReader.create(self.context.action_args['src'])
        logger.debug(f'running put_file start')
        await connection.put_file(src, self.context.action_args['dst'])
        logger.debug(f'running put_file done')
        return {'changed': True}
