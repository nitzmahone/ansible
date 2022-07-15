import hashlib
import json
import typing as t
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# leave this swappable for, eg FIPS
hash_type = hashlib.sha1
dump_hashes = False


def hash_args(args: dict[str, t.Any]) -> str:
    flattened = json.dumps(args, sort_keys=True)
    digest = hash_type()
    digest.update(flattened.encode())
    key = digest.hexdigest()

    if dump_hashes:
        logger.debug('hash[%s] from %s', key, flattened)

    return key
