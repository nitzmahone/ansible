# (c) 2016, Matt Davis <mdavis@ansible.com>
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

# CI-required python3 boilerplate
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import socket
import time

from datetime import datetime, timedelta

from ansible.plugins.action import ActionBase

try:
    from __main__ import display
except ImportError:
    from ansible.utils.display import Display
    display = Display()


class TimedOutException(Exception):
    pass


class ActionModule(ActionBase):
    TRANSFERS_FILES = False

    DEFAULT_SHUTDOWN_COMMAND = "shutdown -r now"
    DEFAULT_TEST_DELAY_SEC = 10
    DEFAULT_TEST_POLL_INTERVAL_SEC = 5
    DEFAULT_REBOOT_TIMEOUT_SEC = 600

    def do_until_success_or_timeout(self, what, timeout_sec, what_desc, fail_sleep_sec=1):
        max_end_time = datetime.utcnow() + timedelta(seconds=timeout_sec)

        while datetime.utcnow() < max_end_time:
            try:
                what()
                if what_desc:
                    display.debug("reboot: %s success" % what_desc)
                return
            except Exception as e:
                if what_desc:
                    display.debug("reboot: %s fail (expected), sleeping before retry..." % what_desc)
                time.sleep(fail_sleep_sec)

        raise TimedOutException("timed out waiting for %s" % what_desc)

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = dict()

        shutdown_command = self._task.args.get('shutdown_command', self.DEFAULT_SHUTDOWN_COMMAND)
        reboot_timeout_sec = int(self._task.args.get('reboot_timeout_sec', self.DEFAULT_REBOOT_TIMEOUT_SEC))
        test_delay_sec = int(self._task.args.get('test_delay_sec', self.DEFAULT_TEST_DELAY_SEC))
        test_poll_interval_sec = int(self._task.args.get('test_poll_interval_sec', self.DEFAULT_TEST_POLL_INTERVAL_SEC))

        if self._play_context.check_mode:
            display.vvv("reboot: skipping for check_mode")
            return dict(skipped=True)

        # run facts module
        # sample last boot time fact
        # issue reboot command (async, or just swallow transport errors? need to watch for and fail on non-transport "can't do that" kinds of things)
        # loop attempt facts refresh until last boot time is != previous value

        # TODO: pass through become task_vars, others (all?)

        result = super(ActionModule, self).run(tmp, task_vars)

        setup_res = self._execute_module(module_name="setup", module_args=dict(gather_subset="hardware"), delete_remote_tmp=True, task_vars=task_vars)

        # TODO: fallback/fail if we can't find this
        original_uptime_sec = setup_res.get('ansible_facts', dict()).get('ansible_uptime_seconds')

        if not original_uptime_sec:
            raise Exception("reboot requires ansible_uptime_seconds fact")

        try:
            shutdown_res = self._execute_module(module_name="command", module_args=dict(_raw_params=shutdown_command, _uses_shell=True), delete_remote_tmp=True, task_vars=task_vars)

            if shutdown_res.get('rc') != 0:
                raise Exception('shutdown command failed')
        except Exception as e:
            display.debug("exception, probably ok")
            raise

        display.debug("sleeping %d seconds before attempting to check for reboot and connectivity" % test_delay_sec)
        time.sleep(test_delay_sec)

        def poll_for_new_uptime():
            try:
                setup_res = self._execute_module(module_name="setup", module_args=dict(gather_subset="hardware"), delete_remote_tmp=True, task_vars=task_vars)

                # TODO: calculate this as "last boot time" with ansible_date/time facts via and check for != with appropriate slop instead of absolute <
                new_uptime_sec = setup_res.get('ansible_facts', dict()).get('ansible_uptime_seconds')

                if not original_uptime_sec:
                    return False

                if new_uptime_sec > original_uptime_sec:
                    return False

                return True
            except:
                self._connection.reset()
                raise

        try:
            self.do_until_success_or_timeout(poll_for_new_uptime, reboot_timeout_sec, what_desc="new last boot time")

            result['rebooted'] = True
            result['changed'] = True

        except TimedOutException as toex:
            result['failed'] = True
            result['rebooted'] = True
            result['msg'] = toex.message

        #
        # if rc != 0:
        #     result['failed'] = True
        #     result['rebooted'] = False
        #     result['msg'] = "Shutdown command failed, error text was %s" % stderr
        #     return result
        #
        # def raise_if_port_open():
        #     try:
        #         sock = socket.create_connection((winrm_host, winrm_port), connect_timeout_sec)
        #         sock.close()
        #     except:
        #         return False
        #
        #     raise Exception("port is open")
        #
        # try:
        #     self.do_until_success_or_timeout(raise_if_port_open, shutdown_timeout_sec, what_desc="winrm port down")
        #
        #     def connect_winrm_port():
        #         sock = socket.create_connection((winrm_host, winrm_port), connect_timeout_sec)
        #         sock.close()
        #
        #     self.do_until_success_or_timeout(connect_winrm_port, reboot_timeout_sec, what_desc="winrm port up")
        #
        #     def run_test_command():
        #         display.vvv("attempting post-reboot test command '%s'" % test_command)
        #         # call connection reset between runs if it's there
        #         try:
        #             self._connection._reset()
        #         except AttributeError:
        #             pass
        #
        #         (rc, stdout, stderr) = self._connection.exec_command(test_command)
        #
        #         if rc != 0:
        #             raise Exception('test command failed')
        #
        #     # FUTURE: ensure that a reboot has actually occurred by watching for change in last boot time fact
        #     # FUTURE: add a stability check (system must remain up for N seconds) to deal with self-multi-reboot updates
        #
        #     self.do_until_success_or_timeout(run_test_command, reboot_timeout_sec, what_desc="post-reboot test command success")
        #
        #     result['rebooted'] = True
        #     result['changed'] = True
        #
        # except TimedOutException as toex:
        #     result['failed'] = True
        #     result['rebooted'] = True
        #     result['msg'] = toex.message

        return result
