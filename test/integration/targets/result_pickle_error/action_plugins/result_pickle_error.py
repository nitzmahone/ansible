# -*- coding: utf-8 -*-
# Copyright: Contributors to the Ansible project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import annotations

import typing as t

from ansible.plugins.action import ActionBase


class CannotPickleMe:
    def __getstate__(self) -> t.NoReturn:
        raise Exception("I cannot be pickled!")


class ActionModule(ActionBase):

    def run(self, tmp=None, task_vars=None):
        return {'obj': CannotPickleMe()}
