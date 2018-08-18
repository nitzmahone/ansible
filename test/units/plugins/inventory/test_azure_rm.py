# Copyright (c) 2018 Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

import pytest
import json

from ansible.plugins.inventory.azure_rm import InventoryClient
from mock import MagicMock
from msrestazure.tools import parse_resource_id

class FakeAzure(object):
    def __init__(self):
        self._subscriptions = {}

    @staticmethod
    def generate_cloud():
        rg_count = 1
        vm_count = 100
        vmss_count = 10
        vmss_vm_count = 2

        az = FakeAzure
        newsub = FakeAzureSubscription()
        az.add_subscription(sub=newsub)

        for i in range(0, rg_count):
            newrg = FakeAzureResourceGroup("newrg{0}".format(i))
            newsub.add_rg(newrg)

            for vmidx in range(0, vm_count):
                newvm = FakeAzureVM("newvm{0}".format(vmidx))
                newrg.add_vm(newvm)

                for

        return az

    def add_subscription(self, sub):
        if self._subscriptions.get(sub.subscription_id):
            raise Exception("cloud already contains subscription {0}".format(sub.subscription_id))

        self._subscriptions[sub.subscription_id] = sub

    def get_url(self, url):
        bits = parse_resource_id(url)

    def batch_get(self, batch_req):
        pass


class FakeAzureSubscription(object):
    def __init__(self, subscription_id='00000000-0000-0000-0000-000000000000'):
        self.subscription_id = subscription_id
        self._rg = {}

    def add_rg(self, rg):
        if self._rg.get(rg.name):
            raise Exception("resource group {0} already exists in subscription {1}".format(rg.name, self.subscription_id))

        self._rg[rg.name] = rg

    def list_vm(self):
        # iterate RGs, list vm
        pass

    def list_vmss(self):
        # iterate RGs, list vmss
        pass


class FakeAzureResourceGroup(object):
    def __init__(self, name, location="westus", tags={}):
        self.name = name
        self.location = location
        self.tags = tags
        self._vm = {}
        self._vmss = {}
        self._nic = {}
        self._pip = {}

    def add_vm(self, FakeAzureVM):
        pass

    def list_vm(self):
        pass

    def list_vmss(self):
        pass




class FakeAzureVM(object):
    pass

class FakeAzureVMSS(object):
    pass

class FakeAzureNIC(object):
    pass

class FakeAzurePIP(object):
    pass



    def add_vm(self, resource_group, name):
        # TODO: urlencode VM names here or handle in lookups
        if not self._rg.get(resource_group):
            self._rg[resource_group] = dict(tags={}, vm={}, vmss={})

        if self._rg[resource_group]['vm'].get(name):
            raise Exception('resource group {0} already contains a vm named {1}'.format(resource_group, name))

        self._rg[resource_group]['vm'][name] = dict(
            type='Microsoft.Compute/virtualMachines',
            properties=dict(
                networkProfile=dict(
                    networkInterfaces=[]
                )
            ),
        )

    @staticmethod
    def get_nic(nic_config):




@pytest.fixture
def inventory_client():
    return


