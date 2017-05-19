# (c) 2016, Fran Fitzpatrick <francis.x.fitzpatrick@gmail.com>
# (c) 2017, Rackspace Managed Security
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.
'''
DOCUMENTATION:
    connection: awsrun
    short_description: connect via AWS Run Command
    description:
        - This connection plugin allows ansible to communicate to an EC2 Instance using Run Command.
        - AWS Run Command itself has the following limitations and [prerequisites](http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/remote-commands-prereq.html), so an instance must adhere to these restrictions:
            - Run Command is only available in the SSM [regions](http://docs.aws.amazon.com/general/latest/gr/rande.html#ssm_region)
            - Instances must be running the latest version of the SSM [agent](http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/install-ssm-agent.html)
            - Proper IAM Roles/Permissions must be [configured](http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ssm-iam.html)
            - The EC2 instances must have outbound Internet access (although AWS documentation does not specify *exactly* where the agent needs access to)
    author: Rackspace Managed Security
    version_added: 2.x  # XXX: Update me.
    options:
        Host:
            description: When using this transport, the host is the instance ID (not an IP address).
            host_vars:
                - ansible_host
        AWS Access Key ID:
            description: Access Key for the account that has the EC2 instance
            host_vars:
               - aws_access_key_id
        AWS Secret Access Key:
            description: Secret Key for the account that has the EC2 instance
            host_vars:
               - aws_secret_access_key
        AWS Session Token:
            description: OPTIONAL: For use with temporary security credentials
            host_vars:
                - aws_session_token
        AWS Region Name:
            description: Name of the region in which the instance resides
            host_vars:
                - aws_region_name
        S3 Output Bucket:
            description:
                - S3 bucket for where to save results
                - Used to temporarily store command output. Without using S3, command output is truncated at 2500 characters and Ansible does a horrible death.
'''
from __future__ import absolute_import, division, print_function

from __main__ import display

import base64
import functools
import uuid

from ansible.errors import AnsibleError
from ansible.plugins import shell_loader
from ansible.plugins.connection import ConnectionBase

try:
    import boto3
except ImportError:  # pragma: no cover
    raise AnsibleError('boto3 is not installed')

from botocore.exceptions import ClientError
from retrying import retry, RetryError


_MAX_THROTTLING_ATTEMPTS = 20
_SSM_SUCCESS_STATES = ('Success',)
_SSM_FAILURE_STATES = ('Cancelled', 'Failed', 'TimedOut',)
_SSM_TERMINAL_STATES = _SSM_SUCCESS_STATES + _SSM_FAILURE_STATES
_SSM_INCOMPLETE_STATES = ('Cancelling', 'Delayed', 'InProgress', 'Pending',)
_BOTO_ERRORS = ('ThrottlingException', 'InternalServerError',)


def ensure_connect(func):
    @functools.wraps(func)
    def wrapped(self, *args, **kwargs):
        if not self.connected:
            self._connect()
        return func(self, *args, **kwargs)
    return wrapped


def _retry_list_command_status(result):
    """Should we try to list the result of a given command again?"""
    status = result['Status']
    display.vvvv(msg='Current status: {}'.format(status))
    if status in _SSM_TERMINAL_STATES:
        return False
    elif status in _SSM_INCOMPLETE_STATES:
        return True
    else:
        msg = ('An unknown status was encountered while executing command '
               '(Command ID: {}; Status: {})')
        raise AnsibleError(msg.format(result['CommandId'], status))


class DeliveryTimeOutError(AnsibleError):
    """The command was not delivered within SSM's timeout window."""
    pass


class Connection(ConnectionBase):
    """AWS Run Command based connections"""

    aws_access_key_id = None
    aws_secret_access_key = None
    aws_session_token = None
    aws_region_name = None
    platform_type = None
    s3_output_bucket = None

    _connected = False
    _ssm = None
    _ec2 = None
    _s3 = None
    _session = None

    @property
    def allow_executable(self):
        return self._shell_type != 'powershell'

    @property
    def transport(self):
        """Identifies the this connection plugin"""
        return 'awsrun'

    @property
    def module_implementation_preferences(self):
        if self.platform_type == 'Windows':
            return ('.ps1', '')
        else:
            return ('',)

    @property
    def _shell_type(self):
        if self.platform_type == 'Windows':
            return 'powershell'
        else:
            return 'sh'

    @property
    def _ssm_document(self):
        if self.platform_type == 'Windows':
            return 'AWS-RunPowerShellScript'
        elif self.platform_type == 'Linux':
            return 'AWS-RunShellScript'
        else:
            msg = 'This instance is not a supported platform ({})'
            raise AnsibleError(msg.format(self.platform_type))

    def __init__(self, *args, **kwargs):
        # NOTE(fxfitz): These may need to be modified when we merge upstream;
        # hoping we can get feedback from Ansible community on these, but for
        # now these work for us.
        self.has_pipelining = False
        self.protocol = None
        self.shell_id = None
        self.delegate = None

        super(Connection, self).__init__(*args, **kwargs)

    def set_host_overrides(self, host, hostvars=None):
        """Override AWS-specific options from host variables."""

        self.host = self._play_context.remote_addr

        self.aws_access_key_id = hostvars.get('aws_access_key_id')
        if self.aws_access_key_id is None:
            raise AnsibleError('AWS Access Key ID must be provided.')

        self.aws_secret_access_key = hostvars.get('aws_secret_access_key')
        if self.aws_secret_access_key is None:
            raise AnsibleError('AWS Secret Access Key must be provided.')

        # NOTE: Session tokens are optional
        self.aws_session_token = hostvars.get('aws_session_token')

        self.aws_region_name = hostvars.get('aws_region_name')
        if self.aws_region_name is None:
            raise AnsibleError('AWS Region Name must be provided')

        self.s3_output_bucket = hostvars.get('s3_output_bucket')
        if self.s3_output_bucket is None:
            raise AnsibleError('A S3 bucket name must be provided')

        self._aws_connect()

    @ensure_connect
    @retry(retry_on_exception=(lambda x: isinstance(x, DeliveryTimeOutError) or
                               isinstance(x, RetryError) or
                               any(e in str(x) for e in _BOTO_ERRORS)),
           stop_max_attempt_number=3)
    def exec_command(self, cmd, tmp_path='', become_user=None, sudoable=False,
                     executable=None, in_data=None):
        display.vvv('EXEC length {}'.format(len(cmd)), host=self.host)
        command_id = self._exec_command(cmd, self.host)
        result = self._get_command_results(command_id)

        return result

    @ensure_connect
    def put_file(self, in_path, out_path):
        display.vvv('PUT {} -> {}'.format(in_path, out_path), host=self.host)

        s3_result = self._s3_put_file(in_path, out_path)
        if not s3_result:
            self._chunk_put_file(in_path, out_path)

    @ensure_connect
    def fetch_file(self, in_path, out_path):
        display.vvv('FETCH {} -> {}'.format(in_path, out_path), host=self.host)

        if self.platform_type == 'Windows':
            command = """
                $content = Get-Content {}
                $bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
                [System.Convert]::ToBase64String($bytes)
            """
        else:
            command = 'base64 -i {}'
        result = self.exec_command(command.format(in_path))

        output = result[1]
        decoded_output = base64.b64decode(output)
        with open(out_path, 'wb') as out_file:
            out_file.write(decoded_output)

    def close(self):
        self._connected = False
        self._session = None
        self._ssm = None
        self._ec2 = None
        self._s3 = None
        self._s3_client = None
        display.vv('The connection has been closed', host=self.host)

    def _connect(self):
            self._aws_connect()

    def _aws_connect(self):
        display.vv('Connecting to AWS SSM...', host=self.host)
        display.vv('AWS_ACCESS_KEY_ID: {}'.format(self.aws_access_key_id),
                   host=self.host)
        display.vv('AWS_REGION_NAME: {}'.format(self.aws_region_name),
                   host=self.host)

        if self._session is None:
            self._session = boto3.Session(
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                aws_session_token=self.aws_session_token,
                region_name=self.aws_region_name
            )

        self._ssm = self._session.client('ssm')
        self._ec2 = self._session.resource('ec2')
        self._s3 = self._session.resource('s3')
        self._s3_client = self._session.client('s3')

        instance_filter = [{
            'key': 'InstanceIds',
            'valueSet': [self.host]
        }]

        key = 'InstanceInformationList'
        try:
            info = self._ssm.describe_instance_information(
                InstanceInformationFilterList=instance_filter
            )[key]
        except ClientError as e:
            if e.response['Error']['Code'] == 'InvalidInstanceId':
                msg = '{} is an invalid instance id.'.format(self.host)
                raise AnsibleError(msg)
            else:
                raise
        ssm_instances = [x['InstanceId'] for x in info]

        if self.host not in ssm_instances:
            msg = '{} is not available for Run Command'.format(self.host)
            raise AnsibleError(msg)

        instance_info = [x for x in info if x['InstanceId'] == self.host][0]
        self.platform_type = instance_info['PlatformType']
        if self.platform_type not in ('Windows', 'Linux'):
            msg = 'This instance is not a supported platform ({})'
            raise AnsibleError(msg.format(self.platform_type))

        self._shell = shell_loader.get(self._shell_type)

        self._connected = True
        display.vv('Connected!', host=self.host)

        return self

    @ensure_connect
    def _exec_command(self, cmd, instance_id):
        display.vvvv('Sending cmd to instance {}'.format(instance_id),
                     host=self.host)

        resp = self._ssm.send_command(InstanceIds=[instance_id],
                                      DocumentName=self._ssm_document,
                                      TimeoutSeconds=60,
                                      Parameters={'commands': [cmd]},
                                      OutputS3BucketName=self.s3_output_bucket)
        command_id = resp['Command']['CommandId']
        display.vvvv('COMMAND ID: {}'.format(command_id))
        return command_id

    @ensure_connect
    def _get_command_results(self, command_id):
        result = self._get_ssm_command_result(command_id)
        status = result['Status']
        msg = 'Command ({}) is done! Result: {}'.format(command_id, status)
        display.vvvv(msg, host=self.host)

        if status in _SSM_FAILURE_STATES:
            status_details = result['StatusDetails']
            msg = 'Command ({}) has failed with status {} ({})'.format(
                command_id, status, status_details
            )
            if status_details == 'DeliveryTimedOut':
                raise DeliveryTimeOutError(msg)
            raise AnsibleError(msg)

        result = self._get_ssm_command_details(command_id)
        msg = 'Command ({}) is done! Result: {}'.format(command_id, result)
        display.vvvv(msg, host=self.host)

        details = result['CommandPlugins'][0]
        result_code = details.get('ResponseCode')
        output = details.get('Output')

        if output:
            # Since the output can be truncated, we need to look in S3
            stdout, stderr = self._get_stdout_stderr(command_id)
        else:
            # However if we already know there is no output, we can skip that
            stdout, stderr = ('', '')

        # NOTE(fxfitz): runPowerShellScript always returns 0 result_code, so
        # we'll return -1 if anything was written to stderr; not sure if this
        # is a good idea or not :-P
        if self.platform_type == 'Windows' and stderr and result_code == 0:
            result_code = -1

        result = (result_code, stdout, stderr)
        display.vvvv('Command ({}) result: {}'.format(command_id, result),
                     host=self.host)
        return result

    @ensure_connect
    @retry(retry_on_exception=lambda x: any(e in str(x) for e in _BOTO_ERRORS),
           stop_max_attempt_number=_MAX_THROTTLING_ATTEMPTS)
    def _get_ssm_command_details(self, command_id):
        """Get the detailed results of a command.

        We use this function after we know a command result was successful, to
        get more specific details of the operation.
        """
        msg = 'Fetching command details from SSM (Command ID: {})'
        display.vvvv(msg.format(command_id), host=self.host)
        commands = self._ssm.list_command_invocations(CommandId=command_id,
                                                      Details=True)
        return commands['CommandInvocations'][0]

    @ensure_connect
    @retry(retry_on_result=_retry_list_command_status,
           retry_on_exception=lambda x: any(e in str(x) for e in _BOTO_ERRORS),
           stop_max_delay=5 * 60 * 1000)
    def _get_ssm_command_result(self, command_id):
        """Get the general result of a command.

        We use this function to first determine if a command was successful
        before getting specific details.
        """
        msg = 'Fetching command status from SSM (Command ID: {})'
        display.vvvv(msg.format(command_id), host=self.host)
        result = self._ssm.list_commands(CommandId=command_id)
        return result['Commands'][0]

    @ensure_connect
    def _chunk_put_file(self, in_path, out_path):
        display.vvvv('CHUNK PUT {} -> {}'.format(in_path, out_path),
                     host=self.host)

        self.exec_command('rm -f {}'.format(out_path))
        with open(in_path, 'rb') as in_file:
            while True:
                result = False
                chunk = in_file.read(20000)  # because this seems to work
                if not chunk:
                    return result

                encoded_chunk = base64.b64encode(chunk)

                if self.platform_type == 'Windows':
                    cmd = """
                        $bytes  = [System.Convert]::FromBase64String("{}")
                        Add-Content -Encoding Byte {} $bytes
                    """
                else:
                    cmd = "echo -n '{}' | base64 --decode >> {}"
                self.exec_command(cmd.format(encoded_chunk, out_path))

    @ensure_connect
    def _s3_put_file(self, in_path, out_path):
        display.vvvv('S3 PUT {} -> {}'.format(in_path, out_path))
        key_name = str(uuid.uuid4())
        s3_object = self._s3.Object(self.s3_output_bucket, key_name)

        with open(in_path, 'rb') as in_file:
            s3_object.put(Body=in_file)

        url = self._s3_client.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': self.s3_output_bucket, 'Key': key_name}
        )

        if self.platform_type == 'Linux':
            cmd = 'curl "{}" -o {}'.format(url, out_path)
        else:
            cmd = 'Invoke-WebRequest -Uri "{}" -OutFile {}'.format(url,
                                                                   out_path)
        exec_res = self.exec_command(cmd)

        s3_object.delete()

        return exec_res[0] == 0

    @ensure_connect
    def _fetch_s3_object_body(self, bucket, key, delete):
        s3_object = self._s3.Object(bucket_name=bucket, key=key)
        body = s3_object.get()['Body'].read().decode('utf-8-sig')

        if delete:
            s3_object.delete()

        return body

    # We are using an exponential backoff here when looking for the output in
    # S3 because there is a possible race condition where we check for the key
    # before the output is actually written to S3.
    #
    # With this configuration, we're backing off incrementally (75
    # milliseconds) with 6 maximum attempts, resulting in a maximum wait of 4.8
    # seconds.
    @retry(wait_exponential_multiplier=75,
           stop_max_attempt_number=6,
           retry_on_result=lambda result: result == (None, None))
    def _get_output_keys(self, command_id):
        s3_bucket = self._s3.Bucket(name=self.s3_output_bucket)
        instance_ssm_output_objects = s3_bucket.objects.filter(
            Prefix='{}/{}/'.format(command_id, self.host)
        )

        stdout_key = None
        stderr_key = None
        for output in instance_ssm_output_objects:
            if 'stdout' in output.key:
                stdout_key = output.key
            elif 'stderr' in output.key:
                stderr_key = output.key

        display.vvvv('Output Keys: {}'.format((stdout_key, stderr_key)),
                     host=self.host)

        return (stdout_key, stderr_key)

    def _get_stdout_stderr(self, command_id):
        try:
            stdout_key, stderr_key = self._get_output_keys(command_id)
        except RetryError:
            stdout_key, stderr_key = (None, None)

        stdout = ''
        if stdout_key is not None:
            stdout = self._fetch_s3_object_body(
                bucket=self.s3_output_bucket,
                key=stdout_key,
                delete=True
            )

        stderr = ''
        if stderr_key is not None:
            stderr = self._fetch_s3_object_body(
                bucket=self.s3_output_bucket,
                key=stderr_key,
                delete=True
            )

        return (stdout, stderr)
