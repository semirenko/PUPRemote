# LPF2 class allows communication between LEGO SPIKE Prime and third party devices.
# Based on code from Tufts University

import machine
import math, struct
from utime import ticks_ms
import utime
import binascii

MAX_PKT = 32

BYTE_NACK = 0x02
BYTE_ACK = 0x04
CMD_Type = 0x40   # @, set sensor type command
CMD_Select = 0x43   #  C, sets modes on the fly
CMD_Mode = 0x49   # I, set mode type command
CMD_Baud = 0x52   # R, set the transmission baud rate
CMD_Vers = 0x5F   # _,  set the version number
CMD_ModeInfo = 0x80  # name command
CMD_Data  = 0xC0  # data command
CMD_EXT_MODE = 0x6
EXT_MODE_0 = 0x00
EXT_MODE_8 = 0x08 # only used for extended mode > 7
CMD_LLL_SHIFT = 3

LENGTH_1 = 0x00

NAME,RAW,Pct,SI,SYM,FCT, FMT = 0x0,0x1,0x2,0x3,0x4,0x5, 0x80
DATA8,DATA16,DATA32,DATAF = 0,1,2,3  # Data type codes
ABSOLUTE,RELATIVE,DISCRETE = 16,8,4
WeDo_Ultrasonic, SPIKE_Color, SPIKE_Ultrasonic = 35, 61, 62
Ev3_Utrasonic = 34

length = [1,2,4]
format = ['B', 'H', 'I', 'f']

HEARTBEAT_PERIOD=200 # time of inactivity after which we reset sensor

# Name, Format [# datasets, data_type, figures, decimals],
# raw [min,max], Percent [min,max], SI [min,max], Symbol, functionMap [type, ?], view
#mode0 = ['LPF2-DETECT',[1,DATA8,3,0],[0,10],[0,100],[0,10],'',[ABSOLUTE,0],True]
#mode1 = ['LPF2-COUNT',[1,DATA32,4,0],[0,100],[0,100],[0,100],'CNT',[ABSOLUTE,0],True]
#mode2 = ['LPF2-CAL',[3,DATA16,3,0],[0,1023],[0,100],[0,1023],'RAW',[ABSOLUTE,0],False]
#defaultModes = [mode0,mode1,mode2]

def bit_length(x):
    n=0
    while (x>0):
        x>>=1
        n+=1
    return n

def default_cmd_callback(size,buf):
         print("received command")
         print("size=",size)
         print("len=",len(buf))
         print("data=",binascii.hexlify(buf))

class LPF2(object):

     def __init__(self, modes , sensor_id = WeDo_Ultrasonic, timer = 4, freq = 5):
          self.txTimer = timer
          self.modes = modes
          self.current_mode = 0
          self.sensor_id = sensor_id
          self.connected = False
          self.payload = bytearray([])
          self.freq = freq
          self.oldbuffer =  bytes([])
          self.textBuffer = bytearray(b'\x00'*32)
          self.cmd_call_back=default_cmd_callback
          self.last_nack= 0

     @staticmethod
     def mode(name,size = 1, data_type=DATA8, writable=0,format = '3.0',  raw = [0,100], percent = [0,100],  SI = [0,100], symbol = '', functionmap = [ABSOLUTE,ABSOLUTE], view = True):
          fig,dec = format.split('.')
          functionmap=[ABSOLUTE,writable]
          fred = [name, [size,data_type,int(fig),int(dec)],raw,percent,SI,symbol,functionmap,view]
          return fred

     def write_tx_pin(self, value, sleep=500):
          tx = machine.Pin(self.tx_pin_nr, machine.Pin.OUT)
          tx.value(value)
          utime.sleep_ms(sleep)

     def fast_uart(self):
          self.uart=machine.UART(self.uartchannel,baudrate=115200,rx=self.rx_pin_nr,tx=self.tx_pin_nr)

     def slow_uart(self):
          self.uart = machine.UART(self.uartChannel,baudrate=2400,rx=self.rxPin,tx=self.txPin,timeout=5)

# define own call back
     def set_call_back(self,cb):
         self.cmd_call_back=cb

# -------- Payload definition

     def load_payload(self,data_type, data):   
         if isinstance(data,list):
              bin_data=struct.pack('%d'%len(data)+format[data_type],*data)
         else:
             bin_data=bytes(data)
         bin_data=bin_data[:MAX_PKT]
         bit=bit_length(len(bin_data)-1)
         bin_data=bin_data+b'\x00'*(2**bit-len(bin_data))
         payload = bytearray([CMD_Data | (bit << CMD_LLL_SHIFT) | self.current_mode])+bin_data
         self.payload = self.addChksm(payload)

     def send_payload(self,data_type,array):
         self.load_payload(data_type,array)
         self.writeIt(self.payload,debug=False)

#----- comm stuff

     def readchar(self,debug=False):
          c=self.uart.read(1)
          cbyte=ord(c) if c else -1
          if debug:
              print("c= 0x%02X"%cbyte)
          return cbyte

     def heartbeat(self):
         if (ticks_ms() - self.last_nack) > HEARTBEAT_PERIOD:
             self.last_nack = ticks_ms()
             self.initialize()
         b = self.readchar()     # read in any heartbeat bytes
         if b>=0:  # keep reading next character
             if b == 0:   # port has nto been setup yet
                 pass
             elif b == BYTE_NACK:     # regular heartbeat pulse
                 # EXT_MODE_0 = 00
                 # EXT_MODE
                 payl=bytearray([CMD_Type|LENGTH_1|CMD_EXT_MODE,0x00])
                 payl=self.addChksm(payl)
                 self.writeIt(payl,debug=False)
                 self.writeIt(self.payload)
                 #print("n=",ticks_ms()-self.last_nack)
                 self.last_nack=ticks_ms()
             else:
               if  b == CMD_Select:    # reset the mode
                 mode = self.readchar()
                 cksm = self.readchar()
                 if cksm == 0xff ^ CMD_Select ^ mode:
                     self.current_mode = mode
                     #print("change mode=",mode)
               elif b == 0x46:     # data from hub to sensor read 46 00 c9
                    #print("cmd recv")
                    zero = self.readchar()
                    b9 = self.readchar()
                    ck = 0xff ^ zero ^ b9
                    #print("zero=%02X,b9=%02x,ck=%02X"%(zero,b9,ck))
                    if ((zero == 0) & (b9 == 0xb9)):
                         # DATA written from the hub start with 46 00 c9, followed by
                         # [CMD_DATA|LENGTH|MODE,[data bytes,]]
                         ck=0xff # reset checksum for command
                         b = self.readchar()    # size and mode
                         size = 2**((b & 0b111000)>>3)
                         #print("size=",size)
                         mode = b & 0b111
                         self.current_mode = mode
                         ck = ck ^ b
                         #print("char=%02x,ck=%02x"%(char,ck))
                         self.textBuffer = bytearray(b'\x00'*size)
                         for i in range(size):
                              self.textBuffer[i] = self.readchar()
                              ck = ck ^ self.textBuffer[i]
                              #print("textbuf=%02X,ck=%02X"%(self.textBuffer[i],ck))
                         #print(self.textBuffer)
                         #print("cmd=%02X"%char)
                         cksm = self.readchar()
                         #print("cksm=%02X, ck=%02X"%(cksm,ck))
                         if cksm == ck:
                              if (b & CMD_Data == CMD_Data):
                                   #print("calling cb")
                                   self.cmd_call_back(size,self.textBuffer)
                                   
         
               

     def writeIt(self,array,debug=False):
          if debug:
              print(binascii.hexlify(array))
          return self.uart.write(array)

     def waitFor (self, char, timeout = 2):
          starttime = utime.time()
          currenttime = starttime
          status = False
          while (currenttime-starttime) < timeout:
               utime.sleep_ms(5)
               currenttime = utime.time()
               if self.uart.any() > 0:
                    data = self.uart.read(1)
                    #print("received",data)
                    if  data == char:
                         status = True
                         break
          return status

     def addChksm(self,array):
          chksm = 0
          for b in array:
               chksm ^= b
          chksm ^= 0xFF
          array.append(chksm)
          return array

# -----  Init and close

     def init(self):
          self.write_tx_pin(0, 500)
          self.write_tx_pin(1, 0)
          self.slow_uart()
          self.writeIt(b'\x00')

     def close(self):
          #self.uart.deinit()
          self.send_timer.deinit()
          

# ---- settup definitions

     def setType(self,sensorType):
          return self.addChksm(bytearray([CMD_Type, sensorType]))

     def defineBaud(self,baud):
          rate = baud.to_bytes(4, 'little')
          return self.addChksm(bytearray([CMD_Baud]) + rate)

     def defineVers(self,hardware,software):
          hard = hardware.to_bytes(4, 'big')
          soft = software.to_bytes(4, 'big')
          return self.addChksm(bytearray([CMD_Vers]) + hard + soft)

     def padString(self,string, num, startNum):
          reply = bytearray(string)
          reply = reply[:MAX_PKT]
          exp=bit_length(len(reply)-1)
          reply = reply + b'\x00'*(2**exp - len(string))
          exp = exp<<3
          return self.addChksm(bytearray([CMD_ModeInfo | exp | num, startNum]) + reply)

     def buildFunctMap(self,mode, num, Type):
          exp = 1 << CMD_LLL_SHIFT
          mapType = mode[0]
          mapOut = mode[1]
          return self.addChksm(bytearray([CMD_ModeInfo | exp | num, Type, mapType, mapOut]))

     def buildFormat(self,mode, num, Type):
          exp = 2 << CMD_LLL_SHIFT
          sampleSize = mode[0] & 0xFF
          dataType = mode[1] & 0xFF
          figures = mode[2] & 0xFF
          decimals = mode[3] & 0xFF
          return self.addChksm(bytearray([CMD_ModeInfo | exp | num, Type, sampleSize, dataType,figures,decimals]))

     def buildRange(self,settings, num, rangeType):
          exp = 3 << CMD_LLL_SHIFT
          minVal = struct.pack('<f', settings[0])
          maxVal = struct.pack('<f', settings[1])
          return self.addChksm(bytearray([CMD_ModeInfo | exp | num, rangeType]) + minVal + maxVal)

     def defineModes(self,modes):
          length = (len(modes)-1) & 0xFF
          views = 0
          for i in modes:
               if (i[7]):
                    views = views + 1
          views = (views - 1) & 0xFF
          return self.addChksm(bytearray([CMD_Mode, length, views]))

     def setupMode(self,mode,num):
          self.writeIt(self.padString(mode[0],num,NAME))        # write name
          self.writeIt(self.buildRange(mode[2], num, RAW))      # write RAW range
          self.writeIt(self.buildRange(mode[3], num, Pct))        # write Percent range
          self.writeIt(self.buildRange(mode[4], num, SI))          # write SI range
          self.writeIt(self.padString(mode[5],num,SYM))          # write symbol
          self.writeIt(self.buildFunctMap(mode[6],num, FCT)) # write Function Map
          self.writeIt(self.buildFormat(mode[1],num, FMT))     # write format

# -----   Start everything up

     def initialize(self):
          self.connected = False
          #self.send_timer = machine.Timer(-1)  # default is 200 ms
          #self.period=int(1000/self.freq)
          self.init()
          self.writeIt(self.setType(self.sensor_id))  # set sensor_id to 35 (WeDo Ultrasonic) 61 (Spike color), 62 (Spike ultrasonic)
          self.writeIt(self.defineModes(self.modes))  # tell how many modes
          self.writeIt(self.defineBaud(115200))
          self.writeIt(self.defineVers(2,2))
          num = len(self.modes) - 1
          for mode in reversed(self.modes):
               self.setupMode(mode,num)
               num -= 1
               utime.sleep_ms(5)

          self.writeIt(b'\x04')  #ACK
          # Check for ACK reply
          self.connected = self.waitFor(b'\x04')
          print('Success' if self.connected else 'Failed')
          self.last_nack=ticks_ms()

          # Reset Serial to High Speed
          if self.connected:
               self.write_tx_pin(0, 10)

               # Change baudrate
               self.fast_uart()
               self.load_payload(DATA8,0)

               #start callback  - MAKE SURE YOU RESTART THE CHIP EVERY TIME (CMD D) to kill previous callbacks running
               #self.send_timer.init(period=self.period, mode=machine.Timer.PERIODIC, callback= self.hubCallback)
          return

class ESP_LPF2(LPF2):
     tx_pin_nr = 19
     rx_pin_nr = 18
     uartchannel = 2

     def write_tx_pin(self, value, sleep=500):
          tx = machine.Pin(self.tx_pin_nr, machine.Pin.OUT)
          tx.value(value)
          utime.sleep_ms(sleep)

     def slow_uart(self):
          self.uart=machine.UART(self.uartchannel,baudrate=2400,rx=self.rx_pin_nr,tx=self.tx_pin_nr)

     def fast_uart(self):
          self.uart=machine.UART(self.uartchannel,baudrate=115200,rx=self.rx_pin_nr,tx=self.tx_pin_nr)

class Prime_LPF2(LPF2):
     def init(self):
          self.tx = machine.Pin(self.txPin, machine.Pin.OUT)
          self.rx = machine.Pin(self.rxPin, machine.Pin.IN)
          self.tx.value(0)
          utime.sleep_ms(500)
          self.tx.value(1)
          self.uart=machine.UART(baudrate=2400, bits=8, parity=None, stop=1)
          self.writeIt(b'\x00')


class EV3_LPF2(LPF2):
     def init(self):
          self.uart.init(baudrate=2400, bits=8, parity=None, stop=1)
          self.writeIt(b'\x00')

     def defineVers(self,hardware,software):
          return bytearray([])

class OpenMV_LPF2(LPF2):
     uartchannel = 3

     def __init__(self, modes, sensor_id = WeDo_Ultrasonic, timer=4, freq=5):
          from uos import dupterm
          dupterm(None,2)
          super().__init__(modes, sensor_id, timer, freq)

     def write_tx_pin(self, value, sleep=500):
          from pyb import Pin
          txpin = Pin('P4', Pin.OUT_PP)
          txpin.value(value)
          utime.sleep_ms(sleep)

     def slow_uart(self):
          self.uart=machine.UART(self.uartchannel,2400)

     def fast_uart(self):
          self.uart=machine.UART(self.uartchannel,115200)
