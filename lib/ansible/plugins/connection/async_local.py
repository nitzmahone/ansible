# Copyright: Contributors to the Ansible project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

DOCUMENTATION = '''
    name: async_local
    short_description: execute on controller
    description:
        - This connection plugin allows ansible to execute tasks on the Ansible 'controller' instead of on a remote host.
    author: ansible (@core)
    version_added: "2.14"
    extends_documentation_fragment:
        - connection_pipelining
    notes:
        - The remote user is ignored, the user with which the ansible CLI was executed is used instead.
'''

from . import AsyncConnectionBase


class Connection(AsyncConnectionBase):
    """Local based connections."""

    transport = 'local'  # mimic the 'local' connection plugin, since some special behavior exists that depends on this value
    has_pipelining = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.cwd = None

    @property
    def async_plugin_name(self) -> str:
        """The fully qualified name of the async portion of this connection plugin."""
        return 'ansible.worker_utils.connection.async_local'
