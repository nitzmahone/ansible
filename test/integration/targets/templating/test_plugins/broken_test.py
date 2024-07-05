from __future__ import annotations


class Broken:
    @property
    def _accept_undefined_args(self):
        raise Exception('boom')


class TestModule:
    def tests(self):
        return {
            'broken': Broken(),
        }
