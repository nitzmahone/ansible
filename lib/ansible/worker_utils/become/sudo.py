import logging
import secrets
import typing as t

from . import AsyncBecome
from ..storage_protocol import AsyncResourceWriter

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class AsyncSudo(AsyncBecome):
    plugin_options = {
        'become_user': ['ansible_become_user'],
        'become_password': ['ansible_become_pass'],
        'requires_tty': ['ansible_sudo_requires_tty']
    }

    def __init__(self):
        self.id = secrets.token_hex(16)
        self.prompt = f'[sudo via ansible, key={self.id}] password:'
        self.success = f'BECOME-SUCCESS-{self.id}'
        self._completed = False

    @property
    def completed(self) -> bool:
        return self._completed

    @property
    def requires_tty(self) -> bool:
        return self.get_option('requires_tty').lower() == "true"

    def build_become_command(self, cmd: str) -> str:
        user = self.get_option('become_user')
        flags = []
        if not self.requires_tty:
            flags.append('--stdin')
        return f"sudo --prompt='{self.prompt}' {' '.join(flags)} --user='{user}' /bin/sh -c 'echo \"{self.success}\" && {cmd}'"

    async def process_data_line(
        self,
        stream_type: str,
        lines: t.List[bytes],
        writer: AsyncResourceWriter,
    ) -> t.Optional[bytes]:
        last_line = lines[-1]
        logger.debug(f"sudo {stream_type}: {last_line.decode()}")
        if self.success.encode() in last_line:
            self._completed = True

        elif last_line == self.prompt.encode():
            password = self.get_option('become_password').encode()
            await writer.write(password + b"\n")

        return None
