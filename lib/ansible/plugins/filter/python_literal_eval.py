from ast import literal_eval


class FilterModule(object):
    """Python literal eval filter (replaces convert_data=True)"""

    def filters(self):
        return {
            "python_literal_eval": literal_eval,
        }
