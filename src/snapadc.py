from __future__ import print_function
#from .__version__ import __version__
from .synth import LMX2581
from .adc import *
from .clockswitch import *
from .wishbonedevice import WishBoneDevice
import logging
import numpy as np
import time
import random
#print(__version__)

# There are so many I2C warnings that a new level is defined
# to filter them out
I2CWARNING = logging.INFO - 5
logging.addLevelName('I2CWARNING', I2CWARNING)

HIST_BINS = np.arange(-128, 128) # for histogram of ADC inputs
ERROR_VALUE = -1 # default value for status reports if comms are broken
ERROR_STRING = 'UNKNOWN' # default string for status reports if comms are broken

logger = logging.getLogger(__name__)

# Some codes and docstrings are copied from https://github.com/UCBerkeleySETI/snap_control
class SnapAdc(object):

    # Wishbone address and mask for read
    WB_DICT = [None] * ((0b11 << 2) + 1)

    WB_DICT[0b00 << 2] = {  'G_ZDOK_REV' : 0b11 << 28,
                'ADC16_LOCKED' : 0b11 << 24,
                'G_NUM_UNITS' : 0b1111 << 20,
                'CONTROLLER_REV' : 0b11 << 18,
                'G_ROACH2_REV' : 0b11 << 16,
                'ADC16_ADC3WIRE_REG' : 0b1111111111111111 << 0,}

    WB_DICT[0b01 << 2] = {'ADC16_CTRL_REG' : 0xffffffff << 0}
    WB_DICT[0b10 << 2] = {'ADC16_DELAY_STROBE_REG_H' : 0xffffffff << 0}
    WB_DICT[0b11 << 2] = {'ADC16_DELAY_STROBE_REG_L' : 0xffffffff << 0}

    # Wishbone address and mask for write
    A_WB_W_3WIRE = 0 << 0
    A_WB_W_CTRL = 1 << 0
    A_WB_W_DELAY_STROBE_L = 2 << 0
    A_WB_W_DELAY_STROBE_H = 3 << 0

    M_WB_W_DEMUX_WRITE      = 0b1 << 26
    M_WB_W_DEMUX_MODE       = 0b11 << 24
    M_WB_W_RESET            = 0b1 << 20
    M_WB_W_SNAP_REQ         = 0b1 << 16
    M_WB_W_DELAY_TAP        = 0b11111 << 0
    M_WB_W_ISERDES_BITSLIP_CHIP_SEL = 0b11111111 << 8
    M_WB_W_ISERDES_BITSLIP_LANE_SEL = 0b111 << 5

    SUCCESS = 0
    ERROR_LMX = 1
    ERROR_MMCM = 2
    ERROR_LINE = 3
    ERROR_FRAME = 4
    ERROR_RAMP = 5

    def __init__(self, host, device_name, device_info, initialize=False, **kwargs):
        """
        Instantiate an ADC block.

        Inputs:
           host (casperfpga.Casperfpga): Host FPGA
           num_chans (int): Number of channels per ADC chip. Valid values are 1, 2, or 4.
           resolution (int): Bit resolution of the ADC. Valid values are 8, 12.
           ref (float): Reference frequency (in MHz) from which ADC clock is derived. If None, an external sampling clock must be used.
        """
        self.logger = logger
        # Purposely setting ref=None below to prevent LMX object
        # from being attached so we can do it ourselves

        self.name = device_name
        self.device_info = device_info
        try:
            self.resolution  = int(self.device_info['adc_resolution'])
            self.sample_rate = float(self.device_info['sample_rate'])
            self.num_channel = int(self.device_info['snap_inputs'])
        except:
            print(self.device_info)
            raise
        # By default, the ref is 10MHz
        self.ref = 10
        # If the resolution is 8, we will use HMCAD1511;
        # if it's 12 or 14, we will use HMCAD1520.
        if self.resolution == 8:
            ADC = 'HMCAD1511'
        else:
            ADC = 'HMCAD1520'

        self.adc = None
        self.lmx = None
        self.clksw = None
        self.ram = None
        
        self.logger = kwargs.get('logger', logging.getLogger(__name__))

        # Current delay tap settings for all IDELAYE2
        self.curDelay = None
        
        # host => casperfpga.CasperFpga(hostname/ip)
        self.host = host

        self.A_WB_R_LIST = [self.WB_DICT.index(a) for a in self.WB_DICT if a != None]
        self.adcList = [0, 1, 2]
        self.ramList = ['adc16_wb_ram0', 'adc16_wb_ram1', 'adc16_wb_ram2']
        self.laneList = [0, 1, 2, 3, 4, 5, 6, 7]

        if self.resolution not in [8,12,14]:
            logger.error("Invalid resolution parameter")
            raise ValueError("Invalid resolution parameter")
        
        self.curDelay = [[0]*len(self.laneList)]*len(self.adcList)
        #self.curDelay = np.zeros((len(self.adcList),len(self.laneList)))

        #if ref is not None:
        #    self.lmx = LMX2581(host,'lmx_ctrl', fosc=ref)
        #else:
        #    self.lmx = None

        self.clksw = HMC922(host,'adc16_use_synth')
        self.ram = [WishBoneDevice(host,name) for name in self.ramList]
        ADC='HMCAD1511'
        if ADC not in ['HMCAD1511','HMCAD1520']:
            raise ValueError("Invalid parameter")

        if ADC == 'HMCAD1511':
            self.adc = HMCAD1511(host,'adc16_controller')
        else:   # 'HMCAD1520'
            self.adc = HMCAD1520(host,'adc16_controller')   

        # test pattern for clock aligning
        pats = [0b10101010,0b01010101,0b00000000,0b11111111]
        mask = (1 << (self.resolution // 2)) - 1
        ofst = self.resolution // 2
        self.p1 = ((pats[0] & mask) << ofst) + (pats[3] & mask)
        self.p2 = ((pats[1] & mask) << ofst) + (pats[2] & mask)

        # below is from hera_corr_f/blocks.py
        # Attach our own wrapping of LMX
        if(self.ref == None):
            self.lmx = LMX2581(host, 'lmx_ctrl', fosc=self.ref)
        self.name            = 'SNAP_adc'
        self.clock_divide    = 1
        #self.resolution      = resolution
        self.host = host # the SNAPADC class doesn't directly expose this
        self.working_taps = {}
        self._retry_cnt = 0
        #self._retry = kwargs.get('retry',7)
        self._retry = kwargs.get('retry',20)
        self._retry_wait = kwargs.get('retry_wait',1)

        if initialize:
            self.init(sample_rate=self.sample_rate, numChannel=self.num_channel)

    def set_gain(self, gain):
        """
        Set the coarse gain of the ADC. Allowed values
        are 1, 1.25, 2, 2.5, 4, 5, 8, 10, 12.5, 16, 20, 25, 32, 50.
        """
        gain_map = {
          1    : 0b0000,
          1.25 : 0b0001,
          2    : 0b0010,
          2.5  : 0b0011,
          4    : 0b0100,
          5    : 0b0101,
          8    : 0b0110,
          10   : 0b0111,
          12.5 : 0b1000,
          16   : 0b1001,
          20   : 0b1010,
          25   : 0b1011,
          32   : 0b1100,
          50   : 0b1101
        }

        if gain not in gain_map.keys():
            raise ValueError("Gain %f is not allowed! Only gains %s are allowed" % (gain, gain_map.keys()))

        self.adc.write((gain_map[gain]<<4) + gain_map[gain], 0x2b)

    # OVERWRITING casperfpga.snapadc.SNAPADC.init
    def init(self, sample_rate=500, numChannel=2, verify=False):
        """
        Get SNAP ADCs into working condition
        Supported frequency range: 60MHz ~ 1000MHz. Set resolution to
        None to let init() automatically decide the best resolution.

        1. configuring frequency synthesizer LMX2581
        2. configuring clock source switch HMC922
        3. configuring ADCs HMCAD1511 (support HMCAD1520 in future)
        4. configuring IDELAYE2 and ISERDESE2 inside of FPGA
        5. Testing under dual pattern and ramp mode

        E.g.
            init(1000,1)    1 channel mode, 1Gsps, 8bit, since 1Gsps
                    is only available in 8bit mode
            init(320,2) 2 channel mode, 320Msps, 8bit for HMCAD1511,
                    or 12bit for HMCAD1520
            init(160,4,8)   4 channel mode, 160Msps, 8bit resolution

        Inputs:
           sample_rate (float): Sample rate in MS/s. Default: 500.
        """

        # XXX verify currently not implemented
        self.numChannel = numChannel
        self.interleave_mode = 4 >> numChannel
        if self.lmx is not None:
            self.logger.debug("Reseting frequency synthesizer")
            self.lmx.init()
            self.logger.debug("Disabling Synth output A")
            self.lmx.setWord(1, "OUTA_PD")
            self.logger.debug("Configuring frequency synthesizer")
            assert(self.lmx.setFreq(sample_rate)) # Error if failed

        logger.info("Configuring clock source switch")
        if self.lmx is not None:
            self.clksw.setSwitch('a')
        else:
            self.clksw.setSwitch('b')

        self.logger.debug("Reseting adc_unit")
        self.reset()
        self.selectADC()
        self.logger.debug("Initialising ADCs")
        self.adc.init() # resets: don't set ADC registers before here

        # SNAP only uses one of the 3 ADC chips to provide clocks,
        # set others to lowest drive strength possible and terminate them
        self.selectADC([1,2]) # Talk to the 2nd and 3rd ADCs
        # Please refer to HMCAD1511 datasheet for more details
        # LCLK Termination
        rid, mask = self.adc._getMask('en_lvds_term')
        val = self.adc._set(0x0, 0b1, mask)          # Enable termination.
        rid, mask = self.adc._getMask('term_lclk')
        val = self.adc._set(val, 0b011, mask)        # 94 ohm
        # Frame CLK termination
        rid, mask = self.adc._getMask('term_frame')
        val = self.adc._set(val, 0b011, mask)        # 94 ohm
        self.adc.write(val, rid)
        # LCLK Drive Strength
        rid, mask = self.adc._getMask('ilvds_lclk')
        val = self.adc._set(0x0, 0b011, mask) # 0.5 mA (default)
        # Frame CLK Drive Strength
        rid, mask = self.adc._getMask('ilvds_frame')
        val = self.adc._set(val, 0b011, mask)        # 0.5 mA
        self.adc.write(val, rid)
        # Select all ADCs and continue initialization
        self.selectADC()

        if numChannel==1 and sample_rate<240:
            lowClkFreq = True
        elif numChannel==2 and sample_rate<120:
            lowClkFreq = True
        elif numChannel==4 and sample_rate<60:
            lowClkFreq = True
        # XXX this case already covered above
        #elif numChannel==4 and self.resolution==14 and sample_rate<30:
        #    lowClkFreq = True
        else:
            lowClkFreq = False

        self.logger.debug("Configuring ADC operating mode")
        if type(self.adc) is HMCAD1511:
            self.adc.setOperatingMode(numChannel, 1, lowClkFreq)
        elif type(self.adc) is HMCAD1520:
            self.adc.setOperatingMode(numChannel, 1, lowClkFreq,
                                      self.resolution)

        # ADC init/lmx select messes with FPGA clock, so reprogram
        self.logger.debug('Reprogramming the FPGA for ADCs')
        self.host.transport.prog_user_image()
        self.selectADC()
        self.logger.debug('Reprogrammed')

        # Select the clock source switch again. The reprogramming
        # seems to lose this information
        self.logger.debug('Configuring clock source switch')
        if self.lmx is not None:
            self.clksw.setSwitch('a')
        else:
            self.clksw.setSwitch('b')

        # Snipped off ADC calibration here; it's now in
        # snap_fengine.
        self._retry_cnt = 0
        self.working_taps = {} # initializing invalidates cached values
        return

    def selectADC(self, chipSel=None):
        """ Select one or multiple ADCs

        Select the ADC(s) to be configured. ADCs are numbered by 0, 1, 2...
        E.g. 
            selectADC(0)        # select the 1st ADC
            selectADC(1)        # select two ADCs
            selectADC(2)        # select all ADCs
        """

        # can activate low for HMCAD1511, but inverted in wb_adc16_controller
        if chipSel==None:       # Select all ADC chips
            self.adc.cs = np.bitwise_or.reduce([0b1 << s for s in self.adcList])
        elif isinstance(chipSel, list) and all(s in self.adcList for s in chipSel):
            csList = [0b1 << s for s in self.adcList if s in chipSel]
            self.adc.cs = np.bitwise_or.reduce(csList)
        elif chipSel in self.adcList:
            self.adc.cs = 0b1 << chipSel
        else:
            raise ValueError("Invalid Parameter")

    def setDemux(self, numChannel=1):
        """
        when mode==0: numChannel=4
            data = data[:,[0,4,1,5,2,6,3,7]]
        when mode==1: numChannel=2
            data = data[:,[0,1,4,5,2,3,6,7]]
        when mode==2: numChannel=1
            data = data[:,[0,1,2,3,4,5,6,7]]
        """
        modeMap = {4:0, 2:1, 1:2} # mapping of numChannel to mode
        if numChannel not in modeMap.keys():
            raise ValueError("Invalid Parameter")
        mode = modeMap[numChannel]
        val = self._set(0x0, mode, self.M_WB_W_DEMUX_MODE)
        val = self._set(val, 0b1,  self.M_WB_W_DEMUX_WRITE)
        self.adc._write(val, self.A_WB_W_CTRL)

    def reset(self):
        """ Reset all adc16_interface logics inside FPGA """
        val = self._set(0x0, 0x1, self.M_WB_W_RESET)
        self.adc._write(0x0, self.A_WB_W_CTRL)
        self.adc._write(val, self.A_WB_W_CTRL)
        self.adc._write(0x0, self.A_WB_W_CTRL)

    def snapshot(self):
        """ Save 1024 consecutive samples of each ADC into its corresponding bram """
        # No wat to snapshot a single ADC because the HDL code is designed so
        val = self._set(0x0, 0x1, self.M_WB_W_SNAP_REQ)
        self.adc._write(0x0, self.A_WB_W_CTRL)
        self.adc._write(val, self.A_WB_W_CTRL)
        self.adc._write(0x0, self.A_WB_W_CTRL)

    def calibrateAdcOffset(self):
        self.logger.warning('Operation not supported.')

    def calibrationAdcGain(self):
        self.logger.warning('Operation not supported.')

    def getRegister(self, rid=None):
        if rid==None:
            return [self.getRegister(regId) for regId in self.A_WB_R_LIST]
        elif rid in self.A_WB_R_LIST:
            rval = self.adc._read(rid)
            return {name: self._get(rval,mask) for name, mask in self.WB_DICT[rid].items()}
        else:
            raise ValueError("Invalid Parameter")

    def _get(self, data, mask):
        data = data & mask
        return data // (mask & -mask)

    def _set(self, d1, d2, mask=None):
        # Update some bis of d1 with d2, while keeping other bits unchanged 
        if mask:
            d1 = d1 & ~mask
            d2 = d2 * (mask & -mask)
        return d1 | d2 

    def getWord(self, name):
        rid = self.getRegId(name)
        rval = self.adc._read(rid)
        return self._get(rval, self.WB_DICT[rid][name])

    def getRegId(self, name):
        rid = [d for d in self.A_WB_R_LIST if name in self.WB_DICT[d]]
        if len(rid) == 0:
            raise ValueError("Invalid Parameter")
        else:
            return rid[0]

    def interleave(self, data, mode):
        """ Reorder the data according to the interleaving mode

        E.g.
            data = np.arange(1024).reshape(-1,8)
            interleave(data, 1) # return a one-column numpy array
            interleave(data, 2) # return a two-column numpy array
            interleave(data, 3) # return a four-column numpy array
        """
        return self.adc.interleave(data, mode)

    def readRAM(self, ram=None, signed=True):
        """ Read RAM(s) and return the 1024-sample data 

        E.g.
            readRAM()       # read all RAMs, return a list of arrays
            readRAM(1)      # read the 2nd RAMs, return a 128X8 aray
            readRAM([0,1])  # read 2 RAMs, return two arrays
            readRAM(signed=False)   # return a list of arrays in unsigned format
        """
        if ram==None:       # read all RAMS
            return self.readRAM(self.adcList, signed)
        elif isinstance(ram, list) and all(r in self.adcList for r in ram):
                            # read a list of RAMs
            data = [self.readRAM(r, signed) for r in ram if r in self.adcList]
            return dict(zip(ram, data))
        elif ram in self.adcList:
            if self.resolution > 8:     # ADC_DATA_WIDTH  == 16
                fmt = '!1024' + ('h' if signed else 'B')
                length = 2048
            else:
                fmt = '!1024' + ('b' if signed else 'B') 
                length = 1024
            vals = self.ram[ram]._read(addr=0, size=length)
            vals = np.array(struct.unpack(fmt,vals)).reshape(-1,8)
            return vals
        else:
            raise ValueError

    # A lane in this method actually corresponds to a "branch" in HMCAD1511 datasheet.
    # But I have to follow the naming convention of signals in casper repo.
    def bitslip(self, chipSel=None, laneSel=None, verify=False):
        """ Reorder the parallelize data for word-alignment purpose
        
        Reorder the parallelized data by asserting a itslip command to the bitslip 
        submodule of a ISERDES primitive.  Each bitslip command left shift the 
        parallelized data by one bit.

        HMCAD1511/HMCAD1520 lane correspondence
        lane number lane name in ADC datasheet
        0       1a
        1       1b
        2       2a
        3       2b
        4       3a
        5       3b
        6       4a
        7       4b

        E.g.
        .. code-block:: python
            bitslip()       # left shift all lanes of all ADCs
            bitslip(0)      # shift all lanes of the 1st ADC
            bitslip(0,3)        # shift the 4th lane of the 1st ADC
            bitslip([0,1],[3,4])    # shift the 4th and 5th lanes of the 1st
                        # and the 2nd ADCs
        """

        if chipSel == None:
            chipSel = self.adcList
        elif chipSel in self.adcList:
            chipSel = [chipSel]

        if not isinstance(chipSel,list):
            raise ValueError("Invalid parameter")
        elif isinstance(chipSel,list) and any(cs not in self.adcList for cs in chipSel):
            raise ValueError("Invalid parameter")

        if laneSel == None:
            laneSel = self.laneList
        elif laneSel in self.laneList:
            laneSel = [laneSel]

        if not isinstance(laneSel,list):
            raise ValueError("Invalid parameter")
        elif isinstance(laneSel,list) and any(cs not in self.laneList for cs in laneSel):
            raise ValueError("Invalid parameter")

        logger.debug('Bitslip lane {0} of chip {1}'.format(str(laneSel),str(chipSel)))

        for cs in chipSel:
            for ls in laneSel:
                val = self._set(0x0, 0b1 << cs, self.M_WB_W_ISERDES_BITSLIP_CHIP_SEL)
                val = self._set(val, ls, self.M_WB_W_ISERDES_BITSLIP_LANE_SEL)
        
                # The registers related to reset, request, bitslip, and other
                # commands after being set will not be automatically cleared.  
                # Therefore we have to clear them by ourselves.

                self.adc._write(0x0, self.A_WB_W_CTRL)
                if verify:
                    assert(self.adc._read(self.A_WB_W_CTRL) & self.M_WB_W_DELAY_TAP == 0x00)
                self.adc._write(val, self.A_WB_W_CTRL)
                if verify:
                    rv = self.adc._read(self.A_WB_W_CTRL)
                    assert(rv & self.M_WB_W_ISERDES_BITSLIP_CHIP_SEL == val & self.M_WB_W_ISERDES_BITSLIP_CHIP_SEL)
                    assert(rv & self.M_WB_W_ISERDES_BITSLIP_LANE_SEL == val & self.M_WB_W_ISERDES_BITSLIP_LANE_SEL)
                self.adc._write(0x0, self.A_WB_W_CTRL)
                if verify:
                    assert(self.adc._read(self.A_WB_W_CTRL) & self.M_WB_W_DELAY_TAP == 0x00)

    # The ADC16 controller word (the offset in write_int method) 2 and 3 are for delaying 
    # taps of A and B lanes, respectively.
    #
    # Refer to the memory map word 2 and word 3 for clarification.  The memory map was made 
    # for a ROACH design so it has chips A-H.  SNAP 1 design has three chips.
    def delay(self, tap, chipSel=None, laneSel=None, verify=False):
        """ Delay the serial data from ADC LVDS links
        
        Delay the serial data by Xilinx IDELAY primitives
        E.g.
            delay(0)        # Set all delay tap of IDELAY to 0
            delay(4, 1, 7)      # set delay on the 8th lane of the 2nd ADC to 4
            delay(31, [0,1,2], [0,1,2,3,4,5,6,7])
                        # Set all delay taps (in SNAP 1 case) to 31
        """

        if chipSel==None:
            chipSel = self.adcList
        elif chipSel in self.adcList:
            chipSel = [chipSel]
        elif isinstance(chipSel, list) and any(s not in self.adcList for s in chipSel):
            raise ValueError("Invalid parameter")

        if laneSel==None:
            laneSel = self.laneList
        elif laneSel in self.laneList:
            laneSel = [laneSel]
        elif isinstance(laneSel,list) and any(s not in self.laneList for s in laneSel):
            raise ValueError("Invalid parameter")
        elif laneSel not in self.laneList:
            raise ValueError("Invalid parameter")

        tap = int(tap) # Fix for Py3
 
        strl = ','.join([str(c) for c in laneSel])
        strc = ','.join([str(c) for c in chipSel])
        logger.debug('Set DelayTap of lane {0} of chip {1} to {2}'
                .format(str(laneSel),str(chipSel),tap))

        matc = np.array([(cs*4) for cs in chipSel])

        matla = np.array([int(l//2) for l in laneSel if l%2==0])
        if matla.size:
            mata =  np.repeat(matc.reshape(-1,1),matla.size,1) + \
                np.repeat(matla.reshape(1,-1),matc.size,0)
            vala = np.bitwise_or.reduce([0b1 << s for s in mata.flat])
        else:
            vala = 0
        
        matlb = np.array([int(l//2) for l in laneSel if l%2==1])
        if matlb.size:
            matb =  np.repeat(matc.reshape(-1,1),matlb.size,1) + \
                np.repeat(matlb.reshape(1,-1),matc.size,0)
            valb = np.bitwise_or.reduce([0b1 << s for s in matb.flat])
        else:
            valb = 0

        valt = self._set(0x0, tap, self.M_WB_W_DELAY_TAP)

        # Don't be misled by the naming - "DELAY_STROBE" in casper repo.  It doesn't 
        # generate strobe at all.  You have to manually clear the bits that you set.
        self.adc._write(0x00, self.A_WB_W_CTRL)
        self.adc._write(0x00, self.A_WB_W_DELAY_STROBE_L)
        self.adc._write(0x00, self.A_WB_W_DELAY_STROBE_H)
        if verify:
            assert(self.adc._read(self.A_WB_W_CTRL) & self.M_WB_W_DELAY_TAP == 0x00)
            assert(self.adc._read(self.A_WB_W_DELAY_STROBE_L) == 0x00)
            assert(self.adc._read(self.A_WB_W_DELAY_STROBE_H) == 0x00)
        self.adc._write(valt, self.A_WB_W_CTRL)
        self.adc._write(vala, self.A_WB_W_DELAY_STROBE_L)
        self.adc._write(valb, self.A_WB_W_DELAY_STROBE_H)
        if verify:
            assert(self.adc._read(self.A_WB_W_CTRL) & self.M_WB_W_DELAY_TAP == valt)
            assert(self.adc._read(self.A_WB_W_DELAY_STROBE_L) == vala)
            assert(self.adc._read(self.A_WB_W_DELAY_STROBE_H) == valb)
        self.adc._write(0x00, self.A_WB_W_CTRL)
        self.adc._write(0x00, self.A_WB_W_DELAY_STROBE_L)
        self.adc._write(0x00, self.A_WB_W_DELAY_STROBE_H)
        if verify:
            assert(self.adc._read(self.A_WB_W_CTRL) & self.M_WB_W_DELAY_TAP == 0x00)
            assert(self.adc._read(self.A_WB_W_DELAY_STROBE_L) == 0x00)
            assert(self.adc._read(self.A_WB_W_DELAY_STROBE_H) == 0x00)

        for cs in chipSel:
            for ls in laneSel:
                self.curDelay[cs][ls] = tap

    def test_patterns(self, chipSel=None, taps=None, mode='std', pattern1=None, pattern2=None):
        """ Return a list of std/err for a given tap or a list of taps

        Return the lane-wise standard deviation/error of the data under a given
        tap setting or a list of tap settings.  By default, mode='std', taps=range(32).
        'err' mode with single test pattern check data against the given pattern, while
        'err' mode with dual test patterns guess the counts of the mismatches.
        'guess' because both patterns could come up at first. This method
        always returns the smaller counts.
        'ramp' mode guess the total number of incorrect data. This is implemented based
        on the assumption that in most cases, $cur = $pre + 1. When using 'ramp' mode,
        taps=None

        E.g.
        .. code-block:: python
            testPatterns(taps=True) # Return lane-wise std of all ADCs, taps=range(32)
            testPatterns(0,taps=range(32))
                        # Return lane-wise std of the 1st ADC
            testPatterns([0,1],2)   # Return lane-wise std of the first two ADCs 
                        # with tap = 2
            testPatterns(1, taps=[0,2,3], mode='std')
                        # Return lane-wise stds of the 2nd ADC with
                        # three different tap settings
            testPatterns(2, mode='err', pattern1=0b10101010)
                        # Check the actual data against the given test
                        # pattern without changing current delay tap
                        # setting and return lane-wise error counts of
                        # the 3rd ADC,
            testPatterns(2, mode='err', pattern1=0b10101010, pattern2=0b01010101)
                        # Check the actual data against the given alternate
                        # test pattern without changing current delay tap
                        # setting and return lane-wise error counts of
                        # the 3rd ADC,
            testPatterns(mode='ramp')
                        # Check all ADCs under ramp mode

        """

        # The deviation looks like this:
        # {0: array([ 17.18963055,  17.18963055,  17.18963055,  17.18963055,
        #          13.05692914,  13.05692914,  13.05692914,  13.05692914]),
        #  1: array([ 14.08136755,  14.08136755,  14.08136755,  14.08136755,
        #           7.39798788,   7.39798788,   7.39798788,   7.39798788]),
        #  2: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  3: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  4: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  5: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  6: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  7: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  8: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  9: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  10: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  11: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  12: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  13: array([ 6.47320197,  7.39906033,  0.        ,  0.        ,  0.        ,
        #          0.        ,  0.        ,  0.        ]),
        #  14: array([ 16.14016176,  16.63835629,   4.66368953,   1.40867846,
        #          75.989443  ,  69.36052029,   6.92820323,   1.40867846]),
        #  15: array([ 49.30397384,  38.93917329,   7.96476616,   7.43487349,
        #          49.32813242,  49.89897448,  24.89428699,  17.29472061]),
        #  16: array([ 45.83336145,  44.495608  ,  25.20036771,  52.85748304,
        #           5.63471383,   0.        ,  45.64965087,  40.04722554]),
        #  17: array([  0.        ,   0.        ,  57.13889616,  24.2960146 ,
        #           0.        ,   0.        ,  65.0524836 ,  30.79302499]),
        #  18: array([  0.        ,   0.        ,   0.        ,   0.        ,
        #           0.        ,   0.        ,  59.11012465,   0.        ]),
        #  19: array([  0.        ,   0.        ,   0.        ,   0.        ,
        #           0.        ,   0.        ,  24.79919354,   0.        ]),
        #  20: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  21: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  22: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  23: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  24: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  25: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  26: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  27: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  28: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  29: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  30: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.]),
        #  31: array([ 0.,  0.,  0.,  0.,  0.,  0.,  0.,  0.])}
    
        MODE = ['std', 'err', 'ramp']

        if chipSel==None:
            chipSel = self.adcList
        elif chipSel in self.adcList:
            chipSel = [chipSel]
        if not isinstance(chipSel,list):
            raise ValueError("Invalid parameter")
        elif isinstance(chipSel,list) and any(cs not in self.adcList for cs in chipSel):
            raise ValueError("Invalid parameter")

        if taps==True:
            taps = list(range(32))
        elif taps in self.adcList:
            taps = [taps]
        if not isinstance(taps,list) and taps!=None:
            raise ValueError("Invalid parameter")
        elif isinstance(taps,list) and any(cs not in range(32) for cs in taps):
            raise ValueError("Invalid parameter")

        if mode not in MODE:
            raise ValueError("Invalid parameter")

        self.selectADC(chipSel)
        if mode=='ramp':        # ramp mode
            self.adc.test('en_ramp')
            taps=None
            pattern1=None
            pattern2=None
        elif pattern1==None and pattern2==None:
            # synchronization mode
            self.adc.test('pat_sync')
            # pattern1 = 0b11110000 when self.resolution is 8
            # pattern1 = 0b111111000000 when self.resolution is 12
            pattern1 = ((2 ** (self.resolution // 2)) - 1) << (self.resolution // 2)
            pattern1 = self._signed(pattern1, self.resolution)
        elif isinstance(pattern1,int) and pattern2==None:
            # single pattern mode

            if type(self.adc) is HMCAD1520:
                # test patterns of HMCAD1520 need special cares
                ofst = 16 - self.resolution
                reg_p1 = pattern1 << ofst
            else:
                reg_p1 = pattern1

            self.adc.test('single_custom_pat', reg_p1)
            pattern1 = self._signed(pattern1, self.resolution)
        elif isinstance(pattern1,int) and isinstance(pattern2,int):
            # dual pattern mode

            if type(self.adc) is HMCAD1520:
                # test patterns of HMCAD1520 need special cares
                ofst = 16 - self.resolution
                reg_p1 = pattern1 << ofst
                reg_p2 = pattern2 << ofst
            else:
                reg_p1 = pattern1
                reg_p2 = pattern2

            self.adc.test('dual_custom_pat', reg_p1, reg_p2)
            pattern1 = self._signed(pattern1, self.resolution)
            pattern2 = self._signed(pattern2, self.resolution)
        else: 
            raise ValueError("Invalid parameter")

        results = []

        def _check(data):
            d = np.array(data).reshape(-1, 8)
            if mode=='std' and pattern2==None:  # std mode, single pattern
                r = np.std(d,0)
            elif mode=='std' and pattern2!=None:    # std mode, dual patterns
                counts = [np.unique(d[:,i],return_counts=True)[1] for i in range(d.shape[1])]
                r = [np.sum(np.sort(count)[::-1][2:]) for count in counts]
            elif mode=='err' and pattern2==None:    # err mode, single pattern
                r = np.sum(d!=pattern1, 0)
            elif mode=='err' and pattern2!=None:    # err mode, dual pattern
                # Try two patterns with different order, and return the
                # result
                m1,m2 = np.zeros(d.shape),np.zeros(d.shape)
                m1[0::2,:],m1[1::2,:]=pattern1,pattern2
                m2[0::2,:],m2[1::2,:]=pattern2,pattern1
                r=np.minimum(np.sum(d!=m1,0),np.sum(d!=m2,0))
            elif mode=='ramp':          # ramp mode
                diff = d[1:,:]-d[:-1,:]
                counts=[np.unique(diff[:,ls],return_counts=True)[1] for ls in self.laneList]
                r = [d.shape[0]-1-sum(counts[ls][:2]) for ls in self.laneList]
            return r

        if taps == None:
            self.snapshot()
            results = [_check(self.readRAM(cs)) for cs in chipSel]
            results = np.array(results).reshape(len(chipSel),len(self.laneList)).tolist()
            results = dict(zip(chipSel,results))
            for cs in chipSel:
                results[cs] = dict(zip(self.laneList,results[cs]))
        else:
            for tap in taps:
                self.delay(tap, chipSel)
                self.snapshot()
                results += [_check(self.readRAM(cs)) for cs in chipSel]
            results = np.array(results).reshape(-1,len(chipSel),len(self.laneList))
            results = np.einsum('ijk->jik',results).tolist()
            results = dict(zip(chipSel,results))
            for cs in chipSel:
                results[cs] = dict(zip(taps,[np.array(row) for row in results[cs]]))
        
        self.adc.test('off')

        if len(chipSel) == 1:
            return results[chipSel[0]]
        else:
            return results

    def _signed(self, data, res=8):
        """ Convert unsigned number to signed number

        adc16_interface converts ADC outputs into signed numbers by flipping MSB.
        Therefore we have to prepare signed-number test patterns as well.
        E.g.
            _signed(0xc0,res=8) convert 8-bit unsigned to 8-bit signed
            _signed(0xc10,res=12)   convert 12-bit unsigned to 16-bit signed
        """

        if res<=8:
            width = 8
        elif res<=16:
            width = 16
        elif res<=32:
            width = 32
        else:
            raise ValueError("Invalid parameter")

        data = data & (1 << res) - 1
        msb = data & (1 << res-1)
        if msb:
            return data ^ msb
        else:
            offset = (1<<width)-(1<<res-1)
            data = data + offset
            if width == 8:
                data = struct.pack('!B',data)
                data = struct.unpack('!b',data)
            elif width == 16:
                data = struct.pack('!H',data)
                data = struct.unpack('!h',data)
            else:   # width == 32
                data = struct.pack('!I',data)
                data = struct.unpack('!i',data)
            return data[0]
        

    def decide_delay(self, data):
        """ Decide and return proper setting for delay tap

        Find the tap setting that has the largest margin of error, i.e. the biggest distance
        to borders (tap=0 and tap=31) and rows with non-zero deviations/mismatches.  The
        parameter data is a 32 by n numpy array, in which the 1st dimension index indicates
        the delay tap setting
        """

        if not isinstance(data,np.ndarray):
            raise ValueError("Invalid parameter")
        elif data.ndim==1:
            data = data.reshape(-1,1)
        elif data.ndim>2:
            raise ValueError("Invalid parameter")

        data = np.sum(data,1)

        if all(d != 0 for d in data):
            return False
            
        dist=np.zeros(data.shape, dtype=int)
        curDist = 0
        for i in range(data.size):
            if data[i] != 0:
                curDist = 0
            else:
                curDist += 1
            dist[i] = curDist
        curDist = 0
        for i in list(reversed(range(data.size))):
            if data[i] != 0:
                curDist = 0
            else:
                curDist += 1
            if dist[i] > curDist:
                dist[i] = curDist

        return np.argmax(dist)

    def _find_working_taps(self, ker_size=5, maxtries=5):
        '''Generate a dictionary of working tap values per chip/lane.
        ker: size of convolutional kernel used as a stand-off from
             marginal tap values. Default 7.'''
        nchips, nlanes, ntaps = len(self.adcList), len(self.laneList), 32
        # Make sure we have enough taps to work with, otherwise reinit ADC
        for cnt in range(maxtries):
            try:
                for chip in self.adcList:
                    assert(self.working_taps[chip].size > 0)
                return
            except(KeyError, AssertionError):
                self.logger.info('Not enough working taps. Reinitializing.')
                if len(self.working_taps) > 0:
                    self.init()
                self.working_taps = {}
                h = np.zeros((nchips, ntaps), dtype=int)
                self.setDemux(numChannel=1)
                self.selectADC() # select all chips
                self.adc.test('pat_deskew')
                for t in range(ntaps):
                    for L in self.laneList:
                        for chip in self.adcList:
                            self.delay(t, chip, L)
                    self.snapshot()
                    for chip,d in self.readRAM(signed=False).items():
                        h[chip,t] = (np.sum(d == d[:1], axis=(0,1)) == d.shape[0] * d.shape[1])
                self.selectADC() # select all chips
                self.adc.test('off')
                self.setDemux(numChannel=self.numChannel)
                ker = np.ones(ker_size)
                for chip in self.adcList:
                    # identify taps that work for all lanes of chip
                    taps = np.where(np.convolve(h[chip], ker, 'same') == ker_size)[0]
                    self.working_taps[chip] = taps
        raise RuntimeError('Failed to find working taps.')

    def alignLineClock(self, chips_lanes=None, ker_size=5):
        """Find a tap for the line clock that produces reliable bit
        capture from ADC."""
        if chips_lanes is None:
            chips_lanes = {chip:self.laneList for chip in self.adcList}
        self.logger.info('Aligning line clock on ADCs/lanes: %s' % \
                          str(chips_lanes))
        try:
            self._find_working_taps(ker_size=ker_size)
        except(RuntimeError):
            self.logger.info('Failed to find working taps.')
            return chips_lanes # total failure
        self.setDemux(numChannel=1)
        for chip, lanes in chips_lanes.items():
            self.selectADC(chip)
            taps = self.working_taps[chip]
            tap = random.choice(taps)
            for L in self.laneList: # redo all lanes to be the same
                self.delay(tap, chip, L)
            # Remove from future consideration if tap doesn't work out
            self.working_taps[chip] = taps[np.abs(taps - tap) >= ker_size//2]
            self.logger.info('Setting ADC=%d tap=%s' % (chip, tap))
        self.setDemux(numChannel=self.numChannel)
        return {} # success

    def isLineClockAligned(self):
        errs = self.testPatterns(mode='std',pattern1=self.p1,pattern2=self.p2)

        if np.all(np.array([list(adc.values()) for adc in errs.values()])==0):
            logger.info('Line clock of all ADCs aligned.')
            return True
        else:
            logger.error('Line clock NOT aligned.\n{0}'.format(str(errs)))
            return False

    def alignFrameClock(self, chips_lanes=None, retry=True):
        """Align frame clock with data frame."""
        if chips_lanes is None:
            chips_lanes = {chip:self.laneList for chip in self.adcList}
        self.logger.debug('Aligning frame clock on ADCs/lanes: %s' % \
                          str(chips_lanes))
        failed_chips = {}
        self.setDemux(numChannel=1)
        for chip, lanes in chips_lanes.items():
            self.selectADC(chip)
            self.adc.test('dual_custom_pat', self.p1, self.p2)
            ans1 = self._signed(self.p1, self.resolution)
            ans2 = self._signed(self.p2, self.resolution)
            failed_lanes = []
            for cnt in range(2 * self.resolution):
                slipped = False
                self.snapshot() # make bitslip "take" (?!) XXX
                d = self.readRAM(chip).reshape(-1, self.resolution)
                # sanity check: these failures mean line clock errors
                failed_lanes += [L for L in lanes
                        if np.any(d[0::2,L] != d[0,L]) or \
                           np.any(d[1::2,L] != d[1,L])]
                lanes = [L for L in lanes if L not in failed_lanes]
                for lane in lanes:
                    if not d[0,lane] in [ans1, ans2]:
                        if cnt == 2*self.resolution - 1:
                            # Failed on last try
                            failed_lanes += [lane]
                        self.bitslip(chip, lane)
                        slipped = True
                if not slipped:
                    break
            self.adc.test('off')
            if len(failed_lanes) > 0:
                failed_chips[chip] = failed_lanes
        self.setDemux(numChannel=self.numChannel)
        if len(failed_chips) > 0 and retry:
            if self._retry_cnt < self._retry:
                self._retry_cnt += 1
                self.logger.info('retry=%d/%d redo Line on ADCs/lanes: %s' % \
                            (self._retry_cnt, self._retry, failed_chips))
                self.alignLineClock(failed_chips)
                return self.alignFrameClock(failed_chips)
        return failed_chips

    def isFrameClockAligned(self):
        errs = self.testPatterns(mode='err',pattern1=self.p1,pattern2=self.p2)

        if all(all(val==0 for val in adc.values()) for adc in errs.values()):
            logger.info('Frame clock of all ADCs aligned.')
            return True
        else:
            logger.error('Frame clock NOT aligned.\n{0}'.format(str(errs)))
            return False

    def rampTest(self, chips=None, nchecks=300, retry=False):
        if chips is None:
            chips = self.adcList
        self.logger.debug('Ramp test on ADCs: %s' % str(chips))
        failed_chips = {}
        self.setDemux(numChannel=1)
        predicted = np.arange(128).reshape(-1,1)
        self.selectADC() # select all chips
        self.adc.test("en_ramp")
        for cnt in range(nchecks):
            self.snapshot()
            for chip,d in self.readRAM(chips, signed=False).items():
                ans = (predicted + d[0,0]) % 256
                failed_lanes = np.sum(d != ans, axis=0)
                if np.any(failed_lanes) > 0:
                    failed_chips[chip] = np.where(failed_lanes)[0]
            if (retry is False) and len(failed_chips) > 0:
                # can bail out if we aren't retrying b/c we don't need list of failures.
                break
        self.selectADC() # select all chips
        self.adc.test('off')
        self.setDemux(numChannel=self.numChannel)
        if len(failed_chips) > 0 and retry:
            if self._retry_cnt < self._retry:
                self._retry_cnt += 1
                self.logger.info('retry=%d/%d redo Line/Frame on ADCs/lanes: %s' % \
                            (self._retry_cnt, self._retry, failed_chips))
                self.alignLineClock(failed_chips)
                self.alignFrameClock(failed_chips)
                return self.rampTest(nchecks=nchecks, retry=retry)
        return failed_chips

    def isLaneBonded(self, bondAllAdcs=False):
        """
        Using ramp test mode, check that all lanes are aligned.
        I.e., snap some data, and check that all lanes' counters are
        in sync.
        inputs:
            bondAllAdcs (bool): If True, require all chips to be synchronized.
                                If False, only require lanes within a chip to
                                be mutually synchronized.
        returns: True is aligned, False otherwise
        """
        self.adc.test("en_ramp")
        self.snapshot()
        d = self.readRAM(signed=False)
        ok = True
        for adc in self.adcList:
            if bondAllAdcs:
                ok = ok and (np.all(d[adc][0] == d[self.adcList[0]][0][0]))
            else:
                ok = ok and (np.all(d[adc][0] == d[adc][0][0]))
        self.adc.test("off")
        return ok

    @classmethod
    def from_device_info(cls, parent, device_name, device_info, initialize=False, **kwargs):
        """
        Process device info and the memory map to get all the necessary info
        and return a ADC instance.
        :param parent: The parent device, normally a casperfpga instance
        :param device_name:
        :param device_info:
        :param memorymap_dict:
        :param initialize:
        :param kwargs:
        :return:
        """
        return cls(parent, device_name, device_info, initialize, **kwargs)
