import asyncio
import hashlib
import typing as t

import psrp
import psrpcore

from . import AsyncConnection, AsyncProcess, BytesIOWriter
from ..storage import AsyncResourceReader, AsyncResourceWriter


_PUT_SCRIPT = r'''[CmdletBinding()]
param (
    [Parameter(Mandatory = $true, Position = 0)]
    [string]
    $Path,

    [Parameter(ValueFromPipeline = $true)]
    [byte[]]
    $InputObject
)

begin {
    $ErrorActionPreference = "Stop"
    $WarningPreference = "Continue"
    $Path = [System.Environment]::ExpandEnvironmentVariables($Path)
    $fd = [System.IO.File]::Create($Path)
    $algo = [System.Security.Cryptography.SHA1CryptoServiceProvider]::Create()
    $bytes = @()

    $bindingFlags = [System.Reflection.BindingFlags]'NonPublic, Instance'
    Function Get-Property {
        <#
        .SYNOPSIS
        Gets the private/internal property specified of the object passed in.
        #>
        Param (
            [Parameter(Mandatory = $true, ValueFromPipeline = $true)]
            [System.Object]
            $Object,

            [Parameter(Mandatory = $true, Position = 1)]
            [System.String]
            $Name
        )

        process {
            $Object.GetType().GetProperty($Name, $bindingFlags).GetValue($Object, $null)
        }
    }

    Function Set-Property {
        <#
        .SYNOPSIS
        Sets the private/internal property specified on the object passed in.
        #>
        Param (
            [Parameter(Mandatory = $true, ValueFromPipeline = $true)]
            [System.Object]
            $Object,

            [Parameter(Mandatory = $true, Position = 1)]
            [System.String]
            $Name,

            [Parameter(Mandatory = $true, Position = 2)]
            [AllowNull()]
            [System.Object]
            $Value
        )

        process {
            $Object.GetType().GetProperty($Name, $bindingFlags).SetValue($Object, $Value, $null)
        }
    }

    Function Get-Field {
        <#
        .SYNOPSIS
        Gets the private/internal field specified of the object passed in.
        #>
        Param (
            [Parameter(Mandatory = $true, ValueFromPipeline = $true)]
            [System.Object]
            $Object,

            [Parameter(Mandatory = $true, Position = 1)]
            [System.String]
            $Name
        )

        process {
            $Object.GetType().GetField($Name, $bindingFlags).GetValue($Object)
        }
    }

    try {
        $Host | Get-Property 'ExternalHost' | `
                Get-Field '_transportManager' | `
                Get-Property 'Fragmentor' | `
                Get-Property 'DeserializationContext' | `
                Set-Property 'MaximumAllowedMemory' $null
    }
    catch {}
} process {
    $bytes = $InputObject
    $fd.Write($bytes, 0, $bytes.Length)
    $algo.TransformBlock($bytes, 0, $bytes.Length, $bytes, 0) > $null
} end {
    $fd.Close()

    $algo.TransformFinalBlock($bytes, 0, 0) > $null
    [System.BitConverter]::ToString($algo.Hash).Replace('-', '').ToLowerInvariant()
}'''

_FETCH_SCRIPT_SETUP = r'''[CmdletBinding()]
param (
    [Parameter(Mandatory)]
    [string]
    $Path,

    [Parameter(Mandatory)]
    [int]
    $BufferSize
)

end {
    $ErrorActionPreference = "Stop"

    if (Test-Path -Path $Path -PathType Leaf) {
        $fs = New-Object -TypeName System.IO.FileStream -ArgumentList @(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        [byte[]]$buffer = New-Object -TypeName byte[] -ArgumentList $BufferSize
    } elseif (Test-Path -Path $path -PathType Container) {
        Write-Output -InputObject "[DIR]"
    } else {
        Write-Error -Message "$path does not exist"
    }
}'''

_FETCH_SCRIPT_BLOCK = r'''[CmdletBinding()]
param (
    [Parameter(Mandatory)]
    [int]
    $Offset
)

end {
    $ErrorActionPreference = "Stop"

    $fs.Seek($Offset, [System.IO.SeekOrigin]::Begin) > $null
    $read = $fs.Read($buffer, 0, $buffer.Length)

    if ($read -gt 0) {
        , ([byte[]]$buffer[0..($read - 1)])
    }
}
'''


async def _stdin_iterator(
        input_queue: asyncio.Queue[t.Optional[bytes]],
) -> t.AsyncIterator[bytes]:
    while True:
        data = await input_queue.get()
        if data is None:
            return

        yield data


class StdoutReader(AsyncResourceReader):
    def __init__(self, stream: psrp.AsyncPSDataCollection) -> None:
        self._stream = stream
        self._output_queue: asyncio.Queue[t.Optional[bytes]] = asyncio.Queue()
        self._stream.data_added += self._on_data_added

    async def read(self, n=-1) -> bytes:
        data = await self._output_queue.get()
        if data is None:
            return b""
        else:
            return data

    async def _on_data_added(self, data: t.Any) -> None:
        await self._output_queue.put(str(data).encode())


class StdinWriter(AsyncResourceWriter):

    def __init__(self) -> None:
        self.input_queue: asyncio.Queue[t.Optional[bytes]] = asyncio.Queue()

    async def write(self, data: bytes) -> None:
        await self.input_queue.put(data)

    async def write_eof(self) -> None:
        await self.input_queue.put(None)


class PSRPProcess(AsyncProcess):
    def __init__(
            self,
            ps: psrp.AsyncPowerShell,
            task: asyncio.Task,
            stdout: StdoutReader,
            stderr: StdoutReader,
            stdin: StdinWriter,
    ) -> None:
        self._ps = ps
        self._ps.state_changed += self._on_complete
        self._task = task
        self._stdout = stdout
        self._stderr = stderr
        self._stdin = stdin

    @property
    def stdin(self) -> AsyncResourceWriter:
        return self._stdin

    @property
    def stdout(self) -> AsyncResourceReader:
        return self._stdout

    @property
    def stderr(self) -> AsyncResourceReader:
        return self._stderr

    @property
    def rc(self) -> t.Optional[int]:
        return int(self._ps.had_errors) if self._task.done() else None

    async def wait_for_exit(self) -> int:
        # await asyncio.wait(self._task)
        await self._task
        return int(self._ps.had_errors)

    async def _on_complete(self, _) -> None:
        await self._stdout._output_queue.put(None)
        await self._stderr._output_queue.put(None)


class PSRPConnection(AsyncConnection):
    # FIXME: bridge this from Ansible YAML config
    plugin_options = {
        'inventory_hostname': ['inventory_hostname'],
        'host': ['host'],
        'port': ['port'],
        'remote_user': ['remote_user'],
        'password': ['password'],
    }

    def __init__(self) -> None:
        super().__init__()
        self._runspace: t.Optional[psrp.AsyncRunspacePool] = None
        self._conn_info: t.Optional[psrp.WSManInfo] = None

    async def connect(self) -> None:
        if not self._runspace:
            self._runspace = psrp.AsyncRunspacePool(psrp.WSManInfo(
                self.get_option('host'),
                port=self.get_option('port', 5985),
                scheme='http',
                username=self.get_option('remote_user', None),
                password=self.get_option('password', None),
                verify=False,
            ))
            try:
                await self._runspace.open()
            except Exception as e:
                x = 5

            x = 5



    async def close(self) -> None:
        if self._runspace:
            await self._runspace.close()
            self._runspace = None

    async def streaming_exec_command(
            self,
            cmd: str,
    ) -> AsyncProcess:
        assert self._runspace is not None

        ps = psrp.AsyncPowerShell(self._runspace)
        ps.add_script(cmd)

        stdin = StdinWriter()
        stdout = psrp.AsyncPSDataCollection()

        task = asyncio.create_task(ps.invoke_async(
            input_data=_stdin_iterator(stdin.input_queue),
            output_stream=stdout,
        ))

        return PSRPProcess(ps, task, StdoutReader(stdout), StdoutReader(ps.streams.error), stdin)

    async def put_file(self, src: AsyncResourceReader, dst: str) -> None:
        sha1_hash = hashlib.sha1()

        ps = psrp.AsyncPowerShell(self._runspace)
        ps.add_script(_PUT_SCRIPT)
        ps.add_parameter("Path", dst)
        out = await ps.invoke(input_data=self._stream_stdin(src, hash_algo=sha1_hash))

        if ps.streams.error:
            raise Exception(f"Failed to put file {ps.streams.error[0]}")

        local_sha1 = sha1_hash.hexdigest()
        remote_sha1 = out[0]

        if not remote_sha1 == local_sha1:
            raise Exception(f"Remote sha1 hash {remote_sha1} does not match local hash {local_sha1}")

    async def fetch_file(self, src: str, dst: AsyncResourceWriter) -> None:
        ps = psrp.AsyncPowerShell(self._runspace)
        ps.add_script(_FETCH_SCRIPT_SETUP, use_local_scope=False)
        ps.add_parameters(Path=src, BufferSize=self._runspace.max_payload_size)
        out = await ps.invoke()
        if ps.streams.error:
            raise Exception(f"Failed to setup file stream for fetch {ps.streams.error[0]}")

        elif out and out[0] == "[DIR]":
            return

        offset = 0
        while True:
            ps = psrp.AsyncPowerShell(self._runspace)
            ps.add_script(_FETCH_SCRIPT_BLOCK)
            ps.add_parameter("Offset", offset)
            out = await ps.invoke()
            if ps.streams.error:
                raise Exception(f"Failed to read file stream for fetch {ps.streams.error[0]}")

            out_data = out[0]

            await dst.write(out_data)
            if len(out_data) < self._runspace.max_payload_size:
                break
            offset += len(out_data)

        ps = psrp.AsyncPowerShell(self._runspace)
        ps.add_script("$fs.Dispose()")
        await ps.invoke()

    async def _stream_stdin(
        self,
        reader: AsyncResourceReader,
        hash_algo: t.Optional[hashlib.sha1] = None,
    ) -> t.AsyncIterator[bytes]:
        while data := await reader.read(self._runspace.max_payload_size):
            if hash_algo:
                hash_algo.update(data)

            yield data
