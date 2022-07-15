from ..plugin_manager import BasePlugin
from ..tasks import TaskContext


class AsyncAction(BasePlugin):
    ansible_variable_name = ''
    ansible_plugin_type = 'action'

    def __init__(self, *, context: TaskContext, **kwargs) -> None:
        # FIXME: try not to need this
        super().__init__()
        self.context = context

    async def run(self) -> dict[str, ...]:
        pass
