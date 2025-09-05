import logging
import time
import struct
import importlib.resources as resources

from .network import IpAddress, Mac
from .tengbe import TenGbe, read_memory_map_definition

TENGBE_UNIFIED_MMAP_TXT = resources.files('casperfpga') / 'tengbe_mmap.txt'
TENGBE_MMAP_LEGACY_TXT  = resources.files('casperfpga') / 'fortygbe_mmap_legacy.txt'


class FortyGbe(TenGbe):
    """

    """

    def __init__(self, parent, name, address, length_bytes,
                 device_info=None, position=None):
        """
        Implements the Gbe class. This is normally initialised from_device_info.

        :param parent: The parent object, normally a CasperFpga instance
        :param name: The name of the device
        :param position: Optional - defaulted to None
        :param address: Integer
        :param length_bytes: Integer
        :param device_info: Information about the device
        """
        self.position = position
        self.logger = parent.logger
        super(FortyGbe, self).__init__(
            parent, name, address, length_bytes, device_info)

    def _check_memmap_compliance(self):
        """
        Look at the first word of the core's memory map and try to
        figure out if it compliant with the harmonized ethernet map.
        This isn't flawless, but unless the user sets a very weird
        MAC address for their core (which is what the old core's map
        stored in register 0, it should be OK).
        """
        x = self.parent.read(self.name, 4)
        cpu_tx_en, cpu_rx_en, rev, core_type = struct.unpack('4B', x)
        self.logger.debug("%s: cpu_tx_en %d, cpu_rx_en: %d, rev: %d, core_type: %d" % (self.name, cpu_tx_en, cpu_rx_en, rev, core_type))
        if (cpu_tx_en > 1) or (cpu_rx_en > 1) or ((core_type != 4) and (core_type != 3)):
            return False
        else:
            return True

    def post_create_update(self, raw_device_info):
        """
        Update the device with information not available at creation.

        :param raw_device_info: info about this block that may be useful
        """
        self.registers = {'tx': [], 'rx': []}
        for register in self.parent.registers:
            if register.name.find(self.name + '_') == 0:
                name = register.name.replace(self.name + '_', '')
                if name[0:2] == 'tx' and name.find('txs_') == -1:
                    self.registers['tx'].append(register.name)
                elif name[0:2] == 'rx' and name.find('rxs_') == -1:
                    self.registers['rx'].append(register.name)
                else:
                    if not (name.find('txs_') == 0 or name.find('rxs_') == 0):
                        self.parent.logger.warn('%s: odd register name %s under Gbe '
                                    'block' % (self.fullname, register.name))
        self.snaps = {'tx': [], 'rx': []}
        snapnames = self.parent.snapshots.names()
        for txrx in ['r', 't']:
            snapshot_index=0
            snapshot_found=True
            while snapshot_found:
                name = self.name + '_%sxs%i_ss' %(txrx,snapshot_index)
                if name in snapnames:
                    self.snaps['%sx' % txrx].append(self.parent.snapshots[name])
                    snapshot_index+=1
                else:
                    snapshot_found=False
        self.get_gbe_core_details()

    def _wbone_rd(self, addr):
        """

        :param addr: 
        """
        self.logger.debug("%s: reading address 0x%x" % (self.name, addr))
        return self.parent.transport.read_wishbone(addr)

    def _wbone_wr(self, addr, val):
        """

        :param addr: 
        :param val: 
        """
        return self.parent.transport.write_wishbone(addr, val)


    def get_mac(self):
        """
        Retrieve core's configured MAC address from HW.

        :return: Mac object
        """
        return self.mac

    def get_ip(self):
        """
        Retrieve core's IP address from HW.

        :return: IpAddress object
        """
        return self.ip_address

    def get_port(self):
        """
        Retrieve core's port from HW.

        :return:  int
        """
        return self.port

    def get_stats(self):
        """
        Retrieves some statistics for this core.
        Needs to have the debug registers compiled-in to the core at 32b.
        """
        rv = {}
        first = self.read_counters()
        time.sleep(0.5)
        second = self.read_counters()

        name = self.name
        txvldcnt = '%s_txvldctr' % name
        rxvldcnt = '%s_rxvldctr' % name
        txcnt = '%s_txctr' % name
        rxcnt = '%s_rxctr' % name

        txofcnt = '%s_txofctr' % name
        rxofcnt = '%s_rxofctr' % name
        rxbadcnt = '%s_rxbadctr' % name

        if int(self.block_info['debug_ctr_width']) != 32:
            raise RuntimeError("Please recompile your design with larger,"\
               " >28b, debug registers to use this function.")
            return

        if txvldcnt in first:
            if second[txvldcnt] >= first[txvldcnt]:
                rv['tx_gbps'] = 2 * 256 / 1e9 * (second[txvldcnt] - first[txvldcnt])
            else:
                rv['tx_gbps'] = 2 * 256 / 1e9 * (
                    second[txvldcnt] - first[txvldcnt] + (2 ** 32))

        if rxvldcnt in first:
            if second[rxvldcnt] >= first[rxvldcnt]:
                rv['rx_gbps'] = 2 * 256 / 1e9 * (second[rxvldcnt] - first[rxvldcnt])
            else:
                rv['rx_gbps'] = 2 * 256 / 1e9 * (
                    second[rxvldcnt] - first[rxvldcnt] + (2 ** 32))

        if txcnt in first:
            rv['tx_pkt_cnt'] = second[txcnt]
            if second[txcnt] >= first[txcnt]:
                rv['tx_pps'] = 2 * (second[txcnt] - first[txcnt])
            else:
                rv['tx_pps'] = 2 * (second[txcnt] - first[txcnt]) + (2 ** 32)

        if rxcnt in first:
            rv['rx_pkt_cnt'] = second[rxcnt]
            if second[rxcnt] >= first[rxcnt]:
                rv['rx_pps'] = 2 * (second[rxcnt] - first[rxcnt])
            else:
                rv['rx_pps'] = 2 * (second[rxcnt] - first[rxcnt]) + (2 ** 32)

        if txofcnt in second:
            rv['tx_over'] = second['%s_txofctr' % name]
        if rxofcnt in second:
            rv['rx_over'] = second['%s_rxofctr' % name]
        if rxbadcnt in second:
            rv['rx_bad_pkts'] = second['%s_rxbadctr' % name]
        return rv

    @staticmethod
    def convert_128_to_64(w128):
        return [(w128 >> (64-(ctr*64))) & (2 ** 64 - 1) for ctr in range(2)]

    @staticmethod
    def process_snap_data(d):
        # convert the 256-bit data to 64-bit data
        d64 = {k: [] for k in d.keys()}
        d64['data'] = []
        for ctr in range(len(d['data_msw'])):
            d64['data'].extend(FortyGbe.convert_128_to_64(d['data_msw'][ctr]))
            d64['data'].extend(FortyGbe.convert_128_to_64(d['data_lsw'][ctr]))
            for k in d.keys():
                if k == 'eof':
                    d64[k].extend([0] * 3)
                    d64[k].append(d[k][ctr])
                elif ((k!='data_msw') and (k!='data_lsw') and (k!='data')):
                    for ctr4 in range(4):
                        d64[k].append(d[k][ctr])
        return d64

    def read_txsnap(self):
        """
        Read the TX snapshot embedded in this GbE yellow block
        """
        d = self.snaps['tx'][0].read()['data']
        for snap in self.snaps['tx'][1:]:
            d.update(snap.read(arm=False)['data'])
        return FortyGbe.process_snap_data(d)

    def read_rxsnap(self):
        """
        Read the RX snapshot embedded in this GbE yellow block
        """
        d = self.snaps['rx'][0].read()['data']
        for snap in self.snaps['rx'][1:]:
            d.update(snap.read(arm=False)['data'])
        for key in ['eof_in', 'valid_in', 'ip_in', ]:
            if key in d:
                d[key.replace('_in', '')] = d[key]
                d.pop(key)
        return FortyGbe.process_snap_data(d)

# end
