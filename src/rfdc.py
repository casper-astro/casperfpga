import logging
import katcp
import os
import random
from enum import Enum, auto

LOGGER = logging.getLogger(__name__)

class RFDC(object):
  """
  Casperfpga class encapsulating the rfdc Yellow Block
  """

  LMK = 'lmk'
  LMX = 'lmx'

  ADC0_OFFSET = 0x14000
  ADC1_OFFSET = 0x18000
  ADC2_OFFSET = 0x1c000
  ADC3_OFFSET = 0x20000

  # Common control and status registers
  VER_OFFSET = 0x0
  COMMON_MASTER_RST = 0x4
  COMMON_IRQ_STATUS = 0x100

  # Tile control and status registers
  RST_PO_STATE_MACHINE = 0x4
  RST_STATE_REG = 0x8
  CUR_STATE_REG = 0xc
  CLK_DETECT_REG = 0x84 #gen3 parts
  RST_COUNT_REG = 0x38
  IRQ_STAT_REG = 0x200
  IRQ_EN_REG = 0x204
  SLICE0_IRQ_REG = 0x208
  SLICE0_IRQ_EN = 0x20c
  SLICE1_IRQ_REG = 0x210
  SLICE1_IRQ_EN = 0x214
  #slice 2/3 registers for quad tile ADC tiles only
  SLICE2_IRQ_REG = 0x218
  SLICE2_IRQ_EN  = 0x21c
  SLICE3_IRQ_REG = 0x220
  SLICE3_IRQ_EN  = 0x224
  COMMON_STATUS_REG = 0x228
  TILE_DISABLE_REG = 0x230

  # converter types
  ADC_TILE = 0
  DAC_TILE = 1

  # mixer
  MIX_TYPE_COARSE = 1
  MIX_TYPE_FINE = 2
  MIX_TYPE_OFF = 0
  MIX_TYPE_DISABLED = 3

  MIX_MODE_OFF = 0
  MIX_MODE_C2C = 1
  MIX_MODE_C2R = 2
  MIX_MODE_R2C = 3
  MIX_MODE_R2R = 4

  MIX_COARSE_OFF = 0
  MIX_COARSE_FS2 = 2
  MIX_COARSE_FS4 = 4
  MIX_COARSE_NFS4 = 8
  MIX_COARSE_BYPASS = 16

  MIX_SCALE_AUTO = 0
  MIX_SCALE_1P0 = 1
  MIX_SCALE_0P7 = 2

  # tile clk out divider
  FAB_CLK_DIV1 = 1
  FAB_CLK_DIV2 = 2
  FAB_CLK_DIV4 = 3
  FAB_CLK_DIV8 = 4
  FAB_CLK_DIV16 = 5

  # nyquist zones
  NYQUIST_ZONE1 = 1
  NYQUIST_ZONE2 = 2

  # converter data types
  DTYPE_REAL = 0
  DTYPE_CMPLX = 1

  # PLL clock source
  CLK_SRC_EXTERNAL = 0
  CLK_SRC_INTERNAL = 1

  PLL_UNLOCKED = 1
  PLL_LOCKED = 2

  # background calibration blocks
  CAL_MODE1 = 1
  CAL_MODE2 = 2

  CAL_BLOCK_OCB1 = 0
  CAL_BLOCK_OCB2 = 1
  CAL_BLOCK_GCB  = 2
  CAL_BLOCK_TSCB = 3

  CAL_UNFREEZE = 0
  CAL_FREEZE = 1

  # trigger event
  EVENT_MIXER =  1
  EVENT_COARSE_DLY = 2
  EVENT_QMC = 4

  # QMC and Coarse Delay event update source
  EVNT_SRC_IMMEDIAT = 0 # Update after register writeE
  EVNT_SRC_SLICE = 1    # Update using SLICE
  EVNT_SRC_TILE = 2     # Update using TILE
  EVNT_SRC_SYSREF = 3   # Update using SYSREF
  EVNT_SRC_MARKER = 4   # update using MARKER
  EVNT_SRC_PL = 5       # update using PL event

  # inverse sinc fir modes
  INVSINC_FIR_DISABLED = 0 # disabled
  INVSINC_FIR_NYQUIST1 = 1 # first nyquist
  INVSINC_FIR_NYQUIST2 = 2 # second nyquist

  # image rection filter
  IMR_LOWPASS = 0
  IMR_HIGHPASS = 1

  # adc threshold settings
  UPDATE_THRESHOLD0 = 1
  UPDATE_THRESHOLD1 = 2
  UPDATE_THRESHOLD_BOTH = 4

  THRESHOLD_OFF = 0
  THRESHOLD_STICKY_OVER = 1
  THRESHOLD_STICKY_UNDER = 2
  THRESHOLD_HYSTERISIS = 3

  THRESHOLD_CLR_MANUAL = 1
  THRESHOLD_CLR_AUTO = 2

  # interrupt masks
  class InterruptMasks(Enum):
    """
    adc and dac datapath interrupt indicating one of the datapath interrupts is active.
    To clear, all datapath sub-interrupts must be cleared
    """
    IXR_FIFOUSRDAT_MASK = 0x0000000F
    IXR_FIFOUSRDAT_OF_MASK = 0x00000001
    IXR_FIFOUSRDAT_UF_MASK = 0x00000002
    IXR_FIFOMRGNIND_OF_MASK = 0x00000004
    IXR_FIFOMRGNIND_UF_MASK = 0x00000008
    ADC_IXR_DATAPATH_MASK = 0x00000FF0
    ADC_IXR_DMON_STG_MASK = 0x000003F0
    DAC_IXR_DATAPATH_MASK = 0x00001FF0
    DAC_IXR_INTP_STG_MASK = 0x000003F0
    DAC_IXR_INTP_I_STG0_MASK = 0x00000010
    DAC_IXR_INTP_I_STG1_MASK = 0x00000020
    DAC_IXR_INTP_I_STG2_MASK = 0x00000040
    DAC_IXR_INTP_Q_STG0_MASK = 0x00000080
    DAC_IXR_INTP_Q_STG1_MASK = 0x00000100
    DAC_IXR_INTP_Q_STG2_MASK = 0x00000200
    ADC_IXR_DMON_I_STG0_MASK = 0x00000010
    ADC_IXR_DMON_I_STG1_MASK = 0x00000020
    ADC_IXR_DMON_I_STG2_MASK = 0x00000040
    ADC_IXR_DMON_Q_STG0_MASK = 0x00000080
    ADC_IXR_DMON_Q_STG1_MASK = 0x00000100
    ADC_IXR_DMON_Q_STG2_MASK = 0x00000200
    IXR_QMC_GAIN_PHASE_MASK = 0x00000400
    IXR_QMC_OFFST_MASK = 0x00000800
    DAC_IXR_INVSNC_OF_MASK = 0x00001000
    SUBADC_IXR_DCDR_MASK = 0x00FF0000
    SUBADC0_IXR_DCDR_OF_MASK = 0x00010000
    SUBADC0_IXR_DCDR_UF_MASK = 0x00020000
    SUBADC1_IXR_DCDR_OF_MASK = 0x00040000
    SUBADC1_IXR_DCDR_UF_MASK = 0x00080000
    SUBADC2_IXR_DCDR_OF_MASK = 0x00100000
    SUBADC2_IXR_DCDR_UF_MASK = 0x00200000
    SUBADC3_IXR_DCDR_OF_MASK = 0x00400000
    SUBADC3_IXR_DCDR_UF_MASK = 0x00800000
    ADC_OVR_VOLTAGE_MASK = 0x04000000
    ADC_OVR_RANGE_MASK = 0x08000000
    ADC_DAT_OVR_MASK = 0x40000000
    ADC_FIFO_OVR_MASK = 0x80000000
    ADC_CMODE_OVR_MASK = 0x10000000 # (Gen 3)
    ADC_CMODE_UNDR_MASK = 0x20000000 # (Gen 3)

  def parse_interrupt_mask(self, interrupt_mask):
    interrupt_status = {}
    for interrupt in self.InterruptMasks:
      interrupt_status[interrupt.name] = bool(interrupt_mask & interrupt.value)

    return interrupt_status

  class tile(object):
    pass

  class adc_slice(object):
    pass

  @classmethod
  def from_device_info(cls, parent, device_name, device_info, initialise=False, **kwargs):
    """
    Process device info and the memory map to populate necessary class info
    and return a RFDC instance.

    :param parent: The parent device, normally a casperfpga instance
    :param device_name:
    :param device_info:
    :param initialise:
    :param kwargs:
    :return:
    """
    return cls(parent, device_name, device_info, initialise, **kwargs)


  def __init__(self, parent, device_name, device_info, initialise=False):
    self.parent = parent
    self.logger = parent.logger
    self.name   = device_name
    self.device_info = device_info
    #self.clkfiles = []

    """
    apply the dtbo for the rfdc driver

    ideally, this would be incorporated as part of an extended `fpg` implementation that includes the device tree overlay by including the
    dtbo as part of the programming process. The rfdc is the only block that is using the dto at the moment, so instead of completely
    implement this extended fpg functionality the rfdc instead manages its own application of the dto.
    """

    """
    Run only when a new client connects and the fpga is already running a design and want to create `casperfpga` `rfdc` helper container
    object from `get_system_information()`

    The `initialise` parameter is passed in here coming from the top-level casperfpga function `upload_to_ram_and_program`. That seems
    like it was intended for something simliar on skarab. However, that defaults to False for some reason when it seems more intuitive
    that default behavior should be True at program. But, I suppose there are any number of reasons that could makes more sense to default
    `False` (e.g., initializations like onboard PLLs are only done on power up, and are not necessarily initialized each time the fpga is
    programmed). As we always want the rfdc initialized on programming and the goal here is to support rfdc initialization when a new
    client connects and the fpga is already programmed (and potentially applying the dto in the rfdc object only temporary until further
    support is considered wen programming the fpg) we instead know that `upload_to_ram_and_program()` sets `prog_info` just before exit we
    need this anyway to know what `.dtbo` to apply so we just check if we know of something that has been programmed and use that.

    using `initialise` could make more sense in the context of knowing that the rfpll's need to be programmed and want to start those up
    when initializing the `rfdc` `casperfpga` object. But in that case we would still want to not apply the dto every time and now would
    require initializing different components. Instead, it would make more sense for the user to implement in their script the logic
    required to either initialize supporting rfdc components or not.
    """
    fpgpath = parent.transport.prog_info['last_programmed']
    if fpgpath != '':
    #if initialise:
      #fpgpath = parent.transport.prog_info['last_programmed']
      fpgpath, fpg = os.path.split(fpgpath)
      dtbo = os.path.join(fpgpath, "{}.dtbo".format(fpg.split('.')[0]))

      os.path.getsize(dtbo) # check if exists
      self.apply_dto(dtbo)


  def init(self, lmk_file=None, lmx_file=None, upload=False):
    """
    Initialize the rfdc driver, optionally program rfplls if file parameters are present.

    :param lmk_file: lmk tics hexdump (.txt) register file name
    :type lmk_file: str, optional

    :param lmx_file: lmx tics hexdump (.txt) register file name
    :type lmx_file: str, optional

    :param upload: Inidicate that the configuration files are local to the client and
        should be uploaded to the remote, will overwrite if exists on remote filesystem
    :type upload: bool, optional

    :return: `True` if completed successfully, `False` otherwise
    :rtype: bool

    :raises KatcpRequestFail: If KatcpTransport encounters an error
    """

    if lmk_file:
      self.progpll('lmk', lmk_file, upload=upload)

    if lmx_file:
      self.progpll('lmx', lmx_file, upload=upload)

    t = self.parent.transport
    reply, informs = t.katcprequest(name='rfdc-init', request_timeout=t._timeout)

    return True


  def apply_dto(self, dtbofile):
    """

    """
    t = self.parent.transport

    os.path.getsize(dtbofile)
    port = random.randint(2000, 2500)

    # hacky tmp file to match tbs expected file format
    tbs_dtbo_name = 'tcpborphserver.dtbo'
    fd = open(dtbofile, 'rb')
    fdtbs_dtbo = open(tbs_dtbo_name, 'wb')
    for b in fd:
      fdtbs_dtbo.write(b)
    fdtbs_dtbo.close()
    fd.close()

    t.upload_to_flash(tbs_dtbo_name, force_upload=True)
    os.remove(tbs_dtbo_name)

    args = ("apply",)
    reply, informs = t.katcprequest(name='dto', request_timeout=t._timeout, request_args=args)

    if informs[0].arguments[0].decode() == 'applied\n':
      return True
    else:
      return False


  def show_clk_files(self):
    """
    Show a list of available remote clock register files to use for rfpll clock programming.

    :return: A list of available clock register files.
    :rtype: list

    :raises KatcpRequestFail: If KatcpTransport encounters an error.
    """
    t = self.parent.transport
    files = t.listbof()

    clkfiles = []
    for f in files:
      s = f.split('.')
      if len(s) > 1:
        if s[-1] == 'txt':
          clkfiles.append(f)
          #self.clkfiles.append(f)
    return clkfiles


  def del_clk_file(self, clkfname):
    """
    Remove an rfpll configuration clock file from the remote filesystem.

    :param clkfname: Name of clock configuration on remote filesystem.
    :type clkfname: str

    :return: `True` if file removed successfully, `False` otherwise.
    :rtype: bool

    :raises KatcpRequestFail: If KatcpTransport encounters an error.
    """
    t = self.parent.transport
    args = (clkfname, )
    reply, informs = t.katcprequest(name='delbof', request_timeout=t._timeout, request_args=args)
    return True


  def upload_clk_file(self, fpath, port=None, force_upload=False):
    """
    Upload a TICS hex dump (.txt) register file to the fpga for programming

    :param fpath: Path to a TICS register configuration file.
    :type fpath: str
    :param port: Port to use for upload, default to `None` using a random port.
    :type port: int, optional
    :param force_upload: Force to upload the file at `fpath`.
    :type force_upload: bool, optional

    :return: `True` if `fpath` is uploaded successfuly or already exists on
        remote filesystem. `False` otherwise.
    :rtype: bool

    :raises KatcpRequestFail: If KatcpTransport encounters an error.
    """
    t = self.parent.transport

    os.path.getsize(fpath)
    fname = os.path.basename(fpath)

    if not force_upload:
      clkfiles = self.show_clk_files()
      if clkfiles.count(fname) == 1:
        print("file exists on remote filesystem, not uploading. Use `force_upload=True` to overwrite.")
        return True

    if not port:
      port = random.randint(2000, 2500)

    t.upload_to_flash(fpath , port=port, force_upload=force_upload)

    return True


  def progpll(self, plltype, fpath=None, upload=False, port=None):
    """
    Program target RFPLL named by `plltype` with tics hexdump (.txt) register file named by
    `fpath`. Optionally upload the register file to the remote.

    :param plltype: Options are 'lmk' or 'lmx'
    :type client: str

    :param fpath: Local path to a tics hexdump register file, or the name of an
        available remote tics register file, default is that tcpboprphserver will look
        for a file called `rfpll.txt`.
    :type fpath: str, optional

    :param upload: Inidicate that the configuration file is local to the client and
        should be uploaded to the remote, this will overwrite any clock file on the remote
        by the same name.
    :type upload: bool, optional

    :param port: Port number to use for upload, default is `None` and will use a random port.
    :type port: int, optional

    :return: `True` if completes successfuly, `False` otherwise.
    :rtype: bool

    :raises KatcpRequestFail: If KatcpTransport encounters an error.
    """
    t = self.parent.transport

    plltype = plltype.lower()
    if plltype not in [self.LMK, self.LMX]:
      print('not a valid pll type')
      return False

    if fpath:
      if upload:
        os.path.getsize(fpath)
        self.upload_clk_file(fpath, force_upload=True)

      fname = os.path.basename(fpath)
      args = (plltype, fname)
    else:
      args = (plltype,)

    reply, informs = t.katcprequest(name='rfdc-progpll', request_timeout=t._timeout, request_args=args)

    return True


  def status(self):
    """
    Get RFDC ADC/DAC tile status. If tile is enabled, the tile state machine current state 
    and internal PLL lock status are reported. See "Power-on Sequence" in PG269 for more information.

    State values range from 0-15. A tile for the RFDC is considered operating nominally with valid
    data present on the interface when in state 15. If in any other state the RFDC is waiting for
    an electrical condition (sufficient power, clock presence, etc.). A summary of the mappings from
    state value to current seuqencing is as follows:

    0-2  : Device Power-up and Configuration
    3-5  : Power Supply adjustment
    6-10 : Clock configuration
    11-13: Converter Calibration (ADC only)
    14   : wait for deassertion of AXI4-Stream reset
    15   : Done, the rfdc is ready and operating

    :return: Dictionary for current enabled state of ADC/DACs
    :rtype: dict[str, int]

    :raises KatcpRequestFail: If KatcpTransport encounters an error
    """
    t = self.parent.transport

    reply, informs = t.katcprequest(name='rfdc-status', request_timeout=t._timeout)
    status = {}
    for i in informs:
      # example inform (same format for DAC): 'ADC0: Enabled 1, State 15, PLL' or 'ADC0: Enabled 0'
      info = i.arguments[0].decode().split(': ')
      tile = info[0]
      stat = info[1].split(', ')
      d = {}
      for s in stat:
        k, v = s.split(' ')
        d[k] = int(v)
      status[tile] = d

    return status


  def shutdown(self, ntile, converter_type):
    """
    Shutdown target tile. Typical use case is to apply a dynamic setting and then shutdown and startup tile.
    """
    t = self.parent.transport
    args = (ntile, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-shutdown', request_timeout=t._timeout, request_args=args)


  def startup(self, ntile, converter_type):
    """
    Startup target tile. Typical use case is to apply a dynamic setting and then shutdown and startup tile.
    """
    t = self.parent.transport
    args = (ntile, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-startup', request_timeout=t._timeout, request_args=args)


  def get_fabric_clk_freq(self, ntile, converter_type):
    """
    Get the clock frequency at the PL/fifo interface between the adc or dac indicated by "converter_type" on tile "ntile".  "converter_type"
    must be "adc" or "dac" and "ntile" must be in the range (0-3).

    :param ntile: Tile index of target converter, in the range (0-3)
    :type ntile: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str

    :return: fabric clk frequency in MHz, "None" if target converter tile is disabled
    :rtype: float

    Examples
    ---------
    # get fabric clock for ADC Tile 0
    >>>> rfdc.get_fabric_clk_freq(0, rfdc.ADC_TILE)
    245.76
    # get fabric clock for DAC Tile 1
    >>>> rfdc.get_fabric_clk_freq(1, rfdc.DAC_TILE)
    245.76
    """
    t = self.parent.transport

    args = (ntile, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-get-fab-clk-freq', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode()
    if info == "(disabled)":
      return None
    else:
      return float(info)


  def get_fab_clk_div_out(self, ntile, converter_type):
    """
    Get tile's output clock divider setting

    :param ntile: Tile index of target converter, in the range (0-3)
    :type ntile: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str

    :return: Converter tile clk out divider value
    :rtype: int
    """
    t = self.parent.transport
    args = (ntile, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-get-fab-clkdiv-out', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode()
    if info == "(disabled)": # (disabled) response
      return None
    else:
      fab_clk_div_out = info
      return int(fab_clk_div_out)


  def set_fab_clk_div_out(self, ntile, converter_type, clk_div):
    """
    Set tile's output clock divider setting

    :param ntile: Tile index of target converter, in the range (0-3)
    :type ntile: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param clk_div: Desired divider setting for tile output clock

    :return: Converter tile clk out divider value
    :rtype: int
    """
    t = self.parent.transport
    args = (ntile, "adc" if converter_type == self.ADC_TILE  else "dac", clk_div)
    reply, informs = t.katcprequest(name='rfdc-set-fab-clkdiv-out', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode()
    if info == "(disabled)": # (disabled) response
      return None
    else:
      fab_clk_div_out = info
      return int(fab_clk_div_out)


  def get_datatype(self, ntile, nblk, converter_type):
    """
    Get the output datetype of the adc or dac indicated by "converter_type" for the tile/block pair "ntile" and "nblk". "converter_type"
    must be "adc" or "dac" and "ntile" and "nblk" must both be in the range (0-3). Returning 0 represents real-valued data and 1 represents
    complex-valued data.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str

    :return: Integer value that is to map back to the converter output type, 0: real-valued, 1: complex-valued. Returns None if the target
    converter is disabled
    :rtype: int

    Examples
    ----------
    # get data type for ADC 00
    >>>> rfdc.get_datatype(0,0,rfdc.ADC_TILE)
    0 # Real-valued (rfdc.DTYPE_REAL)

    # get data type for ADC 10
    >>>> rfdc.get_datatype(1,0,rfdc.ADC_TILE)
    1 # Complex-valued

    # get data type for DAC 00
    >>>> rfdc.get_datatype(0,0,rfdc.DAC_TILE)
    0 # Real-valued
    """
    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-get-datatype', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode()
    if info == "(disabled)":
      return None
    else:
      return int(info)


  def get_datawidth(self, ntile, nblk, converter_type):
    """
    Get the datawidth, in samples, at the PL/fifo interface between the adc or dac indicated by "converter_type" for the tile/block pair
    "ntile/nblk". "converter_type" must be "adc" or "dac" and "ntile" and "nblk" must both be in the range (0-3).

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str

    :return: Number of 16-bit samples at the output of the converter. Returns None if the target converter is disabled. For real-value
    output this is the same as the number of samples. For complex-valued outputs, this is the number I, or Q, samples at the output of the
    interface. On dual-tile platforms, this is the total number of 16-bit I and Q samples combined together.
    :rtype: int

    Examples
    ----------
    # get number of samples out of axis interface for ADC 00
    rfdc.get_datawidth(0,0,rfdc.ADC_TILE)
    4 # four samples per clock

    # get number of input to the axis interface for DAC 00
    rfdc.get_datawidth(0,0,rfdc.DAC_TILE)
    8 # eight samples per clock
    """
    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-get-datawidth', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode()
    if info == "(disabled)":
      return None
    else:
      return int(info)


  def get_nyquist_zone(self, ntile, nblk, converter_type):
    """
    Get the nyquist zone setting for a specified adc/dac

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str

    :return: Currently configured Nyquist zone (1 or 2) for target converter. Returns None if converter is disabled.
    :rtype: int

    Examples
    ---------
    # get nyquist zone for ADC 00
    >>>> rfdc.get_nyquist_zone(0,0,rfdc.ADC_TILE)
    2 # Nyquist zone 2

    # get nyquist zone for DAC 10
    >>>> rfdc.get_nyquist_zone(1,0,rfdc.DAC_TILE)
    1 # Nyquist zone 1
    """
    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-get-nyquist-zone', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: # (disabled) response
      return None
    else:
      nyquist_zone = info[1]
      return int(nyquist_zone)


  def set_nyquist_zone(self, ntile, nblk, converter_type, nyquist_zone):
    """
    Set the nyquist zone for a specified adc/dac

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param nyquist_zone: target nyquist zone value (1 or 2)

    :return: Configured Nyquist zone for target converter. Returns None if converter is disabled.
    :rtype: int

    Examples
    ---------
    # set nyquist zone for ADC 00
    >>>> rfdc.set_nyquist_zone(0, 0, rfdc.ADC_TILE, rfdc.NYQUIST_ZONE2)
    2 # Nyquist zone 2

    # set nyquist zone for DAC 10
    >>>> rfdc.set_nyquist_zone(1, 0, rfdc.DAC_TILE, rfdc.NYQUIST_ZONE1)
    1 # Nyquist zone 1
    """
    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac", nyquist_zone)
    reply, informs = t.katcprequest(name='rfdc-set-nyquist-zone', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: # (disabled) response
      return None
    else:
      nyquist_zone = info[1]
      return int(nyquist_zone)


  def get_coarse_delay(self, ntile, nblk, converter_type):
    """
    Coarse delay allows adjusting the delay in the digital datapath, which can be useful to compensate for delay mismatch in a system
    implementation. The compensation here is limited to periods of the sampling clock. The following shows the number of periods of
    the sampling clock (T1 = sample clock period, or T2=2*T1, i.e., twice the sample period).

    Delay tuning capability in Gen 1/Gen 2 devices:

    Tile Type  | Digital Control | Coarse Delay Step
    ------------------------------------------------
    Dual Tile       0 to 7              T2
    Quad Tile       0 to 7              T1
    RF-DAC          0 to 7              T1

    Delay tuning capability in Gen 3 devices:

    All tile types have digital delay control 0-40 in coarse T1 delay steps.

    Note: for PCB design and flight time information to correct delay adjustment see the UltraScale Architecture PCB Design User Guide (UG583).

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str

    :return: Digital datapath coarse delay value in units of sample clock period as shown in above table. Returns None if the target
             converter is disabled.
    :rtype: int

    Examples
    ---------
    # get coarse delay for ADC 00
    >>>> rfdc.get_coarse_delay(0,0, rfdc.ADC_TILE)
    {'CoarseDelay': 8, 'EventSource': 2}
    """
    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-get-coarse-delay', request_timeout=t._timeout, request_args=args)

    coarse_delay = {}
    info = informs[0].arguments[0].decode().split(', ')
    if len(info) == 1: # (disabled) response
      return coarse_delay

    for stat in info:
      k,v = stat.split(' ')
      coarse_delay[k] = int(v)

    return coarse_delay


  def set_coarse_delay(self, ntile, nblk, converter_type, coarse_delay, event_source):
    """
    Coarse delay allows adjusting the delay in the digital datapath, which can be useful to compensate for delay mismatch in a system
    implementation. The compensation here is limited to periods of the sampling clock. The following shows the number of periods of
    the sampling clock (T1 = sample clock period, or T2=2*T1, i.e., twice the sample period).

    Delay tuning capability in Gen 1/Gen 2 devices:

    Tile Type  | Digital Control | Coarse Delay Step
    ------------------------------------------------
    Dual Tile       0 to 7              T2
    Quad Tile       0 to 7              T1
    RF-DAC          0 to 7              T1

    Delay tuning capability in Gen 3 devices:

    All tile types have digital delay control 0-40 in coarse T1 delay steps.

    Note: for PCB design and flight time information to correct delay adjustment see the UltraScale Architecture PCB Design User Guide (UG583).

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param coarse_delay: Coarse delay in the number of samples. Range: (0-7) for Gen 1/Gen 2 devices and (0-40) for Gen 3 devices.
    :type coarse_delay: int
    :param event_souce: Event source for update of coarse delay settings
    :type event_source: int

    :return: Digital datapath coarse delay value in units of sample clock period as shown in above table. Returns None if the target
             converter is disabled.
    :rtype: int

    Examples
    ---------
    # set coarse delay to 12*T1 for ADC 00 on a gen 3 device and setup to apply on a tile event
    >>>> rfdc.set_coarse_delay(0, 0, rfdc.ADC_TILE, 12, rfdc.EVNT_SRC_TILE)
    {'CoarseDelay': 12, 'EventSource': 2}
    # trigger update event to apply
    rfdc.update_event(0, 0, rfdc.ADC_TILE, rfdc.EVENT_COARSE_DLY)
    """
    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac", coarse_delay, event_source)
    reply, informs = t.katcprequest(name='rfdc-set-coarse-delay', request_timeout=t._timeout, request_args=args)

    coarse_delay = {}
    info = informs[0].arguments[0].decode().split(', ')
    if len(info) == 1: # (disabled) response
      return coarse_delay

    for stat in info:
      k,v = stat.split(' ')
      coarse_delay[k] = v

    return coarse_delay


  def get_qmc_settings(self, ntile, nblk, converter_type):
    """
    Get quadrature modulator correction (QMC) settings for target converter.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str

    :return: dictionary of QMC settings. Returns None if the target converter is disabled.
    :rtype: dict[str, float]

    Examples
    ---------
    >>>> rfdc.get_qmc_settings(0,1, rfdc.ADC_TILE)
    {'EnablePhase': 0.0,
     'EnableGain': 1.0,
     'GainCorrectionFactor': 0.949951,
     'PhaseCorrectionFactor': 0.0,
     'OffsetCorrectionFactor': 0.0,
     'EventSource': 2.0}
    """
    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-get-qmc-settings', request_timeout=t._timeout, request_args=args)

    qmc_settings = {}
    info = informs[0].arguments[0].decode().split(', ')
    if len(info) == 1: # (disabled) response
      return qmc_settings

    for stat in info:
      k,v = stat.split(' ')
      qmc_settings[k] = float(v)

    return qmc_settings


  def set_qmc_settings(self, ntile, nblk, converter_type, enable_phase, phase_correction_factor,
                        enable_gain, gain_correction_factor,
                        offset_correction_factor, event_source):
    """
    Set quadrature modulator correction (QMC) settings for target converter. The QMC is used to correct imbalance in I/Q datapaths
    after front end analog conversion. Error and/or imbalance detection is an application specific process.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param enable_phase: Indicates if phase is enabled (1) or disabled (0).
    :type enable_phase: int
    :param phase_correction_factor: Phase correction factor. Range: +/- 26.5 degrees (Exclusive).
    :type phase_correction_factor: float
    :param enable_gain: Indicates if gain is enabled(1) or disabled (0).
    :type enable_gain: int
    :param gain_correction_factor: Gain correction factor. Range: 0 to 2.0 (Exclusive).
    :type gain_correction_factor: float
    :param offset_correction_factor: Offset correction factor is adding a fixed LSB value to the sampled signal.
    :type offset_correction_factor: int
    :param event_source: Event source for QMC settings.
    :type event_source: int

    :return: dictionary of applied QMC settings. Returns None if the target converter is disabled.
    :rtype: dict[str, float]

    Examples
    ----------
    # set QMC settings to adjust gain and phase for adc 0 in tile 0 to update with a tile event
    >>>> rfdc.set_qmc_settings(0,0, rfdc.ADC_TILE, 1, -5.0, 1, 0.9, 0, rfdc.EVNT_SRC_TILE)
    {'EnablePhase': 1.0,
     'EnableGain': 1.0,
     'GainCorrectionFactor': 0.899902,
     'PhaseCorrectionFactor': -5.0,
     'OffsetCorrectionFactor': 0.0,
     'EventSource': 2.0}

    # set QMC settings for adc 1 to just adjust gain in tile 0 to update with a tile event
    >>>> rfdc.set_qmc_settings(0,1, rfdc.ADC_TILE, 0, 0, 1, 0.95, 0, rfdc.EVNT_SRC_TILE)
    {'EnablePhase': 0.0,
     'EnableGain': 1.0,
     'GainCorrectionFactor': 0.949951,
     'PhaseCorrectionFactor': 0.0,
     'OffsetCorrectionFactor': 0.0,
     'EventSource': 2.0}

    # generate a tile update event to apply QMC settings, both are in the same tile needing to specify the event once using either 0 or 1
    >>>> rfdc.update_event(0, 0, rfdc.ADC_TILE, rfdc.EVENT_QMC)
    """
    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac", enable_phase, enable_gain,
            gain_correction_factor, phase_correction_factor, offset_correction_factor, event_source)
    reply, informs = t.katcprequest(name='rfdc-set-qmc-settings', request_timeout=t._timeout, request_args=args)

    qmc_settings = {}
    info = informs[0].arguments[0].decode().split(', ')
    if len(info) == 1: # (disabled) response
      return qmc_settings

    for stat in info:
      k,v = stat.split(' ')
      qmc_settings[k] = float(v)

    return qmc_settings


  def update_event(self, ntile, nblk, converter_type, event):
    """
    Use this function to trigger the update event for an event if the event source is Slice or Tile.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param event: trigger update event for mixer, coarse delay, or qmc.
    :type event: int

    :return: None
    :rtype: None

    Examples
    ---------
    # make an adjustment to the coarse delay, mixer, or qmc then trigger update event to apply
    >>>> rfdc.set_coarse_delay(0, 0, rfdc.ADC_TILE, 12, rfdc.EVNT_SRC_TILE)
    {'CoarseDelay': 12, 'EventSource': 2}
    # trigger update event to apply
    rfdc.update_event(0, 0, rfdc.EVENT_COARSE_DELAY)
    """
    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac", event)
    reply, informs = t.katcprequest(name='rfdc-update-event', request_timeout=t._timeout, request_args=args)


  def get_pll_config(self, ntile, converter_type):
    """
    Reads the PLL settings for a converter tile.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str

    :return: Dictionary with converter tile PLL settings, empty dictionary if tile/block is disabled
    :rtype: dict[str, float]

    Examples
    ----------
    # get PLL configuration for tile ADC Tile 0
    >>>> rfdc.get_pll_config(0,rfdc.ADC_TILE)
   {'Enabled': 1.0,
    'RefClkFreq': 491.52,
    'SampleRate': 3.93216,
    'RefClkDivider': 24.0,
    'FeedbackDivider': 3.0,
    'OutputDivider': 0.0}
    """
    t = self.parent.transport

    args = (ntile, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-get-pll-config', request_timeout=t._timeout, request_args=args)

    pll_config = {}
    info = informs[0].arguments[0].decode().split(', ')
    if len(info) == 1: # (disabled) response
      return pll_config

    for stat in info:
      k,v = stat.split(' ')
      pll_config[k] = float(v)

    return pll_config


  def set_pll_config(self, ntile, converter_type, clk_src, pll_ref_freq, sample_rate):
    """
    Dyanmically configure PLL settings for a converter tile.

    When changing the reference pll reference frequency must call `shutdown()` and `startup()`
    to reinitialize the tile and relock pll.

    :param ntile: Tile index of where target converter block is, in the range (0-3).
    :type ntile: int
    :param converter_type: Represents the target converter type, "adc" or "dac".
    :type converter_type: str
    :param clk_src: Internal PLL or external clock source.
    :type clk_src: int
    :param pll_ref_freq: Reference clock frequency in MHz (FREF min to FREF max).
    :type pll_ref_freq: float
    :param sample_rate: Sampling rate frequency in MHz (Fs min to Fs max).
    :type sample_rate: float

    :return: Dictionary with converter tile PLL settings, empty dictionary if tile/block is disabled.
    :rtype: dict[str, float]

    Examples
    ---------
    # set pll reference frequency to 245.76 MHz on ADC Tile 0
    >>>> rfdc.set_pll_config(0, rfdc.ADC_TILE, rfdc.CLK_SRC_INTERNAL, 245.76, 3932.16)
    {'Enabled': 1.0,
     'RefClkFreq': 245.76,
     'SampleRate': 3.93216,
     'RefClkDivider': 48.0,
     'FeedbackDivider': 3.0,
     'OutputDivider': 0.0}

    # shutdown and startup tile to relock PLL to new frequency
    >>>> rfdc.shutdown(0,rfdc.ADC_TILE)
    >>>> rfdc.startup(0,rfdc.ADC_TILE)
    """
    t = self.parent.transport

    args = (ntile, "adc" if converter_type == self.ADC_TILE  else "dac", clk_src, pll_ref_freq, sample_rate)
    reply, informs = t.katcprequest(name='rfdc-set-pll-config', request_timeout=t._timeout, request_args=args)

    pll_config = {}
    if len(informs) > 0: # (disabled) response, the only time this function informs is if the tile is disabled
      return pll_config

    return self.get_pll_config(ntile, converter_type)


  def get_pll_lock_status(self, ntile, converter_type):
    """
    Gets the PLL lock status for target converter tile.

    :param ntile: Tile index of where target converter block is, in the range (0-3).
    :type ntile: int
    :param converter_type: Represents the target converter type, "adc" or "dac".
    :type converter_type: str

    :return: PLL lock status, empty if tile/block is disabled or internal PLL not used.
    :rtype: int

    Examples
    ----------
    # get PLL lock status for ADC Tile 0
    >>>> rfdc.get_pll_lock_status(0,rfdc.ADC_TILE)
    1 # Unlocked

    # get PLL lock status for ADC Tile 2
    >>>> rfdc.get_pll_lock_status(2,rfdc.ADC_TILE)
    2 # Locked
    """
    t = self.parent.transport

    args = (ntile, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-pll-lock-status', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode().split(' ')
    pll_lock_status = info[1]
    if info == "(disabled)":
      return None
    else:
      return int(pll_lock_status)


  def get_clk_src(self, ntile, converter_type):
    """
    Gets the source for target converter tile sample clock (external or internal PLL).

    :param ntile: Tile index of where target converter block is, in the range (0-3).
    :type ntile: int
    :param converter_type: Represents the target converter type, "adc" or "dac".
    :type converter_type: str

    :return: Source for sample clock, 0: external, 1: internal PLL, empty if tile is disabled.
    :rtype: int

    Examples
    ----------
    # get sample clock source for ADC Tile 0
    >>>> rfdc.get_clk_src(0, rfdc.ADC_TILE)
    1 # internal RF PLL

    # get sample clock source for ADC Tile 1
    >>>> rfdc.get_clk_src(1, rfdc.ADC_TILE 1
    0 # external clock
    """
    t = self.parent.transport

    args = (ntile, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-get-clk-src', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode().split(' ')
    clk_src = info[1]
    if info == "(disabled)":
      return None
    else:
      return int(clk_src)


  def get_dsa(self, ntile, nblk):
    """
    Get the step attenuator (DSA) value for an enaled ADC block. If a tile/block pair is disabled
    an empty dictionary is returned and nothing is done.

    :param ntile: Tile index of target block to apply attenuation, in the range (0-3)
    :type ntile: int
    :param nblk: Block index of target adc to apply attenuation, must be in the range (0-3)
    :type nblk: int

    :return: Dictionary with dsa value, empty dictionary if tile/block is disabled
    :rtype: dict[str, str]

    :raises KatcpRequestFail: If KatcpTransport encounters an error

    Examples
    ---------
    # get DSA for ADC 00
    >>>> rfdc.get_dsa(0, 0)
    {'dsa': '10'}

    # get DSA for ADC 10
    >>>> rfdc.get_dsa(1, 0)
    {'dsa': '0'}
    """
    t = self.parent.transport

    args = (ntile, nblk)
    reply, informs = t.katcprequest(name='rfdc-get-dsa', request_timeout=t._timeout, request_args=args)

    dsa = {}
    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: # (disabled) response
      return dsa

    k = info[0]
    v = info[1]
    dsa = {k:v}
    return dsa


  def set_dsa(self, ntile, nblk, atten_dB):
    """
    Set the digital step attenuator (DSA) of enabled tile "ntile" and adc block "nblk" to the
    value specified by `atten_dB`.

    After write the attenuation value is read and. If a tile/blk pair is disabled an empty
    dictionary is returned and nothing is done.

    ES1 silicon can command attenuation levels from 0-11 dB with a step of 0.5 dB. Production
    silicon can command to levels 0-27 dB with a step of 1.0 dB.

    See Xilinx/AMD PG269 for more details on the DSA in the RFDC. This is only available on
    Gen 3 devices.

    :param ntile: Tile index of target block to apply attenuation, in the range (0-3)
    :type ntile: int
    :param nblk: Block index of target adc to apply attenuation, must be in the range (0-3)
    :type nblk: int
    :param atten_dB: Requested attenuation level
    :type float:

    :return: Dictionary with dsa value, empty dictionary if tile/block is disabled
    :rtype: dict[str, str]

    :raises KatcpRequestFail: If KatcpTransport encounters an error

    Examples
    ----------
    # set_dsa for ADC 10
    >>>> rfdc.set_dsa(1, 0, 20)
    {'dsa': '20'}
    """
    t = self.parent.transport

    args = (ntile, nblk, atten_dB,)
    reply, informs = t.katcprequest(name='rfdc-set-dsa', request_timeout=t._timeout, request_args=args)

    dsa = {}
    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: # (disabled) response
      return dsa

    k = info[0]
    v = info[1]
    dsa = {k:v}
    return dsa


  def get_cal_freeze(self, ntile, nblk):
    """
    Get the adc calibration freeze status for enabled tile "ntile" and block index "nblk". If a tile/block
    pair is disabled an empty dictionary is returned and nothing is done.

    :param ntile: Tile index of target adc tile to get calibration freeze status, in the range (0-3)
    :type ntile: int
    :param nblk: Block index of target adc block to get output current, must be in the range (0-3)
    :type nblk: int

    :return: Dictionary with freeze settings, empty dictionary if tile/block is disabled
    :rtype: dict[str, int]

    :raises KatcpRequestFail: If KatcpTransport encounters an error

    Examples
    ----------
    # get the calibration freeze status for ADC 00
    >>>> ntile=0, nblk=0
    >>>> rfdc.get_cal_freeze(ntile, nblk)
    {'CalFreeze': 0, 'DisableCalPin': 0, 'CalibrationFreeze': 0}
    """
    t = self.parent.transport

    args = (ntile, nblk)
    reply, informs = t.katcprequest(name='rfdc-get-cal-freeze', request_timeout=t._timeout, request_args=args)

    freeze_settings = {}
    info = informs[0].arguments[0].decode().split(', ')
    if len(info) == 1: # (disabled) response
      return freeze_settings

    for stat in info:
      k,v = stat.split(' ')
      freeze_settings[k] = v

    return freeze_settings


  def set_cal_freeze(self, ntile, nblk, freeze):
    """
    Set adc calibration freeze status for enabled tile "ntile" and block index "nblk". If a tile/block
    pair is disabled an empty dictionary is returned and nothing is done.

    :param ntile: Tile index of target adc tile to get calibration freeze status, in the range (0-3)
    :type ntile: int
    :param nblk: Block index of target adc block to get output current, must be in the range (0-3)
    :type nblk: int
    :param freeze: 1 - indicates the calibration should be frozen. 0 - calibration should be unfrozen.

    :return: Dictionary with freeze settings after applying new freeze value, empty dictionary if tile/block is disabled.
    Freeze settings fields are:
      - CalFrozen: indicates the current status of the background calibration functions. 1 - indicates the calibration
        is frozen; 0 - indicates background calibration is operating normally.
      - DisableFreezePin: this is not accessible by the casperfpga api at the moment
      - FreezeCalibration: software register controling freeze calibration from the driver (this is what is set to freeze
        the calibration). If this value is set calibration should also be frozen as indicated by "CalFrozen".
    :rtype: dict[str, int]

    :raises KatcpRequestFail: If KatcpTransport encounters an error

    Examples
    ---------
    # freeze the calibration for ADC 01
    >>>> ntile=0, nblk=1
    >>>> rfdc.set_cal_freeze(ntile, nblk, rfdc.CAL_FREEZE)
    {'CalFreeze': 1, 'DisableCalPin': 0, 'CalibrationFreeze': 1}

    # unfreeze the calibration for ADC 01
    rfdc.set_cal_freeze(ntile, nblk, rfdc.CAL_UNFREEZE)
    {'CalFreeze': 0, 'DisableCalPin': 0, 'CalibrationFreeze': 0}
    """
    t = self.parent.transport

    args = (ntile, nblk, freeze)
    reply, informs = t.katcprequest(name='rfdc-set-cal-freeze', request_timeout=t._timeout, request_args=args)

    freeze_settings = {}
    info = informs[0].arguments[0].decode().split(', ')
    if len(info) == 1: # (disabled) response
      return freeze_settings

    for stat in info:
      k,v = stat.split(' ')
      freeze_settings[k] = v

    return freeze_settings


  def get_cal_coeffs(self, ntile, nblk, calblk):
    """
    Get the adc calibration coefficients for enabled tile "ntile" and block index "nblk" for the background calibration blocks. If a
    tile/block pair is disabled an empty dictionary is returned and nothing is done. Valid calblk values are 0 (for gen3 devices) 1-3 and
    represent the OCB1, OCB2, GCB, and TSCB background calibration blocks, respectively. A user can also use rfdc.OCB1, rfdc.OCB2, rfdc.GCB, and
    rfdc.TSCB to target calibration blocks, E.g., `get_cal_coeffs(0, 0, rfdc.OCB2)`.

    Coeff{4-7} applies when chopping is active and is only relevant to the time skew calibration block (TSCB).

    :param ntile: Tile index of target adc tile to get calibration freeze status, in the range (0-3)
    :type ntile: int
    :param nblk: Block index of target adc block to get output current, must be in the range (0-3)
    :type nblk: int
    :param calblk: Calibration block index, range 0 (for gen3 devices only) 1-3 representing the OCB1, OCB2, GCB, and TSCB respectively.
    :type calblk: int

    :return: Dictionary with calibration coefficients, empty dictionary if tile/block is disabled
    :rtype: dict[str, int]

    :raises KatcpRequestFail: If KatcpTransport encounters an error

    Examples
    ---------
    # target ADC will be ADC 00
    >>>> ntile=0, nblk=0

    # get calibration coefficients for background calibration blocks OCB1/2
    >>>> rfdc.get_cal_coeffs(ntile, nblk, rfdc.CAL_BLOCK_OCB1) # or CAL_BLOCK_OCB2
    {'Coeff0': '4293263342',
     'Coeff1': '4293001175',
     'Coeff2': '4294770722',
     'Coeff3': '393233',
     'Coeff4': '0',
     'Coeff5': '0',
     'Coeff6': '0',
     'Coeff7': '0'}

    # get calibration coefficients for background gain calibration block (GCB)
    >>>> rfdc.get_cal_coeffs(ntile, nblk rfdc.CAL_BLOCK_OCB1)
    {'Coeff0': '2162688',
     'Coeff1': '659380',
     'Coeff2': '261688808',
     'Coeff3': '1114162',
     'Coeff4': '0',
     'Coeff5': '0',
     'Coeff6': '0',
     'Coeff7': '0'}

    # get calibration coefficients for background time skew block (TSCB)
    >>>> rfdc.get_cal_coeffs(ntile, nblk, rfdc.CAL_BLOCK_TSCB)
    {'Coeff0': '33489407',
     'Coeff1': '33489407',
     'Coeff2': '33489407',
     'Coeff3': '33489407',
     'Coeff4': '33489407',
     'Coeff5': '33489407',
     'Coeff6': '33489407',
     'Coeff7': '197119'}
    """
    t = self.parent.transport

    args = (ntile, nblk, calblk)
    reply, informs = t.katcprequest(name='rfdc-get-cal-coeffs', request_timeout=t._timeout, request_args=args)

    cal_coeffs = {}
    info = informs[0].arguments[0].decode().split(', ')
    if len(info) == 1: # (disabled) response
      return cal_coeffs

    for stat in info:
      k,v = stat.split(' ')
      cal_coeffs[k] = v

    return cal_coeffs


  def set_cal_coeffs(self, ntile, nblk, calblk, coeffs):
    """
    Set adc calibration coefficients for enabled tile "ntile" and block index "nblk" for the background calibration blocks. If a tile/block
    pair is disabled an empty dictionary is returned and nothing is done. Valid calblk values are 0 (for gen3 devices) 1-3 and represent the
    OCB1, OCB2, GCB, and TSCB background calibration blocks, respectively. A user can also use rfdc.OCB1, rfdc.OCB2, rfdc.GCB, and rfdc.TSCB to
    target calibration blocks, E.g., `get_cal_coeffs(0, 0, rfdc.OCB2)`.

    :param ntile: Tile index of target adc tile to get calibration freeze status, in the range (0-3)
    :type ntile: int
    :param nblk: Block index of target adc block to get output current, must be in the range (0-3)
    :type nblk: int
    :param calblk: Calibration block index, range 0 (for gen3 devices only) 1-3 representing the OCB1, OCB2, GCB, and TSCB respectively.
    :type calblk: int
    :param coeffs: list of eight calibration coefficients
    :type coeffs: list[int]

    :return: Dictionary with calibration coefficients, empty dictionary if tile/block is disabled
    :rtype: dict[str, int]

    :raises KatcpRequestFail: If KatcpTransport encounters an error

    Examples
    _________
    # target adc will be ADC 00
    >>>> ntile=0, nblk=0
    # set calibration coeffs for any of the calibration blocks. Values here are
    # similar to the example shown in RFDC product guide PG269. After setting the
    # coefficient values the driver readsback and returns the applied coefficients
    >>>> coeffs = [136, 255, 255, 137, 255, 225, 255, 136]
    >>>> rfdc.set_cal_coeffs(ntile, nblk, rfdc.CAL_BLOCK_TSCB, coeffs)
    {'Coeff0': '136',
     'Coeff1': '255',
     'Coeff2': '255',
     'Coeff3': '137',
     'Coeff4': '255',
     'Coeff5': '225',
     'Coeff6': '255',
     'Coeff7': '136'}
    """
    t = self.parent.transport

    args = (ntile, nblk, calblk, *coeffs)
    reply, informs = t.katcprequest(name='rfdc-set-cal-coeffs', request_timeout=t._timeout, request_args=args)

    cal_coeffs = {}

    if len(informs) > 0: # (disabled) response, the only time this function informs is if the tile is disabled
      return cal_coeffs

    args = (ntile, nblk, calblk)
    reply, informs = t.katcprequest(name='rfdc-get-cal-coeffs', request_timeout=t._timeout, request_args=args)

    cal_coeffs = {}
    info = informs[0].arguments[0].decode().split(', ')

    for stat in info:
      k,v = stat.split(' ')
      cal_coeffs[k] = v

    return cal_coeffs


  def disable_user_coeffs(self, ntile, nblk, calblk):
    """
    Disables calibration coefficients for calibration block "calblk" set by the user from a call to "set_cal_coeffs()" for target adc 
    indicated by "ntile" and "nblk". A call to "get_cal_coeffs()" is required to show coeffs have been cleared of user values.

    :param ntile: Tile index of target adc, in the range (0-3).
    :type ntile: int
    :param nblk: Block index of target adc within a tile, in the range (0-3).
    :type nblk: int
    :param calblk: Calibration block index, range 0 (for gen3 devices only) 1-3 representing the OCB1, OCB2, GCB, and TSCB respectively.
    :type calblk: int

    :return: None

    :raises KatcpRequestFail: If KatcpTransport encounters an error

    Examples
    ---------
    # target adc will ADC 00
    >>>> ntile=0, nblk=0
    # declare and set user specified coeffs
    >>>> coeffs = [136, 255, 255, 137, 255, 225, 255, 136]
    >>>> rfdc.set_cal_coeffs(ntile, nblk, rfdc.CAL_BLOCK_TSCB, coeffs)
    {'Coeff0': '136',
     'Coeff1': '255',
     'Coeff2': '255',
     'Coeff3': '137',
     'Coeff4': '255',
     'Coeff5': '225',
     'Coeff6': '255',
     'Coeff7': '136'}

    # disable user provided coefficients and revert to automatic background calibration
    >>>> rfdc.disable_user_coeffs(ntile, nblk, rfdc.CAL_BLOCK_TSCB)
    # must make a call to `get_cal_coeffs()` to confirm they have been reverted.
    >>>> rfdc.get_cal_coeffs(0,0,rfdc.CAL_BLOCK_TSCB)
    {'Coeff0': '33489407',
     'Coeff1': '33489407',
     'Coeff2': '33489407',
     'Coeff3': '6029823',
     'Coeff4': '33489407',
     'Coeff5': '33489407',
     'Coeff6': '33489407',
     'Coeff7': '197119'}
    """
    t = self.parent.transport

    args = (ntile, nblk, calblk)
    reply, informs = t.katcprequest(name='rfdc-disable-user-coeffs', request_timeout=t._timeout, request_args=args)


  def get_cal_mode(self, ntile, nblk):
    """
    Get the calibration mode for target converter selected by the tile/blk pair "ntile"/"nblk". It a tile pair is disabled None is returned.

    :param ntile: Tile index of target adc, in the range (0-3).
    :type ntile: int
    :param nblk: Block index of target adc within a tile, in the range (0-3).
    :type nblk: int

    :return: Integer representing calibration Mode, 1: Mode 1, 2: Mode 2. None if target adc is disabled.
    :rtype: int

    :raises KatcpRequestFail: If KatcpTransport encounters an error

    Examples
    ---------
    # get calibration mode
    >>>> ntile=0, nblk=0
    >>>> rfdc.get_cal_mode(0,0)
    2 # indicates mode 2=rfdc.CAL_MODE2 is the current mode
    """
    t = self.parent.transport

    args = (ntile, nblk)
    reply, informs = t.katcprequest(name='rfdc-get-cal-mode', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: # (disabled) response
      return None

    return int(info[1])

  def set_cal_mode(self, ntile, nblk, calmode):
    """
    Set the calibration mode for target converter selected by the tile/blk pair "ntile"/"nblk". It a tile pair is disabled None is returned.

    :param ntile: Tile index of target adc, in the range (0-3).
    :type ntile: int
    :param nblk: Block index of target adc within a tile, in the range (0-3).
    :type nblk: int
    :param calmode: Calibration mode to run on target converter, 1: Mode 1, 2: Mode 2.
    :type calmode: int

    :return: Integer representing calibration Mode, 1: Mode 1, 2: Mode 2. None if target adc is disabled.
    :rtype: int

    :raises KatcpRequestFail: If KatcpTransport encounters an error

    Examples
    ---------
    # set calibration mode for ADC 00
    >>>> rfdc.set_cal_mode(0,0, rfdc.CAL_MODE1)
    1 # indicates mode 1=rfdc.CAL_MODE1 has been applied
    """
    t = self.parent.transport

    args = (ntile, nblk, calmode)
    reply, informs = t.katcprequest(name='rfdc-set-cal-mode', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: # (disabled) response
      return None

    return int(info[1])


  def get_output_current(self, ntile, nblk):
    """
    Get the output current in micro amps of enabled tile "ntile" and dac block "nblk". If a tile/block
    pair is disabled an empty dictionary is returned and nothing is done.

    :param ntile: Tile index of target dac tile to get output current, in the range (0-3)
    :type ntile: int
    :param nblk: Block index of target dac block to get output current, must be in the range (0-3)
    :type nblk: int

    :return: Dictionary with current value in micro amp, empty dictionary if tile/block is disabled
    :rtype: dict[str, str]

    :raises KatcpRequestFail: If KatcpTransport encounters an error

    Examples
    ---------
    # get_output_current for DAC 00
    >>>> rfdc.get_output_current(0, 0)
    {'current': '19993'}

    # get output current for DAC 10
    >>>> rfdc.get_output_current(1, 0)
    {'current': '19993'}
    """
    t = self.parent.transport

    args = (ntile, nblk)
    reply, informs = t.katcprequest(name='rfdc-get-output-current', request_timeout=t._timeout, request_args=args)

    current = {}
    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: # (disabled) response
      return {}

    k = info[0]
    v = info[1]
    current = {k:v}
    return current


  def set_vop(self, ntile, nblk, curr_uA):
    """
    Set the output current in micro amps of enabled tile "ntile" and dac block "nblk". If a tile/block
    pair is disabled an empty dictionary is returned and nothing is done.

    ES1 silicon can command ranges from 6425 to 32000. Production silicon can accept values in the
    range 2250 to 40500. Values are rounded to the nearest increment managed by the rfdc driver. Ranges,
    errors, and bound checks are performed by the driver.

    See Xilinx/AMD PG269 for more details on the VOP capabilities of the RFDC. This Only available on
    Gen 3 device.

    Examples
    ---------
    :param ntile: Tile index of target block to get output current, in the range (0-3)
    :type ntile: int
    :param nblk: Block index of target dac get output current, must be in the range (0-3)
    :type nblk: int
    :param curr_uA: the desired output current in micro amps
    :type curr_uA: int

    :return: Dictionary with current value in micro amp, empty dictionary if tile/block is disabled
    :rtype: dict[str, str]

    :raises KatcpRequestFail: If KatcpTransport encounters an error

    # set output current for DAC00
    >>>> rfdc.set_vop(0, 0, 34500)
    {'current': '34475'}
    """
    t = self.parent.transport

    args = (ntile, nblk, curr_uA,)
    reply, informs = t.katcprequest(name='rfdc-set-vop', request_timeout=t._timeout, request_args=args)

    vop = {}
    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: #(disabled) response
      return vop

    k = info[0]
    v = info[1]
    vop = {k:v}
    return vop


  def get_invsinc_fir(self, ntile, nblk):
    """
    Get the inverse sinc filter mode.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int

    :return: 0 if disabled, 1 if first nyquist, and 2 for second nyquist (gen 3 devices only). Returns None if converter is disabled.

    Examples
    ----------
    >>>> rfdc.get_invsinc_fir(0,0)
    2 # Nyquist zone 2
    """
    t = self.parent.transport

    args = (ntile, nblk)
    reply, informs = t.katcprequest(name='rfdc-get-invsincfir', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: # (disabled) response
      return None
    else:
      invsincfir_mode = info[1]
      return int(invsincfir_mode)


  def set_invsinc_fir(self, ntile, nblk, invsinc_fir_mode):
    """
    Set the inverse sinc filter mode; 0 - disabled, 1 - first nyquist, and for gen 3 devices 2 - second nyquist.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param invsinc_fir_mode: inverse sinc filter mode; 0 - disabled, 1 - first nyquist, and for gen 3 devices 2 - second nyquist.

    :type invsinc_fir_mode: int

    :return: 0 if disabled, 1 if first nyquist, and 2 for second nyquist (gen 3 devices only). Returns None if converter is disabled.
    :rtype: int

    Examples
    ----------
    >>>> rfdc.set_invsinc_fir(0,0,rfdc.INVSINC_FIR_DISABLED)
    0 # disabled

    >>>> rfdc.set_invsinc_fir(0,0,rfdc.INVSINC_FIR_NYQUIST1)
    1 # nyquist zone 1

    >>>> rfdc.set_invsinc_fir(0,0,rfdc.INVSINC_FIR_NYQUIST2)
    2 # nyquist zone 2

    """
    t = self.parent.transport

    args = (ntile, nblk, invsinc_fir_mode)
    reply, informs = t.katcprequest(name='rfdc-set-invsincfir', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: # (disabled) response
      return None
    else:
      invsincfir_mode = info[1]
      return int(invsincfir_mode)


  def invsinc_fir_enabled(self, ntile, nblk):
    """
    If the inverse sinc filter is enabled for target DAC converter, the function returns 1; otherwise, it returns 0.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int

    :return: 1 if filter is enabled; otherwise, returns 0. Returns None if converter is disabled.
    :rtype: int

    Examples
    ----------
    >>>> rfdc.invsinc_fir_enabled(0,0)
    0 # disabled
    """
    t = self.parent.transport

    args = (ntile, nblk)
    reply, informs = t.katcprequest(name='rfdc-invsincfir-enabled', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: # (disabled) response
      return None
    else:
      invsincfir_enabled = info[1]
      return int(invsincfir_enabled)


  def get_imr_mode(self, ntile, nblk):
    """
    Get the image rejection filter mode.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int

    :return: 0 if in lowpass mode, 1 if highpass mode. Returns None if converter is disabled.
    :rtype: int

    Examples
    ----------
    # get image rejection filter mode for DAC 00
    >>>> rfdc.get_imr_mode(0,0)
    2 # high pass
    """
    t = self.parent.transport

    args = (ntile, nblk)
    reply, informs = t.katcprequest(name='rfdc-get-imr-mode', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: # (disabled) response
      return None
    else:
      imr_mode = info[1]
      return int(imr_mode)


  def set_imr_mode(self, ntile, nblk, imr_mode):
    """
    Set the image rejection filter mode; 0 - lowpass, 1 - highpass.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param imr_mode: IMR filter mode set to 0 for lowpass or 1 for high pass.

    :return: 0 if in lowpass mode, 1 if highpass mode. Returns None if converter is disabled.
    :rtype: int

    Examples
    ----------
    # set image rejection filter to high pass for DAC 00
    >>>> rfdc.set_imr_mode(0, 0, rfdc.IMR_HIGHPASS)
    1 # high pass

    # set image rejection filter to lowpass for DAC 00
    >>>> rfdc.set_imr_mode(0, 0, rfdc.IMR_LOWPASS)
    0 # lowpass
    """
    t = self.parent.transport

    args = (ntile, nblk, imr_mode)
    reply, informs = t.katcprequest(name='rfdc-set-imr-mode', request_timeout=t._timeout, request_args=args)

    info = informs[0].arguments[0].decode().split(' ')
    if len(info) == 1: # (disabled) response
      return None
    else:
      imr_mode = info[1]
      return int(imr_mode)


  def run_mts(self, converter_type, tile_mask=15, target_latency=-1):
    """
    Execute multi-tile synchronization (MTS) to synchronize ADC or DAC tiles set by "tile_mask".
    Optionally request to a target latency specified by "target_latency".

    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param mask: Bitmask for selecting which tiles to sync, defaults to all tiles 0x1111 = 15. LSB is ADC/DAC Tile 0.
    :type mask: int
    :param target_latency: Requested target latency
    :type target_latency: int

    :return: `True` if completes successfuly, `False` otherwise
    :rtype: bool

    :raises KatcpRequestFail: If KatcpTransport encounters an error
    """

    t = self.parent.transport
    args = ("adc" if converter_type == self.ADC_TILE  else "dac",tile_mask,target_latency)
    reply, informs = t.katcprequest(name='rfdc-run-mts', request_timeout=t._timeout, request_args=args)

    return True

  def get_mts_latency(self, converter_type, ntile):
    """
    Get the adc or dac mts latency for enabled tile "ntile" and block index "nblk". If a tile/block pair is
    disabled or, no mts information is available, an empty dictionary is returned and nothing is done.

    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param ntile: Tile index of target adc tile to get mts latency, in the range (0-3)
    :type ntile: int

    :return: Dictionary with mts latency, empty dictionary if tile/block is disabled
    :rtype: dict[str, int]

    :raises KatcpRequestFail: If KatcpTransport encounters an error

    Examples
    ----------
    # get the mts latency for ADC 00
    >>>> ntile=0
    >>>> rfdc.get_mts_latency(rfdc.ADC_TILE, ntile)
    {'Latency': 160, 'DelayOffset': 2, 'DecFactor': 4}
    """
    t = self.parent.transport

    args = ("adc" if converter_type == self.ADC_TILE  else "dac",ntile)
    reply, informs = t.katcprequest(name='rfdc-mts-tile-latency', request_timeout=t._timeout, request_args=args)

    mts_latency = {}
    info = informs[0].arguments[0].decode().split(', ')
    if len(info) == 1: # (disabled) response
      return mts_latency

    for stat in info:
      k,v = stat.split(' ')
      mts_latency[k] = v

    return mts_latency


  def mts_debug_info(self, converter_type):
    """
    Prints a detailed report of the most recent multi-tile synchronization run. Including information
    such as latency on each tile, delay maker, delay bit.

    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str

    :return: `True` if completes successfuly, `False` otherwise
    :rtype: bool

    :raises KatcpRequestFail: If KatcpTransport encounters an error
    """
    t = self.parent.transport
    args = ("adc" if converter_type == self.ADC_TILE  else "dac",)
    reply, informs = t.katcprequest(name='rfdc-mts-debug-info', request_timeout=t._timeout, request_args=args)

    for i in informs:
      print(i)

    return True


  def update_nco_mts(self, adc_mask, dac_mask, freq):
    """
    Program and updates NCOs on board while maintaining multi-tile synchronization.

    :param adc_mask: 16 bits indicating what ADCs to set. LSB is ADC 00
    :type adc_mask: int

    :param dac_mask: 16 bits indicating what DACs to set. LSB is DAC 00
    :type dac_mask: int

    :param freq: Frequency in MHz to set the NCO to
    :type freq: float

    :return: `True` if completes successfuly, `False` otherwise
    :rtype: bool

    :raises KatcpRequestFail: If KatcpTransport encounters an error
    """
    t = self.parent.transport
    args = (adc_mask, dac_mask, freq,)
    reply, informs = t.katcprequest(name='rfdc-update-nco-mts', request_timeout=t._timeout, request_args=args)
    for i in informs:
      print(i)
    return True


  def set_mixer_mode(self, ntile, nblk, converter_type, mixer_mode, force=1):
    """
    Set the mixer mode: 0 - Off, C2C - 1, C2R - 2, R2C - 3, R2R - 4. Updates to the target tile and block are applied immediately
    by default. To control when updates are applied set the `force` parameter to zero and manually call `update_event`.

    Constants are available as: MIX_MODE_OFF, MIX_MODE_C2C, MIX_MODE_R2C, MIX_MODE_R2R

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param mixer_mode: Target mixer operating mode
    :type mixer_mode: int
    :param force: Immediately update mixer mode. Set to zero to manually trigger upadte with `update_event`
    :type force: int

    :return: None if converter is disabled.

    Examples
    -----------
    # update the mixer mode, it is immediately applied
    >>>> rfdc.set_mixer_mode(rfdc.MIX_MODE_C2C, 0, 0, rfdc.ADC_TILE)

    # set two different mixers and apply updates at the same time
    >>>> rfdc.set_mixer_mode(rfdc.MIX_MODE_C2C, 0, 0, rfdc.ADC_TILE, force=0)
    >>>> rfdc.set_mixer_mode(rfdc.MIX_MODE_C2C, 1, 0, rfdc.ADC_TILE, force=0)
    >>>> rfdc.update_event(0, 0, rfdc.ADC_TILE, rfdc.EVENT_MIXER)
    """
    t = self.parent.transport
    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac", mixer_mode, force)
    reply, informs = t.katcprequest(name='rfdc-set-mixer-mode', request_timeout=t._timeout, request_args=args)


  def set_fine_mixer_freq(self, ntile, nblk, converter_type, fine_freq, fine_phase=0, force=1):
    """
    Updates the fine mixer frequency in MHz and phase offset in degrees. Updates to the target tile and block are applied immediately by
    default. To control when updates are applied set the `force` parameter to zero and manually call `update_event`.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param fine_freq: Target fine mixer operating frequency in MHz. Range (-fs/2, fs/2]
    :type fine_freq: float
    :param fine_phase: Target fine mixer phase offset in degrees. Range (-180, 180]
    :type fine_phase: float
    :param force: Immediately update mixer mode. Set to zero to manually trigger upadte with `update_event`
    :type force: int

    :return: None if converter is disabled.

    Examples
    -----------
    # update fine frequency mixer, update is immediately applied
    >>>> rfdc.set_fine_mixer_freq(0, 0, rfdc.ADC_TILE, -983.04)

    # set fine frequency mixer for two different converters and apply updates at the same time
    >>>> rfdc.set_fine_mixer_freq(0, 0, rfdc.ADC_TILE, -983.04, force=0)
    >>>> rfdc.set_fine_mixer_freq(0, 1, rfdc.ADC_TILE, -983.04, force=0)
    >>>> rfdc.update_event(0, 0, rfdc.ADC_TILE, rfdc.EVENT_MIXER)
    """
    t = self.parent.transport
    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac", fine_freq, fine_phase, force)
    reply, informs = t.katcprequest(name='rfdc-update-nco', request_timeout=t._timeout, request_args=args)


  def set_mixer_type(self, ntile, nblk, converter_type, mixer_type, force=1):
    """
    Set the mixer type: Coarse - 1, Fine - 2, Off - 0, Disabled - 3. Updates to the target tile and block are applied immediately
    by default. To control when updates are applied set the `force` parameter to zero and manually call `update_event`.

    Constants are available as: MIX_TYPE_COARSE, MIX_TYPE_FINE, MIX_TYPE_OFF, MIX_TYPE_DISABLED

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param mixer_type: Target mixer type
    :type mixer_type: int
    :param force: Immediately update mixer mode. Set to zero to manually trigger upadte with `update_event`
    :type force: int

    :return: None if converter is disabled.

    Examples
    -----------
    # update the mixer mode, it is immediately applied
    >>>> rfdc.set_mixer_type(rfdc.MIX_TYPE_COARSE, 0, 0, rfdc.ADC_TILE)

    # set two different mixers and apply updates at the same time
    >>>> rfdc.set_mixer_type(rfdc.MIX_TYPE_COARSE, 0, 0, rfdc.ADC_TILE, force=0)
    >>>> rfdc.set_mixer_type(rfdc.MIX_MODE_COARSE, 0, 1, rfdc.ADC_TILE, force=0)
    >>>> rfdc.update_event(0, 0, rfdc.ADC_TILE, rfdc.EVENT_MIXER)
    """
    t = self.parent.transport
    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac", mixer_type, force)
    reply, informs = t.katcprequest(name='rfdc-set-mixer-type', request_timeout=t._timeout, request_args=args)


  def set_coarse_mixer_freq(self, ntile, nblk, converter_type, coarse_mixer_freq, force=1):
    """
    Set the coarse mixer frequency: 16 (bypass), 8 (-fs/4), 4 (fs/4), 2 (fs/2), 0 (off). Updates to the target tile and block are applied
    immediately by default. To control when updates are applied set the `force` parameter to zero and manually call `update_event`.

    Constants are available as: MIX_COARSE_BYPASS, MIX_COARSE_NFS4, MIX_COARSE_FS4, MIX_COARSE_FS2, MIX_COARSE_OFF.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param coarse_mixer_freq: Target coarse mixer frequency
    :type coarse_mixer_freq: int
    :param force: Immediately update mixer mode. Set to zero to manually trigger upadte with `update_event`
    :type force: int

    :return: None if converter is disabled.

    Examples
    -----------
    # update the mixer mode, it is immediately applied
    >>>> rfdc.set_coarse_mixer_freq(0, 0, rfdc.ADC_TILE, rfdc.MIX_COARSE_NFS4)

    # set two different mixers and apply updates at the same time
    >>>> rfdc.set_coarse_mixer_freq(0, 0, rfdc.ADC_TILE, rfdc.MIX_COARSE_NFS4)
    >>>> rfdc.set_coarse_mixer_freq(0, 1, rfdc.ADC_TILE, rfdc.MIX_COARSE_NFS4)
    >>>> rfdc.update_event(0, 0, rfdc.ADC_TILE, rfdc.EVENT_MIXER)
    """
    t = self.parent.transport
    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac", coarse_mixer_freq, force)
    reply, informs = t.katcprequest(name='rfdc-set-coarse-mixer-freq', request_timeout=t._timeout, request_args=args)

  def set_mixer_scale(self, ntile, nblk, converter_type, mixer_scale, force=1):
    """
    Set gain correction factor for fine frequency mixer: 0 (Auto), 1 (1.0), 2 (0.7). Updates to the target tile and block are applied
    immediately by default. To control when updates are applied set the `force` parameter to zero and manually call `update_event`.

    Constants are available as: MIX_SCALE_AUTO, MIX_SCALE_1P0, MIX_SCALE_0P7

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param mixer_scale: Gain correction factor for fine frequency mixer
    :type mixer_scale: int
    :param force: Immediately update mixer mode. Set to zero to manually trigger upadte with `update_event`
    :type force: int

    :return: None if converter is disabled.

    Examples
    -----------
    # update scale factor for fine frequency mixer, it is immediately applied
    >>>> rfdc.set_mixer_scale(0, 0, rfdc.ADC_TILE, rfdc.MIX_SCALE_0P7)

    # set for two different mixers and apply updates at the same time
    >>>> rfdc.set_mixer_scale(0, 0, rfdc.ADC_TILE, rfdc.MIX_SCALE_0P7)
    >>>> rfdc.set_mixer_scale(0, 1, rfdc.ADC_TILE, rfdc.MIX_SCALE_0P7)
    >>>> rfdc.update_event(0, 0, rfdc.ADC_TILE, rfdc.EVENT_MIXER)
    """
    t = self.parent.transport
    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac", mixer_scale, force)
    reply, informs = t.katcprequest(name='rfdc-set-mixer-scale', request_timeout=t._timeout, request_args=args)


  def set_mixer_event_source(self, ntile, nblk, converter_type, event_source):
    """
    Set mixer event source.

    Constants are available as:
      EVNT_SRC_IMMEDIAT = 0 # Update after register write (not available on dual-tile adcs)
      EVNT_SRC_SLICE = 1    # Update using SLICE (not available on dual-tile adcs)
      EVNT_SRC_TILE = 2     # Update using TILE
      EVNT_SRC_SYSREF = 3   # Update using SYSREF
      EVNT_SRC_MARKER = 4   # update using MARKER
      EVNT_SRC_PL = 5       # update using PL event

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str
    :param event_source: Event source that will trigger update to parameters
    :type event_source: int

    :return: None if converter is disabled.

    Examples
    -----------
    >>>> rfdc.set_mixer_event_source(0, 0, rfdc.ADC_TILE, rfdc.EVNT_SRC_SYSREF)
    """
    t = self.parent.transport
    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac", event_source)
    reply, informs = t.katcprequest(name='rfdc-set-mixer-event-source', request_timeout=t._timeout, request_args=args)


  def get_mixer_settings(self, ntile, nblk, converter_type):
    """
    Get mixer configuration settings for a target converter

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param converter_type: Represents the target converter type, "adc" or "dac"
    :type converter_type: str

    :return: Dictionary with mixer configurations PLL settings, empty dictionary if tile/block is disabled
    :rtype: dict[str, float]
    """

    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-get-mixer-settings', request_timeout=t._timeout, request_args=args)

    mixer_config = {}
    info = informs[0].arguments[0].decode().split(', ')
    if len(info) == 1: # (disabled) response
      return mixer_config

    for stat in info:
      k,v = stat.split(' ')
      mixer_config[k] = float(v)

    return mixer_config

  def get_adc_threshold(self, ntile, nblk):
    """
    Get the threshold settings for target ADC

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nlblk: int

    :return: dict[str, int] of threshold settings, otherwise None if target converter is disabled

    Examples
    ---------
    >>>> rfdc.set_adc_thresh(0, 0, rfdc.UPDATE_THRESHOLD_BOTH, rfdc.THRESHOLD_STICKY_UNDER, rfdc.THRESHOLD_STICKY_OVER, 8, 8, 1000, 1000, 12000, 12000)
    >>>>  {'UpdateThreshold': 4,
           'ThresholdMode0': 2,
           'ThresholdMode1': 1,
           'ThresholdAvgVal0': 8,
           'ThresholdAvgVal1': 8,
           'ThresholdUnderVal0': 1000,
           'ThresholdUnderVal1': 1000,
           'ThresholdOverVal0': 12000,
           'ThresholdOverVal1': 12000}
    """
    t = self.parent.transport

    args = (ntile, nblk)
    reply, informs = t.katcprequest(name='rfdc-get-adc-thresh', request_timeout=t._timeout, request_args=args)

    thresh_config = {}
    info = informs[0].arguments[0].decode().split(', ')
    if len(info) == 1: # (disabled) response
      return thresh_config

    for stat in info:
      k,v = stat.split(' ')
      thresh_config[k] = int(v)

    return thresh_config


  def set_adc_threshold(self, ntile, nblk, threshold_to_update, threshold0_mode, threshold1_mode, threshold0_avg_val, threshold1_avg_val,
          threshold0_under_val, threshold1_under_val, threshold0_over_val, threshold1_over_val):
    """
    Configure threshold settings for target ADC

    Threshold update constants are: UPDATE_THRESHOLD0 (1), UPDATE_THRESHOLD1 (2), UPDATE_THRESHOLD_BOTH (4)
    Threshold mode constants are: THRESHOLD_OFF (0), THRESHOLD_STICKY_UNDER (1), THRESHOLD_STICKY_OVER (2), THRESHOLD_HYSTERISIS (3)

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nlblk: int
    :param threshold_to_update: Update settings for Threshold0, Threshold1, or both simultaneously
    :type threshold_to_update: int
    :param threshold0_mode: The operating mode for Threshold0, {0 Off, sticky-over, sicky-under, hysteresis}
    :type threshold0_mode: int
    :param threshold1_mode: The operating mode for Threshold0, {Off, sticky-over, sicky-under, hysteresis}
    :type threshold1_mode: int
    :param threshold0_avg_val: Delay value before asserting Threshold0
    :type threshold0_avg_val: int
    :param thresdhol1_avg_val: Delay value before asserting Threshold1
    :type threshold1_avg_val: int
    :param threshold0_under_val: The under "lower" value to use on Threshold0
    :type threshold0_under_val: int
    :param threshold1_under_val: The under "lower" value to use on Threshold1
    :type threshold1_under_val: int
    :param threshold0_over_val: The over "upper" value to use on Threshold0
    :type threshold0_over_val: int
    :param threshold1_over_val: The over "upper" value to use on Threshold1
    :type threshold1_over_val: int

    :return: dict[str, int] of threshold settings, otherwise None if target converter is disabled

    Examples
    ---------
    >>>> rfdc.set_adc_thresh(0, 0, rfdc.UPDATE_THRESHOLD_BOTH, rfdc.THRESHOLD_STICKY_UNDER, rfdc.THRESHOLD_STICKY_OVER, 8, 8, 1000, 1000, 12000, 12000)
    >>>>  {'UpdateThreshold': 4,
           'ThresholdMode0': 2,
           'ThresholdMode1': 1,
           'ThresholdAvgVal0': 8,
           'ThresholdAvgVal1': 8,
           'ThresholdUnderVal0': 1000,
           'ThresholdUnderVal1': 1000,
           'ThresholdOverVal0': 12000,
           'ThresholdOverVal1': 12000}
    """
    t = self.parent.transport
    args = (ntile, nblk, threshold_to_update, threshold0_mode, threshold1_mode,
              threshold0_avg_val,   threshold1_avg_val,
              threshold0_under_val, threshold1_under_val,
              threshold0_over_val, threshold1_over_val)
    reply, informs = t.katcprequest(name='rfdc-set-adc-thresh', request_timeout=t._timeout, request_args=args)

    thresh_config = {}
    info = informs[0].arguments[0].decode().split(', ')
    if len(info) == 1: # (disabled) response
      return thresh_config

    for stat in info:
      k,v = stat.split(' ')
      thresh_config[k] = int(v)

    return thresh_config


  def set_thresh_clr_mode(self, ntile, nblk, threshold_to_update, clr_mode):
    """
    Configure threshold flag to be cleared manually with `thresh_sticky_clr` or with a QMC gain update for target ADC.
    The default mode for an enabled threshold is to be cleared manually. Thereshold clear modes are integer values:
    rfdc.THRESHOLD_CLR_MANUAL=1, rfdc.THRESHOLD_CLR_AUTO=2.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param threshold_to_update: Update settings for Threshold0, Threshold1, or both simultaneously
    :type threshold_to_update: int
    :param clr_mode: The clear mode to be used
    :type clr_mode: int

    :return: None

    Examples
    _________
    >>>> rfdc.set_thresh_clr_mode(0, 0, rfdc.UPDATE_THRESHOLD_BOTH, rfdc.THRESHOLD_CLR_MANUAL)
    """
    t = self.parent.transport
    args = (ntile, nblk, threshold_to_update, clr_mode)
    reply, informs = t.katcprequest(name='rfdc-set-thresh-clrmode', request_timeout=t._timeout, request_args=args)


  def thresh_sticky_clr(self, ntile, nblk, threshold_to_update):
    """
    Clears sticky threshold flags for target ADC with threshold flag clear mode `THRESHOLD_CLR_MANUAL`.

    :param ntile: Tile index of where target converter block is, in the range (0-3)
    :type ntile: int
    :param nblk: Block index within target converter tile, in the range (0-3)
    :type nblk: int
    :param threshold_to_update: Update settings for Threshold0, Threshold1, or both simultaneously
    :type threshold_to_update: int
    :param clr_mode: The clear mode to be used
    :type clr_mode: int

    :return: None

    Examples
    _________
    >>>> rfdc.thresh_sticky_clr(0, 0, rfdc.UPDATE_THRESHOLD_BOTH)
    """
    t = self.parent.transport
    args = (ntile, nblk, threshold_to_update)
    reply, informs = t.katcprequest(name='rfdc-thresh-stickyclr', request_timeout=t._timeout, request_args=args)

  def get_en_intr(self, ntile, nblk, converter_type):
    """
    """
    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-get-en-intr', request_timeout=t._timeout, request_args=args)

    enabled_intr = {}
    info = informs[0].arguments[0].decode().split(' ')
    print(info)
    if len(info) == 1: # (disabled) response
      return intr_status
    else:
      mask = int(info[1])

    return self.parse_interrupt_mask(mask)

  def set_en_intr(self, ntile, nblk, converter_type, interrupt_mask):
    """
    """
    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac", interrupt_mask)
    reply, informs = t.katcprequest(name='rfdc-set-en-intr', request_timeout=t._timeout, request_args=args)

    enabled_intr = {}
    info = informs[0].arguments[0].decode().split(' ')
    print(info)
    if len(info) == 1: # (disabled) response
      return intr_status
    else:
      mask = int(info[1])

    return self.parse_interrupt_mask(mask)

  def get_intr_status(self, ntile, nblk, converter_type):
    """
    """
    t = self.parent.transport

    args = (ntile, nblk, "adc" if converter_type == self.ADC_TILE  else "dac")
    reply, informs = t.katcprequest(name='rfdc-get-intr-status', request_timeout=t._timeout, request_args=args)

    intr_status = {}
    info = informs[0].arguments[0].decode().split(' ')
    print(info)
    if len(info) == 1: # (disabled) response
      return intr_status
    else:
      mask = int(info[1])

    return self.parse_interrupt_mask(mask)

