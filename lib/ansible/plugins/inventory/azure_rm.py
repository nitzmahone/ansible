# Copyright (c) 2018 Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = '''
    name: azure_rm
    plugin_type: inventory
    short_description: Azure Resource Manager inventory plugin
    requirements:
        - TODO
    # TODO: extends_documentation_fragment azure_rm?
    description:
        - Query VM details from Azure Resource Manager
        - Requires a *.azure_rm.yaml YAML configuration file
    options:
        plugin:
            description: marks this as an instance of the 'azure_rm' plugin
            required: true
            choices: ['azure_rm']
        auth_source:
            description: TODO 
            choices: ['cli']
            default: cli
        profile:
            description: TODO
        subscription_id:
            description: TODO
        client_id:
            description: TODO
        secret:
            description: TODO
        tenant:
            description: TODO
        ad_user:
            description: TODO
        password:
            description: TODO
        cloud_environment:
            description: TODO
            default: AzureCloud
        cert_validation_mode:
            description: TODO
            default: validate
        api_profile:
            description: TODO
            default: latest
        adfs_authority_url:
            description: TODO           
        include_vm_resource_groups:
            description: A list of resource group names to search for virtual machines. '*' will include all resource 
                groups in the subscription.
            default: ['*']
        include_vmss_resource_groups:
            description: A list of resource group names to search for virtual machine scale sets (VMSSs). '*' will
                include all resource groups in the subscription.
            default: []
        hostname_sources:
          # TODO: implemented?
          description: A list in order of precedence for hostname variables. You can use the options specified in
              U(http://docs.aws.amazon.com/cli/latest/reference/ec2/describe-instances.html#options). To use tags as hostnames
              use the syntax tag:Name=Value to use the hostname Name_Value, or tag:Name to use the value of the Name tag.
        # TODO: filters: ?  
        batch_fetch:
          description: TODO 
          default: true
'''

EXAMPLES = '''
# TODO
'''



import hashlib
import json
import re

try:
    from queue import Queue, Empty
except ImportError:
    from Queue import Queue, Empty

from collections import namedtuple
from ansible import release
from ansible.plugins.inventory import BaseInventoryPlugin, Constructable, Cacheable
from ansible.module_utils.six import iteritems
from ansible.module_utils.azure_rm_common import AzureRMAuth
from ansible.utils.display import Display
from azure.common.credentials import get_azure_cli_credentials
from azure.common.cloud import get_cli_active_cloud
from itertools import chain
from msrest import ServiceClient, Serializer, Deserializer
from msrestazure import AzureConfiguration
from msrestazure.polling.arm_polling import ARMPolling


class AzureRMRestConfiguration(AzureConfiguration):
    def __init__(self, credentials, subscription_id, base_url=None):

        if credentials is None:
            raise ValueError("Parameter 'credentials' must not be None.")
        if subscription_id is None:
            raise ValueError("Parameter 'subscription_id' must not be None.")
        if not base_url:
            base_url = 'https://management.azure.com'

        super(AzureRMRestConfiguration, self).__init__(base_url)

        self.add_user_agent('ansible-dynamic-inventory/{}'.format(release.__version__))

        self.credentials = credentials
        self.subscription_id = subscription_id


UrlAction = namedtuple('UrlAction', ['url', 'api_version', 'handler', 'handler_args'])


class InventoryModule(BaseInventoryPlugin, Constructable):

    NAME = 'azure_rm'

    def __init__(self):
        super(InventoryModule, self).__init__()

        self._serializer = Serializer()
        self._deserializer = Deserializer()
        self._hosts = []

        # TODO: use API profiles with defaults
        self._compute_api_version = '2017-03-30'
        self._network_api_version = '2015-06-15'

        self._default_header_parameters = {'Content-Type': 'application/json; charset=utf-8'}

        self._request_queue = Queue()

        self.azure_auth = None

        self._batch_fetch = False


    def verify_file(self, path):
        '''
            :param loader: an ansible.parsing.dataloader.DataLoader object
            :param path: the path to the inventory config file
            :return the contents of the config file
        '''
        if super(InventoryModule, self).verify_file(path):
            if re.match(r'.+\.azure_rm.y(a)?ml$', path):
                return True
        display.debug("azure_rm inventory filename must match '*.azure_rm.yml' or '*.azure_rm.yaml'")
        return False

    def parse(self, inventory, loader, path, cache=True):
        super(InventoryModule, self).parse(inventory, loader, path)

        self._read_config_data(path)
        self._batch_fetch = self.get_option('batch_fetch')

        try:
            self._credential_setup()

            # TODO: parse filters
            # TODO: add caching support

            self._get_hosts()
            self._process_groups()
        except Exception as ex:
            raise

    def _credential_setup(self):
        auth_options = dict(
            auth_source=self.get_option('auth_source'),
            profile=self.get_option('profile'),
            subscription_id=self.get_option('subscription_id'),
            client_id=self.get_option('client_id'),
            secret=self.get_option('secret'),
            tenant=self.get_option('tenant'),
            ad_user=self.get_option('ad_user'),
            password=self.get_option('password'),
            cloud_environment=self.get_option('cloud_environment'),
            cert_validation_mode=self.get_option('cert_validation_mode'),
            api_profile=self.get_option('api_profile'),
            adfs_authority_url=self.get_option('adfs_authority_url')
        )

        self.azure_auth = AzureRMAuth(**auth_options)

        self._clientconfig = AzureRMRestConfiguration(self.azure_auth.credentials, self.azure_auth.subscription_id, self.azure_auth._cloud_environment.endpoints.resource_manager)
        self._client = ServiceClient(self._clientconfig.credentials, self._clientconfig)

    def _enqueue_get(self, url, api_version, handler, handler_args={}):
        self._request_queue.put_nowait(UrlAction(url=url, api_version=api_version, handler=handler, handler_args=handler_args))

    def _enqueue_vm_list(self, rg='*'):
        if not rg or rg == '*':
            url = '/subscriptions/{subscriptionId}/providers/Microsoft.Compute/virtualMachines'
        else:
            url = '/subscriptions/{subscriptionId}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines'

        url = url.format(subscriptionId=self._clientconfig.subscription_id, rg=rg)
        self._enqueue_get(url=url, api_version=self._compute_api_version, handler=self._on_vm_page_response)

    def _enqueue_vmss_list(self, rg=None):
        if not rg or rg == '*':
            url = '/subscriptions/{subscriptionId}/providers/Microsoft.Compute/virtualMachineScaleSets'
        else:
            url = '/subscriptions/{subscriptionId}/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachineScaleSets'

        url = url.format(subscriptionId=self._clientconfig.subscription_id, rg=rg)
        self._enqueue_get(url=url, api_version=self._compute_api_version, handler=self._on_vmss_page_response)

    def _get_hosts(self):
        for vm_rg in self.get_option('include_vm_resource_groups'):
            self._enqueue_vm_list(vm_rg)

        for vmss_rg in self.get_option('include_vmss_resource_groups'):
            self._enqueue_vmss_list(vmss_rg)

        if self._batch_fetch:
            self._process_queue_batch()
        else:
            self._process_queue_serial()

        for h in self._hosts:
            inventory_hostname = self._get_hostname(h)
            self.inventory.add_host(inventory_hostname)
            # TODO: configurable default IP list?
            self.inventory.set_variable(inventory_hostname, "ansible_host", next(chain(h.hostvars['public_ipv4_addresses'],h.hostvars['private_ipv4_addresses'])))
            for k, v in iteritems(h.hostvars):
                # TODO: configurable prefix?
                self.inventory.set_variable(inventory_hostname, k, v)


    # TODO: do we want the configurable logic here or down in the actual objects?
    def _get_hostname(self, host):
        #TODO: configurable hostname sources
        return host.default_inventory_hostname

    def _process_groups(self):
        pass

    def _process_queue_serial(self):
        # FUTURE: parallelize serial fetch with worker threads?
        try:
            while True:
                item = self._request_queue.get_nowait()
                resp = self.send_request(item.url, item.api_version)
                item.handler(resp, **item.handler_args)
        except Empty:
            pass

    def _on_vm_page_response(self, response):
        next_link = response.get('nextLink')

        if next_link:
            self._enqueue_get(url=next_link, api_version=self._compute_api_version, handler=self._on_vm_page_response)

        for h in response['value']:
            self._hosts.append(AzureHost(h, self))

    def _on_vmss_page_response(self, response):
        next_link = response.get('nextLink')

        if next_link:
            self._enqueue_get(url=next_link, api_version=self._compute_api_version, handler=self._on_vmss_page_response)

        # TODO: filter VMSSs by config
        for vmss in response['value']:
            print("vmss yay")
            url = '{0}/virtualMachines'.format(vmss['id'])
            # VMSS instances look close enough to regular VMs that we can share the handler impl...
            self._enqueue_get(url=url, api_version=self._compute_api_version, handler=self._on_vm_page_response)

    # use the undocumented /batch endpoint to bulk-send up to 500 requests in a single round-trip
    #
    def _process_queue_batch(self):
        while True:
            batch_requests = []
            batch_item_index = 0
            batch_response_handlers = []
            try:
                while batch_item_index < 500:
                    item = self._request_queue.get_nowait()

                    query_parameters = {'api-version': item.api_version}
                    req = self._client.get(item.url, query_parameters)

                    batch_requests.append(dict(httpMethod="GET", url=req.url))
                    batch_response_handlers.append(item)
                    batch_item_index += 1
            except Empty:
                pass

            if not batch_requests:
                break

            batch_resp = self._send_batch(batch_requests)

            # TODO: validate batch response count matches request count

            for idx, r in enumerate(batch_resp['responses']):
                # TODO: check individual response codes
                item = batch_response_handlers[idx]
                # TODO: store errors from individual handlers or just let them pop?
                item.handler(r['content'], **item.handler_args)

    def _send_batch(self, batched_requests):
        url = '/batch'
        query_parameters = {'api-version': '2015-11-01'}

        body_obj = dict(requests=batched_requests)

        body_content = self._serializer.body(body_obj, 'object')

        request = self._client.post(url, query_parameters)
        initial_response = self._client.send(request, self._default_header_parameters, body_content)

        # TODO: configurable timeout?
        poller = ARMPolling(timeout=2)
        poller.initialize(client=self._client,
                          initial_response=initial_response,
                          deserialization_callback=lambda r: self._deserializer('object', r))

        poller.run()

        return poller.resource()

    def send_request(self, url, api_version):
        query_parameters = { 'api-version': api_version }
        req = self._client.get(url, query_parameters)
        resp = self._client.send(req, self._default_header_parameters, stream=False)

        if resp.status_code != 200:
            # TODO: error handler
            raise Exception("bang")
        content = resp.content

        return json.loads(content)


# VM list (all, N resource groups): VM -> InstanceView, N NICs, N PublicIPAddress)
# VMSS VMs (all SS, N specific SS, N resource groups?): SS -> VM -> InstanceView, N NICs, N PublicIPAddress)

class AzureHost(object):
    def __init__(self, vm_model, inventory_client):
        self._inventory_client = inventory_client
        self._vm_model = vm_model

        # determine if this is a VMSS instance
        self.is_vmss_instance = (vm_model['type'] == 'Microsoft.Compute/virtualMachineScaleSets/virtualMachines')

        self.instanceview = None
        self.nics = []

        # Azure often doesn't provide a globally-unique filename, so use resource name + a chunk of ID hash
        self.default_inventory_hostname = '{0}_{1}'.format(vm_model['name'], hashlib.sha1(vm_model['id']).hexdigest()[0:4])

        self._hostvars = {}

        inventory_client._enqueue_get(url="{0}/instanceView".format(vm_model['id']), api_version=self._inventory_client._compute_api_version, handler=self._on_instanceview_response)

        nic_refs = vm_model['properties']['networkProfile']['networkInterfaces']
        for nic in nic_refs:
            # single-nic instances don't set primary, so figure it out...
            is_primary = nic.get('properties', {}).get('primary', len(nic_refs) == 1)
            inventory_client._enqueue_get(url=nic['id'], api_version=self._inventory_client._network_api_version, handler=self._on_nic_response, handler_args=dict(is_primary=is_primary))

        if self.is_vmss_instance:
            print("vmss instance yay")
        else:
            print("host yay")

    @property
    def hostvars(self):
        if self._hostvars != {}:
            return self._hostvars

        new_hostvars = dict(
            public_ipv4_addresses=[],
            public_dns_hostnames=[],
            private_ipv4_addresses=[],
            id=self._vm_model['id'],
            location=self._vm_model['location'],
            name=self._vm_model['name'],
            provisioning_state=self._vm_model['properties']['provisioningState'],
            vmid=self._vm_model['properties']['vmId'],
        )

        # set nic-related values from the primary NIC first
        for nic in sorted(self.nics, key=lambda n: n.is_primary, reverse=True):
            # and from the primary IP config per NIC first
            for ipc in sorted(nic._nic_model['properties']['ipConfigurations'], key=lambda i: i['properties']['primary'], reverse=True):
                private_ip = ipc['properties'].get('privateIPAddress')
                if private_ip:
                    new_hostvars['private_ipv4_addresses'].append(private_ip)
                pip_id = ipc['properties'].get('publicIPAddress', {}).get('id')
                if pip_id:
                    pip = nic.public_ips[pip_id]
                    new_hostvars['public_ipv4_addresses'].append(pip._pip_model['properties']['ipAddress'])
                    pip_fqdn = pip._pip_model['properties'].get('dnsSettings', {}).get('fqdn')
                    if pip_fqdn:
                        new_hostvars['public_dns_hostnames'].append(pip_fqdn)

        self._hostvars = new_hostvars

        return self._hostvars

    def _on_instanceview_response(self, vm_instanceview_model):
        print("instanceview yay")

    def _on_nic_response(self, nic_model, is_primary=False):
        print("nic yay")
        nic = AzureNic(nic_model=nic_model, inventory_client=self._inventory_client, is_primary=is_primary)
        # TODO: lock+sort for repeatable order?
        self.nics.append(nic)


class AzureNic(object):
    def __init__(self, nic_model, inventory_client, is_primary=False):
        self._nic_model = nic_model
        self.is_primary = is_primary
        self._inventory_client = inventory_client

        self.public_ips = {}

        for ipc in nic_model['properties']['ipConfigurations']:
            pip = ipc['properties'].get('publicIPAddress')
            if pip:
                self._inventory_client._enqueue_get(url=pip['id'], api_version=self._inventory_client._network_api_version, handler=self._on_pip_response)



    def _on_pip_response(self, pip_model):
        print("pip yay: %s" % pip_model['properties']['ipAddress'])
        self.public_ips[pip_model['id']] = AzurePip(pip_model)


class AzurePip(object):
    def __init__(self, pip_model):
        self._pip_model = pip_model

    @property
    def fqdn(self):
        return self._pip_model['properties'].get('dnsSettings', {}).get('fqdn')

