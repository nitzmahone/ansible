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
      # transport options
      host:
        description:
        - The hostname or IP address of the remote host.
        default: inventory_hostname
        type: str
        vars:
        - name: inventory_hostname
        - name: ansible_host
        - name: ansible_psrp_host
      remote_user:
        description:
        - The user to log in as.
        type: str
        vars:
        - name: ansible_user
        - name: ansible_psrp_user
        keyword:
        - name: remote_user
      password:
        description: Authentication password for the C(remote_user). Can be supplied as CLI option.
        type: str
        vars:
        - name: ansible_password
        - name: ansible_winrm_pass
        - name: ansible_winrm_password
        aliases:
        - password  # Needed for --ask-pass to come through on delegation
      port:
        description:
        - The port for PSRP to connect on the remote target.
        - Default is C(5986) if I(protocol) is not defined or is C(https),
          otherwise the port is C(5985).
        type: int
        vars:
        - name: ansible_port
        - name: ansible_psrp_port
        keyword:
        - name: port
      protocol:
        description:
        - Set the protocol to use for the connection.
        - Default is C(https) if I(port) is not defined or I(port) is not C(5985).
        choices:
        - http
        - https
        type: str
        vars:
        - name: ansible_psrp_protocol
      path:
        description:
        - The URI path to connect to.
        type: str
        vars:
        - name: ansible_psrp_path
        default: 'wsman'
      auth:
        description:
        - The authentication protocol to use when authenticating the remote user.
        - The default, C(negotiate), will attempt to use C(Kerberos) if it is
          available and fall back to C(NTLM) if it isn't.
        type: str
        vars:
        - name: ansible_psrp_auth
        choices:
        - basic
        - certificate
        - negotiate
        - kerberos
        - ntlm
        - credssp
        default: negotiate
      cert_validation:
        description:
        - Whether to validate the remote server's certificate or not.
        - Set to C(ignore) to not validate any certificates.
        - I(ca_cert) can be set to the path of a PEM certificate chain to
          use in the validation.
        choices:
        - validate
        - ignore
        default: validate
        type: str
        vars:
        - name: ansible_psrp_cert_validation
      ca_cert:
        description:
        - The path to a PEM certificate chain to use when validating the server's
          certificate.
        - This value is ignored if I(cert_validation) is set to C(ignore).
        type: path
        vars:
        - name: ansible_psrp_cert_trust_path
        - name: ansible_psrp_ca_cert
        aliases: [ cert_trust_path ]
      connection_timeout:
        description:
        - The connection timeout for making the request to the remote host.
        - This is measured in seconds.
        type: int
        vars:
        - name: ansible_psrp_connection_timeout
        default: 30
      read_timeout:
        description:
        - The read timeout for receiving data from the remote host.
        - This value must always be greater than I(operation_timeout).
        - This option requires pypsrp >= 0.3.
        - This is measured in seconds.
        type: int
        vars:
        - name: ansible_psrp_read_timeout
        default: 30
        version_added: '2.8'
      reconnection_retries:
        description:
        - The number of retries on connection errors.
        type: int
        vars:
        - name: ansible_psrp_reconnection_retries
        default: 0
        version_added: '2.8'
      reconnection_backoff:
        description:
        - The backoff time to use in between reconnection attempts.
          (First sleeps X, then sleeps 2*X, then sleeps 4*X, ...)
        - This is measured in seconds.
        - The C(ansible_psrp_reconnection_backoff) variable was added in Ansible
          2.9.
        type: int
        vars:
        - name: ansible_psrp_connection_backoff
        - name: ansible_psrp_reconnection_backoff
        default: 2
        version_added: '2.8'
      message_encryption:
        description:
        - Controls the message encryption settings, this is different from TLS
          encryption when I(ansible_psrp_protocol) is C(https).
        - Only the auth protocols C(negotiate), C(kerberos), C(ntlm), and
          C(credssp) can do message encryption. The other authentication protocols
          only support encryption when C(protocol) is set to C(https).
        - C(auto) means means message encryption is only used when not using
          TLS/HTTPS.
        - C(always) is the same as C(auto) but message encryption is always used
          even when running over TLS/HTTPS.
        - C(never) disables any encryption checks that are in place when running
          over HTTP and disables any authentication encryption processes.
        type: str
        vars:
        - name: ansible_psrp_message_encryption
        choices:
        - auto
        - always
        - never
        default: auto
      proxy:
        description:
        - Set the proxy URL to use when connecting to the remote host.
        vars:
        - name: ansible_psrp_proxy
        type: str
      ignore_proxy:
        description:
        - Will disable any environment proxy settings and connect directly to the
          remote host.
        - This option is ignored if C(proxy) is set.
        vars:
        - name: ansible_psrp_ignore_proxy
        type: bool
        default: 'no'
    
      # auth options
      certificate_key_pem:
        description:
        - The local path to an X509 certificate key to use with certificate auth.
        type: path
        vars:
        - name: ansible_psrp_certificate_key_pem
      certificate_pem:
        description:
        - The local path to an X509 certificate to use with certificate auth.
        type: path
        vars:
        - name: ansible_psrp_certificate_pem
      credssp_auth_mechanism:
        description:
        - The sub authentication mechanism to use with CredSSP auth.
        - When C(auto), both Kerberos and NTLM is attempted with kerberos being
          preferred.
        type: str
        choices:
        - auto
        - kerberos
        - ntlm
        default: auto
        vars:
        - name: ansible_psrp_credssp_auth_mechanism
      credssp_disable_tlsv1_2:
        description:
        - Disables the use of TLSv1.2 on the CredSSP authentication channel.
        - This should not be set to C(yes) unless dealing with a host that does not
          have TLSv1.2.
        default: no
        type: bool
        vars:
        - name: ansible_psrp_credssp_disable_tlsv1_2
      credssp_minimum_version:
        description:
        - The minimum CredSSP server authentication version that will be accepted.
        - Set to C(5) to ensure the server has been patched and is not vulnerable
          to CVE 2018-0886.
        default: 2
        type: int
        vars:
        - name: ansible_psrp_credssp_minimum_version
      negotiate_delegate:
        description:
        - Allow the remote user the ability to delegate it's credentials to another
          server, i.e. credential delegation.
        - Only valid when Kerberos was the negotiated auth or was explicitly set as
          the authentication.
        - Ignored when NTLM was the negotiated auth.
        type: bool
        vars:
        - name: ansible_psrp_negotiate_delegate
      negotiate_hostname_override:
        description:
        - Override the remote hostname when searching for the host in the Kerberos
          lookup.
        - This allows Ansible to connect over IP but authenticate with the remote
          server using it's DNS name.
        - Only valid when Kerberos was the negotiated auth or was explicitly set as
          the authentication.
        - Ignored when NTLM was the negotiated auth.
        type: str
        vars:
        - name: ansible_psrp_negotiate_hostname_override
      negotiate_send_cbt:
        description:
        - Send the Channel Binding Token (CBT) structure when authenticating.
        - CBT is used to provide extra protection against Man in the Middle C(MitM)
          attacks by binding the outer transport channel to the auth channel.
        - CBT is not used when using just C(HTTP), only C(HTTPS).
        default: yes
        type: bool
        vars:
        - name: ansible_psrp_negotiate_send_cbt
      negotiate_service:
        description:
        - Override the service part of the SPN used during Kerberos authentication.
        - Only valid when Kerberos was the negotiated auth or was explicitly set as
          the authentication.
        - Ignored when NTLM was the negotiated auth.
        default: WSMAN
        type: str
        vars:
        - name: ansible_psrp_negotiate_service
    
      # protocol options
      operation_timeout:
        description:
        - Sets the WSMan timeout for each operation.
        - This is measured in seconds.
        - This should not exceed the value for C(connection_timeout).
        type: int
        vars:
        - name: ansible_psrp_operation_timeout
        default: 20
      max_envelope_size:
        description:
        - Sets the maximum size of each WSMan message sent to the remote host.
        - This is measured in bytes.
        - Defaults to C(150KiB) for compatibility with older hosts.
        type: int
        vars:
        - name: ansible_psrp_max_envelope_size
        default: 153600
      configuration_name:
        description:
        - The name of the PowerShell configuration endpoint to connect to.
        type: str
        vars:
        - name: ansible_psrp_configuration_name
        default: Microsoft.PowerShell
'''

from . import AsyncConnectionBase


class Connection(AsyncConnectionBase):
    """PSRP based connections."""

    transport = 'async_psrp'
    module_implementation_preferences = ('.ps1', '.exe', '')
    allow_executable = False
    has_pipelining = True
    allow_extras = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shell_type = 'powershell'

    @property
    def async_plugin_name(self) -> str:
        """The fully qualified name of the async portion of this connection plugin."""
        return 'ansible.worker_utils.connection.async_psrp'
