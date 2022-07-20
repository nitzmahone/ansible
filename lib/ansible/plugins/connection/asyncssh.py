# Copyright: Contributors to the Ansible project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

DOCUMENTATION = '''
    name: asyncssh
    short_description: execute via SSH with ronf/asyncssh Python lib
    description:
        - This connection plugin allows ansible to execute tasks on a remote host with the ronf/asyncssh Python SSH library.
    author: ansible (@core)
    version_added: "2.14"
    extends_documentation_fragment:
        - connection_pipelining
    options:
      host:
          description: Hostname/IP to connect to.
          default: inventory_hostname
          vars:
               - name: inventory_hostname
               - name: ansible_host
               - name: ansible_ssh_host
               - name: delegated_vars['ansible_host']
               - name: delegated_vars['ansible_ssh_host']
      password:
          description: Authentication password for the C(remote_user). Can be supplied as CLI option.
          vars:
              - name: ansible_password
              - name: ansible_ssh_pass
              - name: ansible_ssh_password
      remote_user:
          description:
              - User name with which to login to the remote server, normally set by the remote_user keyword.
              - If no user is supplied, the current user on the Ansible 'controller' will be used.
          ini:
            - section: defaults
              key: remote_user
          env:
            - name: ANSIBLE_REMOTE_USER
          vars:
            - name: ansible_user
            - name: ansible_ssh_user
          cli:
            - name: user
          keyword:
            - name: remote_user
      pipelining:
          env:
            - name: ANSIBLE_PIPELINING
            - name: ANSIBLE_SSH_PIPELINING
          ini:
            - section: defaults
              key: pipelining
            - section: connection
              key: pipelining
            - section: ssh_connection
              key: pipelining
          vars:
            - name: ansible_pipelining
            - name: ansible_ssh_pipelining
'''

from . import AsyncConnectionBase


class Connection(AsyncConnectionBase):
    """AsyncSSH based connections."""

    transport = 'asyncssh'
    has_pipelining = True

    @property
    def async_plugin_name(self) -> str:
        """The fully qualified name of the async portion of this connection plugin."""
        return 'ansible.worker_utils.connection.asyncssh'
