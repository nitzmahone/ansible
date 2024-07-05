from __future__ import annotations

import pytest

from ansible.template.templar import Templar, TemplateOptions
from ansible.template.utils import TemplateContext


@pytest.fixture
def with_template_context():
    templar = Templar()

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions.DEFAULT, stop_on_template=False):
        yield
