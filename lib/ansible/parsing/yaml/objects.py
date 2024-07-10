from __future__ import annotations

# deprecated: description='deprecate ansible.parsing.yaml.objects module' core_version='2.19'


class AnsibleBaseYAMLObject:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(f"{type(self)} should never be instantiated")


class AnsibleMapping(AnsibleBaseYAMLObject):
    pass


class AnsibleUnicode(AnsibleBaseYAMLObject):
    pass


class AnsibleSequence(AnsibleBaseYAMLObject):
    pass


class AnsibleVaultEncryptedUnicode(AnsibleBaseYAMLObject):
    # DTFIX-MERGE: future deprecate this?
    pass
