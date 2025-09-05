import logging
import sys

from .transport_katcp import KatcpTransport
import katcp
from struct import *

from .network import IpAddress, Mac
#from hundredgbe import HundredGbe
from .transport import Transport
from .utils import create_meta_dictionary, get_hostname, get_kwarg


class AlveoFunctionError(RuntimeError):
  """Not an Alveo function"""
  pass

#inherit from katcp transport for now (TODO)
class AlveoTransport(KatcpTransport):

  # def __init__(self, host_ip):
  def __init__(self, **kwargs):
      """

      :param host:
      :param port:
      :param timeout:
      :param connect:
      """
      port = get_kwarg('port', kwargs, 7147)
      timeout = get_kwarg('timeout', kwargs, 10)
      Transport.__init__(self, **kwargs)

      # Create instance of self.logger
      # try:
      #     self.logger = kwargs['logger']
      # except KeyError:
      #     self.logger = logging.getLogger(__name__)

      try:
          self.parent = kwargs['parent_fpga']
          self.logger = self.parent.logger
      except KeyError:
          errmsg = 'parent_fpga argument not supplied when creating katcp device'
          raise RuntimeError(errmsg)

      new_connection_msg = '*** NEW CONNECTION MADE TO {} ***'.format(self.host)
      self.logger.info(new_connection_msg)

      katcp.CallbackClient.__init__(
            self, self.host, port, tb_limit=20,
            timeout=timeout, logger=self.logger, auto_reconnect=True)
      self.system_info = {}
      self.unhandled_inform_handler = None
      self._timeout = timeout
      self.connect()
      self.logger.info('%s: port(%s) created and connected.' % (self.host, port))
      self.sensor_list = {};

      self.hgbe_base_addr = 0x100000
      
      self.hgbe_reg_map = {'src_mac_addr_lower'  : 0x00,
                           'src_mac_addr_upper'  : 0x04,
                           'dst_mac_addr_lower'  : 0x0C,
                           'dst_mac_addr_upper'  : 0x10,
                           'dst_ip_addr'         : 0x24,
                           'src_ip_addr'         : 0x28,
                           'fabric_port'         : 0x2C,
                           'udp_count'           : 0x4C,
                           'ping_count'          : 0x50,
                           'arp_count'           : 0x54,
                           'dropped_mac_count'   : 0x60,
                           'dropped_ip_count'    : 0x64,
                           'dropped_port_count'  : 0x68}

      # The base adressses for the HBM stacks/banks in the Xilinx Alveo.
      self.hbm_stack_base_addr = {0 : 0x400000,
                                  1 : 0x800000}

      # The memory controller region bitfields for the HBM are provided below as an extract from:
      # AXI High Bandwidth Memory Controller v1.0 LogiCORE IP Product Guide (PG276) found at https://docs.xilinx.com/r/en-US/pg276-axi-hbm/Memory-Controller-Register-Map
      self.hbm_controller_regions = {0 : 0b01000,
                                     1 : 0b01100,
                                     2 : 0b01001,
                                     3 : 0b01101,
                                     4 : 0b01010,
                                     5 : 0b01110,
                                     6 : 0b01011,
                                     7 : 0b01111}

      # The memory controller register map for the HBM ECC and Status registers, Activity Monitor/Status registers and Activity Monitor Tracking registers are provided below as an extract from: 
      # AXI High Bandwidth Memory Controller v1.0 LogiCORE IP Product Guide (PG276) found at https://docs.xilinx.com/r/en-US/pg276-axi-hbm/Memory-Controller-Register-Map
      self.hbm_reg_map = { 'CFG_ECC_CORRECTION_EN': 0x05800,
                           'INIT_ECC_SCRUB_EN': 0x05804,
                           'CFG_ECC_SCRUB_PERIOD': 0x05810,
                           'INIT_WRITE_DATA_1B_ECC_ERROR_GEN_PS0': 0x0584c,
                           'INIT_WRITE_DATA_2B_ECC_ERROR_GEN_PS0': 0x05850,
                           'INIT_WRITE_DATA_1B_ECC_ERROR_GEN_PS1': 0x05854,
                           'INIT_WRITE_DATA_2B_ECC_ERROR_GEN_PS1': 0x05858,
                           'STAT_ECC_ERROR_1BIT_CNT_PS0': 0x05828,
                           'STAT_ECC_ERROR_1BIT_CNT_PS1': 0x05834,
                           'STAT_ECC_ERROR_2BIT_CNT_PS0': 0x0582c,
                           'STAT_ECC_ERROR_2BIT_CNT_PS1': 0x05838,
                           'INIT_ECC_ERROR_CLR': 0x05818,
                           'CFG_ECC_1BIT_INT_THRESH': 0x0585c,
                           'STAT_INT_ECC_1BIT_THRESH_PS0': 0x05864,
                           'STAT_INT_ECC_1BIT_THRESH_PS1': 0x05868,
                           'STAT_DFI_INIT_COMPLETE': 0x10034,
                           'STAT_DFI_CATTRIP': 0x1004c,
                           'INIT_AM_REPEAT': 0x13800,
                           'INIT_AM_SINGLE_EN': 0x13804,
                           'CFG_AM_INTERVAL': 0x13808,
                           'STAT_AM_COMPLETE': 0x1380c,
                           'AM_WR_CMD_PS0': 0x13814,
                           'AM_WR_CMD_PS1': 0x13818,
                           'AM_WR_AP_CMD_PS0': 0x13820,
                           'AM_WR_AP_CMD_PS1': 0x13824,
                           'AM_RD_CMD_PS0': 0x1382c,
                           'AM_RD_CMD_PS1': 0x13830,
                           'AM_RD_AP_CMD_PS0': 0x13838,
                           'AM_RD_AP_CMD_PS1': 0x1383c,
                           'AM_REFRESH_CMD_PS0': 0x13844,
                           'AM_REFRESH_CMD_PS1': 0x13848,
                           'AM_ACT_CMD_PS0': 0x13850,
                           'AM_ACT_CMD_PS1': 0x13854,
                           'AM_PRECHARGE_CMD_PS0': 0x1385c,
                           'AM_PRECHARGE_CMD_PS1': 0x13860,
                           'AM_PRECHARGE_ALL_CMD_PS0': 0x13868,
                           'AM_PRECHARGE_ALL_CMD_PS1': 0x1386c,
                           'AM_POWER_DOWN': 0x13870,
                           'AM_SELF_REFRESH': 0x13874,
                           'AM_RD_TO_WR_SWITCH_PS0': 0x1387c,
                           'AM_RD_TO_WR_SWITCH_PS1': 0x13880,
                           'AM_RO_AGE_LIMIT_PS0': 0x13888,
                           'AM_RO_AGE_LIMIT_PS1': 0x1388c,
                           'AM_RMW_CYCLE_PS0': 0x13894,
                           'AM_RMW_CYCLE_PS1': 0x13898}


  @staticmethod
  def test_host_type(host_ip, port, timeout=5):
    """
    Is this host_ip assigned to an Alveo?
    """
    #print("new alveo-test-host")
    try:
      board = katcp.CallbackClient(host=host_ip, port=port,
      timeout=timeout, auto_reconnect=False)
      board.setDaemon(True)
      board.start()
      connected = board.wait_connected(timeout)

      #TODO this read needs to be standardized with the final mem map
      #reply,informs = board.blocking_request(katcp.Message.request('alveo-read','0x28000' ),timeout=timeout)
      #reply,informs = board.blocking_request(katcp.Message.request('alveo-read','0x90004' ),timeout=timeout)
      reply,informs = board.blocking_request(katcp.Message.request('alveo-memread','0x28000' ),timeout=timeout)
      #reply,informs = board.blocking_request(katcp.Message.request('wordread','id' ),timeout=timeout)
      board.stop()

      args = [(i.arguments[0], i.arguments[1]) for i in informs]
      if args[0][1].decode() == '0x74736574':
     # if args[0][1] == '0x00000000':
      #if args[0][1] == '0xDECADE05':
      #if reply.arguments[1] == '0xdecade05':       #for wordread katcp msg
        #print("Seems to be an ALVEO")   #TODO to be removed
        return True
      else:
        #print("Seems not to be an ALVEO")   #TODO to be removed
        return False

    except AttributeError:
      #print("alveo-error")
      raise RuntimeError("Please ensure that katcp-python >=v0.6.3 is being used")

    except Exception:
      #print("alveo-failed")
      return False


  def reset(self):
    """
    reset the ALVEO
    :return: True/False
    """
    reply, _ = self.katcprequest(name='alveo-reset', request_timeout=self._timeout)
    ret = reply.arguments[0]

    #ensure reply is a string literal
    try:
        ret = ret.decode()
    except (UnicodeDecodeError, AttributeError):
        pass
    return ret == 'ok'


  def memread(self, addr):
    """
    :param addr: absolute memory address from which to read (numeric hex or dec value)
    :return: string of the read value in hexadecimal
    """
    assert(type(addr) == int), 'Please supply numeric address (hex or dec)!'
    addr_str='0x{:x}'.format(addr)

    reply,informs = self.katcprequest(name='alveo-memread', request_timeout=self._timeout, require_ok=True,
        request_args=(addr_str,))   #note-to-self (rvw) this comma is NB when args get unpacked
    #print reply.arguments
    args = [(i.arguments[0], i.arguments[1]) for i in informs]

    return args[0][1].decode()


  def memwrite(self, addr, data):
    """
    :param addr: absolute memory address to write to (numeric hex or dec value)
    :param dataword: numeric data to write (four-byte word)
    :return: True or False
    """
    assert(type(addr) == int), 'Please supply numeric address (hex or dec)!'
    assert(type(data) == int), 'Please supply numeric data (hex or dec)!'
    assert(addr < (32*1024*1024-4)), 'Please supply address within Alveo design memory range'
    #we know that the alveo xdma bar region0 is set to 32M NOTE this could
    #change during dev but should be a spec once the pcie region0 size is fixed

    addr_str='0x{:x}'.format(addr)
    data_str='0x{:x}'.format(data)

    reply,informs = self.katcprequest(name='alveo-memwrite', request_timeout=self._timeout, require_ok=True,
        request_args=(addr_str,data_str,))   #note-to-self (rvw) the trailing comma is NB when args get unpacked
    #print reply.arguments
    args = [(i.arguments[0], i.arguments[1]) for i in informs]
    #print args

    return args[0][1] == bytes(data_str, 'utf-8')

  def wordwrite(self, device_name, data, offset=0, verify=True):
    """
    :param device_name: name of memory device from which to read
    :param data: the numeric data to write, in hex. or dec.
    :param offset: the offset, in bytes, at which to write
    :param verify: verify the operation by reading back data
    :return: True or False
    """
    #extend the functionality of blindwrite
    #assert(type(data) == str), 'Please supply data in string format'
    assert(type(data) == int), 'Please supply numeric data (hex or dec)'
    #data_int = int(data, base=16)
    assert(data < pow(2,32)), 'Please supply a 32-bit-bounded data word'
    data_packed = pack('I', data) 
    super(AlveoTransport, self).blindwrite(device_name, data_packed, offset)
    if verify == True:
      verify_data_str_hex = self.wordread(device_name)
      #return verify_data_str.lower() == data.lower()
      return int(verify_data_str_hex,base=16) == data


  def upload_to_ram_and_program(self, filename, timeout=120):  #TODO this timeout may be too short for large images
    """
    Upload an FPG file to the Alveo.

    :param filename: the file to upload
    :param timeout: how long to wait, seconds
    :return: True upon success
    """
    self.upload_to_flash(filename)
    self.program(filename)

    reply, _ = self.katcprequest(
    name='alveo-program', request_timeout=timeout, require_ok=True)
    #delete regardless of returned status, then check status...
    self._delete_bof(filename)

    ret = reply.arguments[0]

    #ensure reply is a string literal
    try:
        ret = ret.decode()
    except (UnicodeDecodeError, AttributeError):
        pass

    if ret != 'ok':
      raise RuntimeError('%s: could not program alveo' % self.host)

    if self.reset() != True:
      raise RuntimeError('%s: could not reset alveo' % self.host)

    return True


  def _upload_to_ram_and_program_bitfile(self, filename, timeout=120):  #TODO this timeout may be too short for large images
    """
    Upload an FPG file to the Alveo.

    :param filename: the file to upload
    :param timeout: how long to wait, seconds
    :return: True upon success
    """
    self.upload_to_flash(filename)

    reply, _ = self.katcprequest(
    name='alveo-program', request_timeout=timeout, require_ok=True, request_args=(filename,))
    #delete regardless of returned status, then check status...
    self._delete_bof(filename)
    if reply.arguments[0] != 'ok':
      raise RuntimeError('%s: could not program alveo' % self.host)

    if self.reset() != True:
      raise RuntimeError('%s: could not reset alveo' % self.host)

    return True

  def check_phy_counter(self):
    raise AlveoFunctionError("Not an Alveo function")


  def ip_dest_address(self):
    ip = self.memread(self.hgbe_base_addr + self.hgbe_reg_map['dst_ip_addr'])
    ip_address = IpAddress(ip)
    return ip_address


  def ip_src_address(self):
    ip = self.memread(self.hgbe_base_addr + self.hgbe_reg_map['src_ip_addr'])
    ip_address = IpAddress(ip)
    return ip_address


  def get_hgbe_dest_ip(self):
    """
    Retrieve core's IP address from HW.

    :return: IpAddress object
    """
    IP_address = self.ip_dest_address()
    return IP_address


  def get_hgbe_src_ip(self):
    """
    Retrieve core's IP address from HW.

    :return: IpAddress object
    """
    IP_address = self.ip_src_address()
    return IP_address


  def get_hgbe_dest_mac(self):
    gbedata = []
    for ctr in range(0xC, 0x14, 4):
        gbedata.append(int(self.memread(self.hgbe_base_addr + ctr), 16))
    gbedata.reverse()
    gbebytes = []
    for d in gbedata:
        gbebytes.append(hex((d >> 24) & 0xff))
        gbebytes.append(hex((d >> 16) & 0xff))
        gbebytes.append(hex((d >> 8) & 0xff))
        gbebytes.append(hex((d >> 0) & 0xff))
    pd = gbebytes
    return Mac('{}:{}:{}:{}:{}:{}'.format(*pd[2:]))


  def get_hgbe_src_mac(self):
    gbedata = []
    for ctr in range(0x0, 0x8, 4):
        gbedata.append(int(self.memread(self.hgbe_base_addr + ctr), 16))
    gbedata.reverse()
    gbebytes = []
    for d in gbedata:
        gbebytes.append(hex((d >> 24) & 0xff))
        gbebytes.append(hex((d >> 16) & 0xff))
        gbebytes.append(hex((d >> 8) & 0xff))
        gbebytes.append(hex((d >> 0) & 0xff))
    pd = gbebytes
    return Mac('{}:{}:{}:{}:{}:{}'.format(*pd[2:]))


  def get_hgbe_port(self, station=''):
    """
    Retrieve core's port from HW.

    :return:  int
    """
    en_port = int(self.memread(self.hgbe_base_addr + self.hgbe_reg_map['fabric_port']), 16)
    if station == 'source':
        port = en_port & (2 ** 16 - 1)
    elif station == 'destination':
        port = en_port >> 16 & (2 ** 16 - 1)
    else:
        errmsg = 'Error specifying port station'
        self.logger.error(errmsg)
        raise ValueError(errmsg)
    return port


  def set_hgbe_port(self, port, station=''):
    """
    set the source or destination port of the 100GbE

    :param port: port number
    :param station: specify 'source' or 'destination'
    :return: string of the read value in hexadecimal
    """
    if station == 'source':
        en_port = int(self.memread(self.hgbe_base_addr + self.hgbe_reg_map['fabric_port']), 16)
        if en_port & (2 ** 16 - 1) == port:
            print('%s port already set to %s'%(station, port))
            return True
        else:
            en_port_new = (en_port & 0xFFFF0000) + port
            self.memwrite(self.hgbe_base_addr + self.hgbe_reg_map['fabric_port'], en_port_new)
            port_readback = self.get_hgbe_port('source')
            if port_readback == port:
               print('%s port set to %s'%(station, port_readback))
               return True
            else:
               return False
    elif station == 'destination':
        en_port = int(self.memread(self.hgbe_base_addr + self.hgbe_reg_map['fabric_port']), 16)
       # if (en_port >> 16) & (2 ** 16 - 1) == port:
        if (en_port >> 16) & (2 ** 16 - 1) == port:
            print('%s port already set to %s'%(station, port))
            return True
        else:
            en_port_new = (en_port & 0x0000FFFF) + (port << 16)
            self.memwrite(self.hgbe_base_addr + self.hgbe_reg_map['fabric_port'], en_port_new)
            port_readback = self.get_hgbe_port('destination')
            if port_readback == port:
               print('%s port set to %s'%(station, port_readback))
               return True
            else:
               return False

    else:
        errmsg = 'Error specifying port station'
        self.logger.error(errmsg)
        raise ValueError(errmsg)


  def get_hgbe_udp_count(self):
    udp_count = int(self.memread(self.hgbe_base_addr + self.hgbe_reg_map['udp_count']), 16)
    return udp_count


  def get_hgbe_ping_count(self):
    ping_count = int(self.memread(self.hgbe_base_addr + self.hgbe_reg_map['ping_count']), 16)
    return ping_count


  def get_hgbe_arp_count(self):
    arp_count = int(self.memread(self.hgbe_base_addr + self.hgbe_reg_map['arp_count']), 16)
    return arp_count


  def get_hgbe_dropped_mac_count(self):
    dropped_mac_count = int(self.memread(self.hgbe_base_addr + self.hgbe_reg_map['dropped_mac_count']), 16)
    return dropped_mac_count


  def get_hgbe_dropped_ip_count(self):
    dropped_ip_count = int(self.memread(self.hgbe_base_addr + self.hgbe_reg_map['dropped_ip_count']), 16)
    return dropped_ip_count


  def get_hgbe_dropped_port_count(self):
    dropped_port_count = int(self.memread(self.hgbe_base_addr + self.hgbe_reg_map['dropped_port_count']), 16)
    return dropped_port_count


  def get_hgbe_counters(self):
    udp_count = self.get_hgbe_udp_count()
    ping_count = self.get_hgbe_ping_count()
    arp_count = self.get_hgbe_arp_count()
    dropped_mac_count = self.get_hgbe_dropped_mac_count()
    dropped_ip_count = self.get_hgbe_dropped_ip_count()
    dropped_port_count = self.get_hgbe_dropped_port_count()
    self.counters = {
            "udpcnt"            : udp_count,
            "pingcnt"           : ping_count,
            "arp_count"         : arp_count,
            "dropped_mac_cnt"   : dropped_mac_count,
            "dropped_ip_cnt"    : dropped_ip_count,
            "dropped_port_cnt"  : dropped_port_count}
    return self.counters

  def set_hgbe_mac(self, mac, station=''):
    """
    set the source or destination mac address of the 100GbE

    :param port: mac address e.g. '82:00:00:af:25:10'. Please only specify the mac address in this format.
    :param station: specify 'source' or 'destination'
    :return: True of False
    """
    if station == 'source':
        en_mac = self.get_hgbe_src_mac()
        if en_mac == mac:
            print('%s mac already set to %s'%(station, mac))
            return True
        else:
            mac_list = mac.split(":")
            mac_upper = int(mac_list[0] + mac_list[1], 16)
            mac_lower = int(mac_list[2] + mac_list[3] + mac_list[4] + mac_list[5], 16)
            self.memwrite(self.hgbe_base_addr + self.hgbe_reg_map['src_mac_addr_lower'], mac_lower)
            self.memwrite(self.hgbe_base_addr + self.hgbe_reg_map['src_mac_addr_upper'], mac_upper)
            mac_readback = self.get_hgbe_src_mac()
            if mac_readback == mac:
               print('%s mac set to %s'%(station, mac_readback))
               return True
            else:
               return False
    elif station == 'destination':
        en_mac = self.get_hgbe_dest_mac()
        if en_mac == mac:
            print('%s mac already set to %s'%(station, mac))
            return True
        else:
            mac_list = mac.split(":")
            mac_upper = int(mac_list[0] + mac_list[1], 16)
            mac_lower = int(mac_list[2] + mac_list[3] + mac_list[4] + mac_list[5], 16)
            self.memwrite(self.hgbe_base_addr + self.hgbe_reg_map['dst_mac_addr_lower'], mac_lower)
            self.memwrite(self.hgbe_base_addr + self.hgbe_reg_map['dst_mac_addr_upper'], mac_upper)
            mac_readback = self.get_hgbe_dest_mac()
            if mac_readback == mac:
               print('%s mac set to %s'%(station, mac_readback))
               return True
            else:
               return False
    else:
        errmsg = 'Error specifying mac station'
        self.logger.error(errmsg)
        raise ValueError(errmsg)
  
  def set_hgbe_ip(self, ip, station=''):
      '''
      set the source or destination ip address of the 100GbE

      :param ip: ip address e.g. '192.168.2.11'. Please only specify the ip address in this format.
      :param station: specify 'source' or 'destination'
      :return: True or False

      '''
      if station == 'source':
        en_ip = self.get_hgbe_src_ip()
        if en_ip == ip:
            print('%s ip already set to %s'%(station, ip))
            return True
        else:
             ip_list = ip.split(".")
             ipbytes = []
             ipbytes.append((int(ip_list[0], 10) << 24) & 0xff000000)
             ipbytes.append((int(ip_list[1], 10) << 16) & 0x00ff0000)
             ipbytes.append((int(ip_list[2], 10) << 8) & 0x0000ff00)
             ipbytes.append((int(ip_list[3], 10) << 0) & 0x000000ff)
             ip_value = sum(ipbytes)
             self.memwrite(self.hgbe_base_addr + self.hgbe_reg_map['src_ip_addr'], ip_value)
             ip_readback = self.get_hgbe_src_ip()
             if ip_readback == ip:
                print('%s ip set to %s'%(station, ip_readback))
                return True
             else:
                return False
      elif station == 'destination':
        en_ip = self.get_hgbe_dest_ip()
        if en_ip == ip:
            print('%s ip already set to %s'%(station, ip))
            return True
        else:
             ip_list = ip.split(".")
             ipbytes = []
             ipbytes.append((int(ip_list[0], 10) << 24) & 0xff000000)
             ipbytes.append((int(ip_list[1], 10) << 16) & 0x00ff0000)
             ipbytes.append((int(ip_list[2], 10) << 8) & 0x0000ff00)
             ipbytes.append((int(ip_list[3], 10) << 0) & 0x000000ff)
             ip_value = sum(ipbytes)
             self.memwrite(self.hgbe_base_addr + self.hgbe_reg_map['dst_ip_addr'], ip_value)
             ip_readback = self.get_hgbe_dest_ip()
             if ip_readback == ip:
                print('%s ip set to %s'%(station, ip_readback))
                return True
             else:
                return False


  def hbm_rd(self, stack=None, controller=None, reg_name=''):
    '''
    readback the hbm memory at a specified stack, memeory controller region and address offset.

    :param stack: which hbm stack/bank to read from? Must be 0 or 1.
    :param controller: the memory controller region to read from. Values must be 0 to 7.
    :reg_name: the register name string as specified in the register map, eg CFG_ECC_CORRECTION_EN etc. See register names and descriptions below.
    :return: dictionary or list of dictionaries with register name(s) and value(s).

    The descriptions for the HBM ECC and Status registers, Activity Monitor/Status registers and Activity Monitor Tracking registers are provided below as an extract from:

    AXI High Bandwidth Memory Controller v1.0 LogiCORE IP Product Guide (PG276) found at https://docs.xilinx.com/r/en-US/pg276-axi-hbm/Memory-Controller-Register-Map

    # HBM ECC and Status registers

    CFG_ECC_CORRECTION_EN -  D0 : Set this bit to correct 1-bit errors and detect 2-bit errors. Reset value is 1'b1.
    INIT_ECC_SCRUB_EN - D0 : If this bit is set, and if CFG_ECC_CORRECTION_EN is also set, then ECC scrubbing is enabled for all addresses in this memory controller. Single bit errors will be detected and corrected. Double bit errors will be detected.
    CFG_ECC_SCRUB_PERIOD - D[12..0] : Period between read operations for ECC scrubbing. This value is in units of 256 memory clock periods. A value of 0x02 means 512 memory clock periods between each read. Reset value is 13'h02.
    INIT_WRITE_DATA_1B_ECC_ERROR_GEN_PS0 - D[3..0] : Setting one of these bits will instruct the Memory Controller to insert a single 1-bit ECC error on the next cycle of write data. The enabled bit selects which write of the BL4 has the error. For additional errorgeneration, the bit must be reset then set again. Reset value is 4'h0.
    INIT_WRITE_DATA_2B_ECC_ERROR_GEN_PS0 - D[3..0] : Setting one of these bits will instruct the Memory Controller to insert a single 2-bit ECC error on the next cycle of write data. The enabled bit selects which write of the BL4 has the error. For additional error generation, the bit must be reset then set again. Reset value is 4'h0.
    INIT_WRITE_DATA_1B_ECC_ERROR_GEN_PS1 - D[3..0] : Setting one of these bits will instruct the Memory Controller to insert a single 1-bit ECC error on the next cycle of write data. The enabled bit selects which write of the BL4 has the error. For additional error generation, the bit must be reset then set again. Reset value is 4'h0.
    INIT_WRITE_DATA_2B_ECC_ERROR_GEN_PS1 - D[3..0] : Setting one of these bits will instruct the Memory Controller to insert a single 2-bit ECC error on the next cycle of write data. The enabled bit selects which write of the BL4 has the error. For additional error generation, the bit must be reset then set again. Reset value is 4'h0.
    STAT_ECC_ERROR_1BIT_CNT_PS0 - D[7..0] : A counter that increments whenever 1-bit ECC errors have been detected. Holds the value when maximum count has been reached (255) or until reset by INIT_ECC_ERROR_CLR. Reset value 8’b0.
    STAT_ECC_ERROR_1BIT_CNT_PS1 - D[7..0] : A counter that increments whenever 1-bit ECC errors have been detected. Holds the value when maximum count has been reached (255) or until reset by INIT_ECC_ERROR_CLR. Reset value 8’b0.
    STAT_ECC_ERROR_2BIT_CNT_PS0 - D[7..0] : A counter that increments whenever 2-bit ECC errors have been detected. Holds the value when maximum count has been reached (255) or until reset by INIT_ECC_ERROR_CLR. Reset value 8’b0.
    STAT_ECC_ERROR_2BIT_CNT_PS1 - D[7..0] : A counter that increments whenever 2-bit ECC errors have been detected. Holds the value when maximum count has been reached (255) or until reset by INIT_ECC_ERROR_CLR. Reset value 8’b0.
    INIT_ECC_ERROR_CLR - D[0] : When set to 1 this will reset the STAT_ECC_ERR_1BIT_CNT_PSx registers. When set to 0 the counters will resume. Reset value 1’b0.
    CFG_ECC_1BIT_INT_THRESH - D[7..0] : This register configures a count threshold that must be exceeded before STAT_INT_ECC_1BIT_THRESH is asserted andSTAT_ECC_ERROR_1BIT_CNT_PSx begin to count. Reset value 8'b0.
    STAT_INT_ECC_1BIT_THRESH_PS0 - D[0] : This bit is set when the number of 1-bit ECC errors exceeds the threshold defined in CFG_ECC_1BIT_INT_THRESH. Reading this register automatically clears it. Reset value 1’b0.
    STAT_INT_ECC_1BIT_THRESH_PS1 - D[0] : This bit is set when the number of 1-bit ECC errors exceeds the threshold defined in CFG_ECC_1BIT_INT_THRESH. Reading this register automatically clears it. Reset value 1’b0.
    STAT_DFI_INIT_COMPLETE - D[0] : This value is set to ‘1’ when PHY initialization has completed. Reset value 1’b0.
    STAT_DFI_CATTRIP - D[0] : This register will be set if the temperature ever exceeds the catastrophic value per HBM2 Jedec specification. Reset value 1’b0.
STAT_DFI_INIT_COMPLETE

    # Activity Monitor/Status registers

    INIT_AM_REPEAT - D[0] Set to 1 to initiate the repeating interval data collection.
    INIT_AM_SINGLE_EN - D[0] Set to 1 to initiate a single interval data collection.
    CFG_AM_INTERVAL - D[31..0] Set the activity monitor interval, in memory clocks.
    STAT_AM_COMPLETE - D[0] This is set to 1 when the interval has completed. This register is cleared on Auto-Precharge.

    # Activity Monitor Tracking registers

    AM_WR_CMD_PS0 - Number of cmd=Write commands captured in the last monitoring interval. Note that this counts writes without Auto-Precharge, since writes with Auto-Precharge are a different command. For total Write commands, sum the two counts.
    AM_WR_CMD_PS1 - Number of cmd=Write commands captured in the last monitoring interval. Note that this counts writes without Auto-Precharge, since writes with Auto-Precharge are a different command. For total Write commands, sum the two counts.
    AM_WR_AP_CMD_PS0 - Number of cmd=Write-with-Auto-Precharge commands captured in the last monitoring interval.
    AM_WR_AP_CMD_PS1 - Number of cmd=Write-with-Auto-Precharge commands captured in the last monitoring interval.
    AM_RD_CMD_PS0 - Number of cmd=Read commands captured in the last monitoring interval. Note that this counts reads without Auto-Precharge, since reads with Auto-Precharge are a different command. For total Read commands, sum the two counts.
    AM_RD_CMD_PS1 - Number of cmd=Read commands captured in the last monitoring interval. Note that this counts reads without Auto-Precharge, since reads with Auto-Precharge are a different command. For total Read commands, sum the two counts.
    AM_RD_AP_CMD_PS0 - Number of Read with Auto-Precharge commands captured in the last monitoring interval.
    AM_RD_AP_CMD_PS1 - Number of Read with Auto-Precharge commands captured in the last monitoring interval.
    AM_REFRESH_CMD_PS0 - Number of Refresh commands captured in the last monitoring interval.
    AM_REFRESH_CMD_PS1 - Number of Refresh commands captured in the last monitoring interval.
    AM_ACT_CMD_PS0 - Number of Activate commands captured in the last monitoring interval.
    AM_ACT_CMD_PS1 - Number of Activate commands captured in the last monitoring interval.
    AM_PRECHARGE_CMD_PS0 - Number of Precharge (single-bank) commands captured in the last monitoring interval.
    AM_PRECHARGE_CMD_PS1 - Number of Precharge (single-bank) commands captured in the last monitoring interval.
    AM_PRECHARGE_ALL_CMD_PS0 - Number of times any Read command (Read or Read with Auto-Precharge) is followed by any Write command (Write or Write with Auto-Precharge) in the last monitoring interval.
    AM_PRECHARGE_ALL_CMD_PS1 - Number of times any Read command (Read or Read with Auto-Precharge) is followed by any Write command (Write or Write with Auto-Precharge) in the last monitoring interval.
    AM_POWER_DOWN - Number of clock cycles the memory is in power-down in the last monitoring interval.
    AM_SELF_REFRESH - Number of clock cycles the memory is in self-refresh in the last monitoring interval.
    AM_RD_TO_WR_SWITCH_PS0 - Number of times any Read command (Read or Read with Auto-Precharge) is followed by any Write command (Write or Write with Auto-Precharge) in the last monitoring interval.
    AM_RD_TO_WR_SWITCH_PS1 - Number of times any Read command (Read or Read with Auto-Precharge) is followed by any Write command (Write or Write with Auto-Precharge) in the last monitoring interval.
    AM_RO_AGE_LIMIT_PS0 - Number of times the reorder queue entry reaches its age limit in the last monitoring interval.
    AM_RO_AGE_LIMIT_PS1 - Number of times the reorder queue entry reaches its age limit in the last monitoring interval.
    AM_RMW_CYCLE_PS0 - Number of Read Modify Write cycles in the last monitoring interval.
    AM_RMW_CYCLE_PS1 - Number of Read Modify Write cycles in the last monitoring interval.
    
    '''
    
    hbm_stacks = []        

    if stack == None and controller == None and reg_name == '':
        for hbm_stack_idx in self.hbm_stack_base_addr.keys():
            hbm_base_addr = self.get_hbm_base_addr(hbm_stack_idx)
            controllers = []
            self.logger.info("stack " + str(hbm_stack_idx))
            for region_idx, upper_5_bit_addr in self.hbm_controller_regions.items():
                registers = dict()
                self.logger.info(" reading memory controller " + str(region_idx))
                for reg, reg_offset in self.hbm_reg_map.items():
                    registers[reg] = self.memread(hbm_base_addr + (reg_offset + (upper_5_bit_addr << 17)))
                controllers.insert(region_idx, registers)
                hbm_stacks.insert(hbm_stack_idx, controllers)
        return hbm_stacks

    elif stack != None and controller == None and reg_name == '':
        if stack in self.hbm_stack_base_addr.keys():
            hbm_base_addr = self.get_hbm_base_addr(stack)
            controllers = []
            self.logger.info("stack " + str(stack))
            for region_idx, upper_5_bit_addr in self.hbm_controller_regions.items():
                registers = dict()
                self.logger.info(" reading memory controller " + str(region_idx))
                for reg, reg_offset in self.hbm_reg_map.items():
                    registers[reg] = self.memread(hbm_base_addr + (reg_offset + (upper_5_bit_addr << 17)))
                controllers.insert(region_idx, registers)
            return controllers
        else:
            self.logger.error(str(stack) + " does not exist in hbm stack dictionary.")
            sys.exit()

    elif stack != None and controller != None and reg_name == '':
        if (stack in self.hbm_stack_base_addr.keys()) and (controller in self.hbm_controller_regions.keys()):
            hbm_base_addr = self.get_hbm_base_addr(stack)
            registers = dict()
            for reg, reg_offset in self.hbm_reg_map.items():
                    registers[reg] = self.memread(hbm_base_addr + (reg_offset + (self.hbm_controller_regions.get(controller) << 17)))
            return registers
        else:
            self.logger.error("stack " + str(stack) + " and/or " + "controller " + str(controller) + " does not exist in hbm dictionaries.")
            sys.exit()

    elif stack != None and controller != None and reg_name != '':
        if (stack in self.hbm_stack_base_addr.keys()) and (controller in self.hbm_controller_regions.keys()) and (reg_name in self.hbm_reg_map.keys()):
            hbm_base_addr = self.get_hbm_base_addr(stack)
            registers = dict()
            registers[reg_name] = self.memread(hbm_base_addr + (self.hbm_reg_map.get(reg_name) + (self.hbm_controller_regions.get(controller) << 17)))
            return registers
        else:
            self.logger.error("stack " + str(stack) + " and/or " + "controller " + str(controller) + " reg_name  " + str(reg_name) + " does not exist in hbm dictionaries.")
            sys.exit()

    else:
        self.logger.error("Arguments error: If you specify a particular register name then you must also specify from which stack and from which memory controller. If you specify a memory controller then you must also specify from which stack.")
        return None

  def get_hbm_base_addr(self, hbm_stack):
    '''
    Retrieves the hbm base address depending on the which stack to read from or write to.
    :param hbm_stack: which hbm stack/bank to read from or write to? Must be 0 or 1.

    '''

    hbm_base_address = self.hbm_stack_base_addr.get(hbm_stack)
    return hbm_base_address


  def hbm_wr(self, stack, controller, val, reg_name=''):
    '''
    writes a value to the hbm memory at a specified stack, memory controller region and address offset.

    :param stack: which hbm stack/bank to write to? Must be 0 or 1.
    :param controller: the memory controller region to write to. Values must be 0 to 7.
    :val: the value to write  to hbm memory.
    :reg_name: the register name string as specified in the register map, eg CFG_ECC_CORRECTION_EN etc. See register names and descriptions below.
    :return: returns True or False when the write is successful or unsuccessful respectively. #TODO Currently there is a bug that makes the retuen value opposite

    The descriptions for the HBM ECC and Status registers, Activity Monitor/Status registers and Activity Monitor Tracking registers are provided below as an extract from:

    AXI High Bandwidth Memory Controller v1.0 LogiCORE IP Product Guide (PG276) found at https://docs.xilinx.com/r/en-US/pg276-axi-hbm/Memory-Controller-Register-Map

    # HBM ECC and Status registers

    CFG_ECC_CORRECTION_EN -  D0 : Set this bit to correct 1-bit errors and detect 2-bit errors. Reset value is 1'b1.
    INIT_ECC_SCRUB_EN - D0 : If this bit is set, and if CFG_ECC_CORRECTION_EN is also set, then ECC scrubbing is enabled for all addresses in this memory controller. Single bit errors will be detected and corrected. Double bit errors will be detected.
    CFG_ECC_SCRUB_PERIOD - D[12..0] : Period between read operations for ECC scrubbing. This value is in units of 256 memory clock periods. A value of 0x02 means 512 memory clock periods between each read. Reset value is 13'h02.
    INIT_WRITE_DATA_1B_ECC_ERROR_GEN_PS0 - D[3..0] : Setting one of these bits will instruct the Memory Controller to insert a single 1-bit ECC error on the next cycle of write data. The enabled bit selects which write of the BL4 has the error. For additional errorgeneration, the bit must be reset then set again. Reset value is 4'h0.
    INIT_WRITE_DATA_2B_ECC_ERROR_GEN_PS0 - D[3..0] : Setting one of these bits will instruct the Memory Controller to insert a single 2-bit ECC error on the next cycle of write data. The enabled bit selects which write of the BL4 has the error. For additional error generation, the bit must be reset then set again. Reset value is 4'h0.
    INIT_WRITE_DATA_1B_ECC_ERROR_GEN_PS1 - D[3..0] : Setting one of these bits will instruct the Memory Controller to insert a single 1-bit ECC error on the next cycle of write data. The enabled bit selects which write of the BL4 has the error. For additional error generation, the bit must be reset then set again. Reset value is 4'h0.
    INIT_WRITE_DATA_2B_ECC_ERROR_GEN_PS1 - D[3..0] : Setting one of these bits will instruct the Memory Controller to insert a single 2-bit ECC error on the next cycle of write data. The enabled bit selects which write of the BL4 has the error. For additional error generation, the bit must be reset then set again. Reset value is 4'h0.
    STAT_ECC_ERROR_1BIT_CNT_PS0 - D[7..0] : A counter that increments whenever 1-bit ECC errors have been detected. Holds the value when maximum count has been reached (255) or until reset by INIT_ECC_ERROR_CLR. Reset value 8’b0.
    STAT_ECC_ERROR_1BIT_CNT_PS1 - D[7..0] : A counter that increments whenever 1-bit ECC errors have been detected. Holds the value when maximum count has been reached (255) or until reset by INIT_ECC_ERROR_CLR. Reset value 8’b0.
    STAT_ECC_ERROR_2BIT_CNT_PS0 - D[7..0] : A counter that increments whenever 2-bit ECC errors have been detected. Holds the value when maximum count has been reached (255) or until reset by INIT_ECC_ERROR_CLR. Reset value 8’b0.
    STAT_ECC_ERROR_2BIT_CNT_PS1 - D[7..0] : A counter that increments whenever 2-bit ECC errors have been detected. Holds the value when maximum count has been reached (255) or until reset by INIT_ECC_ERROR_CLR. Reset value 8’b0.
    INIT_ECC_ERROR_CLR - D[0] : When set to 1 this will reset the STAT_ECC_ERR_1BIT_CNT_PSx registers. When set to 0 the counters will resume. Reset value 1’b0.
    CFG_ECC_1BIT_INT_THRESH - D[7..0] : This register configures a count threshold that must be exceeded before STAT_INT_ECC_1BIT_THRESH is asserted andSTAT_ECC_ERROR_1BIT_CNT_PSx begin to count. Reset value 8'b0.
    STAT_INT_ECC_1BIT_THRESH_PS0 - D[0] : This bit is set when the number of 1-bit ECC errors exceeds the threshold defined in CFG_ECC_1BIT_INT_THRESH. Reading this register automatically clears it. Reset value 1’b0.
    STAT_INT_ECC_1BIT_THRESH_PS1 - D[0] : This bit is set when the number of 1-bit ECC errors exceeds the threshold defined in CFG_ECC_1BIT_INT_THRESH. Reading this register automatically clears it. Reset value 1’b0.
    STAT_DFI_INIT_COMPLETE - D[0] : This value is set to ‘1’ when PHY initialization has completed. Reset value 1’b0.
    STAT_DFI_CATTRIP - D[0] : This register will be set if the temperature ever exceeds the catastrophic value per HBM2 Jedec specification. Reset value 1’b0.

    # Activity Monitor/Status registers

    INIT_AM_REPEAT - D[0] Set to 1 to initiate the repeating interval data collection.
    INIT_AM_SINGLE_EN - D[0] Set to 1 to initiate a single interval data collection.
    CFG_AM_INTERVAL - D[31..0] Set the activity monitor interval, in memory clocks.
    STAT_AM_COMPLETE - D[0] This is set to 1 when the interval has completed. This register is cleared on Auto-Precharge.

    # Activity Monitor Tracking registers

    AM_WR_CMD_PS0 - Number of cmd=Write commands captured in the last monitoring interval. Note that this counts writes without Auto-Precharge, since writes with Auto-Precharge are a different command. For total Write commands, sum the two counts.
    AM_WR_CMD_PS1 - Number of cmd=Write commands captured in the last monitoring interval. Note that this counts writes without Auto-Precharge, since writes with Auto-Precharge are a different command. For total Write commands, sum the two counts.
    AM_WR_AP_CMD_PS0 - Number of cmd=Write-with-Auto-Precharge commands captured in the last monitoring interval.
    AM_WR_AP_CMD_PS1 - Number of cmd=Write-with-Auto-Precharge commands captured in the last monitoring interval.
    AM_RD_CMD_PS0 - Number of cmd=Read commands captured in the last monitoring interval. Note that this counts reads without Auto-Precharge, since reads with Auto-Precharge are a different command. For total Read commands, sum the two counts.
    AM_RD_CMD_PS1 - Number of cmd=Read commands captured in the last monitoring interval. Note that this counts reads without Auto-Precharge, since reads with Auto-Precharge are a different command. For total Read commands, sum the two counts.
    AM_RD_AP_CMD_PS0 - Number of Read with Auto-Precharge commands captured in the last monitoring interval.
    AM_RD_AP_CMD_PS1 - Number of Read with Auto-Precharge commands captured in the last monitoring interval.
    AM_REFRESH_CMD_PS0 - Number of Refresh commands captured in the last monitoring interval.
    AM_REFRESH_CMD_PS1 - Number of Refresh commands captured in the last monitoring interval.
    AM_ACT_CMD_PS0 - Number of Activate commands captured in the last monitoring interval.
    AM_ACT_CMD_PS1 - Number of Activate commands captured in the last monitoring interval.
    AM_PRECHARGE_CMD_PS0 - Number of Precharge (single-bank) commands captured in the last monitoring interval.
    AM_PRECHARGE_CMD_PS1 - Number of Precharge (single-bank) commands captured in the last monitoring interval.
    AM_PRECHARGE_ALL_CMD_PS0 - Number of times any Read command (Read or Read with Auto-Precharge) is followed by any Write command (Write or Write with Auto-Precharge) in the last monitoring interval.
    AM_PRECHARGE_ALL_CMD_PS1 - Number of times any Read command (Read or Read with Auto-Precharge) is followed by any Write command (Write or Write with Auto-Precharge) in the last monitoring interval.
    AM_POWER_DOWN - Number of clock cycles the memory is in power-down in the last monitoring interval.
    AM_SELF_REFRESH - Number of clock cycles the memory is in self-refresh in the last monitoring interval.
    AM_RD_TO_WR_SWITCH_PS0 - Number of times any Read command (Read or Read with Auto-Precharge) is followed by any Write command (Write or Write with Auto-Precharge) in the last monitoring interval.
    AM_RD_TO_WR_SWITCH_PS1 - Number of times any Read command (Read or Read with Auto-Precharge) is followed by any Write command (Write or Write with Auto-Precharge) in the last monitoring interval.
    AM_RO_AGE_LIMIT_PS0 - Number of times the reorder queue entry reaches its age limit in the last monitoring interval.
    AM_RO_AGE_LIMIT_PS1 - Number of times the reorder queue entry reaches its age limit in the last monitoring interval.
    AM_RMW_CYCLE_PS0 - Number of Read Modify Write cycles in the last monitoring interval.
    AM_RMW_CYCLE_PS1 - Number of Read Modify Write cycles in the last monitoring interval.
  

    '''
    hbm_base_addr = self.get_hbm_base_addr(stack)
    return self.memwrite(hbm_base_addr + (self.hbm_reg_map.get(reg_name) + (self.hbm_controller_regions.get(controller) << 17)), val)
