from configparser import ConfigParser


def get_inventory(path: str) -> dict[str, dict[str, str]]:
    config = ConfigParser()
    config.read(path)
    inventory = {}
    for host, section in config.items():
        if host == 'DEFAULT':
            continue
        inventory[host] = dict(config.items(host))
        inventory[host]['inventory_hostname'] = host

    return inventory

