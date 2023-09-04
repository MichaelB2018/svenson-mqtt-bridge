#!/usr/bin/python3
#
# pip3 install paho-mqtt tendo
#
# To run in crontab, do the same again as root
# sudo apt-get install pigpio
# sudo su -
# pip3 install paho-mqtt tendo
#
#
# CRONTAB:
# @reboot sleep 60;sudo /home/pi/svenson-mqtt-bridge/svenson-mqtt-bridge.py
# 0 * * * * sudo /home/pi/svenson-mqtt-bridge/svenson-mqtt-bridge.py
#
# GND: Blue
# GPIO12: TX: Red
# GPIO13: RX: Black
# GPIO14: CTS: Yellow
# GPIO15: RTS: Green
#


import re, requests, sys, os, logging, socket, time, uuid
import json, time, threading, argparse
import paho.mqtt.client as paho
import difflib
import pigpio

from tendo import singleton
from flask import Flask, render_template, request, Response, jsonify, json
from flask.logging import default_handler
from logging.handlers import RotatingFileHandler

try:
    from ConfigParser import RawConfigParser
except ImportError as e:
    from configparser import RawConfigParser

msgTypes = {"0x04": "Raise Head", "0x05": "Raise Foot", "0x07": "Lower Head", "0x08": "Lower Foot ", "0x0b": "Flat", "0x0c": "Stop Previous command", "0x0d": "Tilt Up ", "0x0e": "Tilt down", "0x14": "Light On", "0x15": "Light Off", "0x1e": "Massage Head Level 1", "0x1f": "Massage Head Level 2", "0x20": "Massage Head Level 3", "0x21": "Massage Head Off", "0x28": "Massage Foot Level 1", "0x29": "Massage Foot Level 2", "0x2a": "Massage Foot Level 3", "0x2b": "Massage Foot Off", "0x33": "Massage Mode 1", "0x34": "Massage Mode 2", "0x35": "Massage Mode 3", "0x36": "Massage On", "0x37": "Massage Off", "0x3d": "TV Mode", "0x40": "Anti-Snore Mode", "0x3f": "Zero-G Mode", "0x41": "M1", "0x42": "M2", "0xa5": "Store M1", "0xa6": "Store M2", "0xa1": "Store TV", "0xa3": "Store ZeroG", "0xa4": "Store AntiSnore"}
cmdTypes = {"M1": 0x41, "M2": 0x42, "TV": 0x3d, "tiltUp": 0x0d, "tiltDown": 0x0e, "zeroG": 0x3f, "antiSnore": 0x40, "light": [0x15, 0x14], "headUp": 0x04, "headDown": 0x07, "flat": 0x0b, "feetUp": 0x05, "feetDown": 0x08, "massageHead": [0x21, 0x1e, 0x1f, 0x20], "massageMode": [0x33, 0x34, 0x35],  "massageOnOff": [0x37, 0x36], "massageFeet": [0x2b, 0x28, 0x29, 0x2a], "cancel": 0x0c, "storeM1": 0xa5, "storeM2": 0xa6, "storeTV": 0xa1, "storeZeroG": 0xa3, "storeAntiSnore": 0xa4}
svensonState = {"head": 10000, "feet": 10000, "tilt": 10000, "lumbar": 10000, "light": 0, "massageHead": 0, "massageOnOff": 0, "massageFeet": 0, "massageMode": 0}
svensonContinuous = {"active": False, "cmd": None, "startTime": None, "prevState": svensonState.copy()}
svensonContinuousCmd = {"headUp": "head", "feetUp": "feet", "tiltUp": "tilt", "lumbarUp": "lumbar", "headDown": "head", "feetDown": "feet", "tiltDown": "tilt", "lumbarDown": "lumbar"}
TX = 0
RX = 0
CTS = 0
RTS = 0
config = {}
curr_path = os.path.dirname(__file__)
curr_name = os.path.basename(__file__)
uartLock = threading.Lock()
lightTimer = threading.Timer(300, None)
webapp = Flask(import_name="WebServer", static_url_path="", static_folder=curr_path+'/html')
mqttState = svensonState.copy()

def get_mac_address():
    mac = ''.join(['{:02x}'.format((uuid.getnode() >> (8 * i)) & 0xff)
                   for i in reversed(range(6))])
    return mac.lower()

def startPIGPIO():
   if sys.version_info[0] < 3:
       import commands
       status, process = commands.getstatusoutput('sudo pidof pigpiod')
       if status:  #  it wasn't running, so start it
           logger.info("pigpiod was not running")
           commands.getstatusoutput('sudo pigpiod')  # try to  start it
           time.sleep(0.5)
           # check it again
           status, process = commands.getstatusoutput('sudo pidof pigpiod')
   else:
       import subprocess
       status, process = subprocess.getstatusoutput('sudo pidof pigpiod')
       if status:  #  it wasn't running, so start it
           logger.info("pigpiod was not running")
           subprocess.getstatusoutput('sudo pigpiod')  # try to  start it
           time.sleep(0.5)
           # check it again
           status, process = subprocess.getstatusoutput('sudo pidof pigpiod')

   if not status:  # if it was started successfully (or was already running)...
       pigpiod_process = process
       logger.info("pigpiod is running, process ID is {} ".format(pigpiod_process))

       try:
           pi = pigpio.pi()  # local GPIO only
           logger.info("pigpio's pi instantiated")
       except Exception as e:
           start_pigpiod_exception = str(e)
           logger.error("problem instantiating pi: {}".format(start_pigpiod_exception))
   else:
       logger.error("start pigpiod was unsuccessful.")
       return False
   return True

###################################################################
###################################################################
## Config File
###################################################################
###################################################################
def LoadConfig(conf_file):
    global config

    try:
        configParser = RawConfigParser()
        configParser.read(conf_file)
    except Exception as e1:
        logger.critical("Error in LoadConfig: " + str(e1))
        return False

    parameters = {'DebugLevel': str, 'name': str, 'baudrate': int, 'RX': int, 'TX': int, 'CTS': int, 'RTS': int, 'MQTT_Server': str, 'MQTT_Port': int, 'MQTT_User': str, 'MQTT_Password': str, 'EnableDiscovery': bool, 'max_head': int, 'max_feet': int, 'max_tilt': int, 'max_lumbar': int, 'UseHttps': bool, 'HTTPPort': int, 'HTTPSPort': int, 'positionM1': str, 'positionM2': str, 'positionTV': str, 'positionZeroG': str, 'positionAntiSnore': str, 'defaultM1': str, 'defaultM2': str, 'defaultTV': str, 'defaultZeroG': str, 'defaultAntiSnore': str}

    for key, type in parameters.items():
        try:
            if configParser.has_option("General", key):
                config[key] = ReadValue(key, return_type=type, section="General", configParser=configParser)
        except Exception as e1:
            logger.critical("Missing config file or config file entries in Section General for key "+key+": " + str(e1))
            return False

    return True

def ReadValue(Entry, return_type = str, default = None, section = None, NoLog = False, configParser = None):
    try:
        if configParser.has_option(section, Entry):
            if return_type == str:
                return configParser.get(section, Entry)
            elif return_type == bool:
                return configParser.getboolean(section, Entry)
            elif return_type == float:
                return configParser.getfloat(section, Entry)
            elif return_type == int:
                return configParser.getint(section, Entry)
            else:
                logger.error("Error in ReadValue: invalid type:" + str(return_type))
                return default
        else:
            return default
    except Exception as e1:
        if not NoLog:
            logger.critical("Error in ReadValue: " + Entry + ": " + str(e1))
        return default
        
def LineIsSection(Line):
    Line = Line.strip()
    if Line.startswith("[") and Line.endswith("]") and len(Line) >=3 :
        return True
    return False
        
def GetSectionName(Line):
    Line = Line.strip()
    if Line.startswith("[") and Line.endswith("]") and len(Line) >=3 :
        Line = Line.replace("[", "")
        Line = Line.replace("]", "")
        return Line
    return ""

def WriteValue(Entry, Value, remove = False, section = None):
    global uartLock
    
    SectionFound = False
    try:
        with uartLock:
            Found = False
            ConfigFile = open(conf_file,'r')
            FileList = ConfigFile.read().splitlines()
            ConfigFile.close()

            mySectionStart = -1;
            mySectionEnd = -1;
            myLine = -1;
            currentLastDataLine = -1;
            for i, line in enumerate(FileList):
               if LineIsSection(line) and section.lower() == GetSectionName(line).lower():
                  mySectionStart = i
               elif mySectionStart >=0 and mySectionEnd == -1 and len(line.strip().split('=')) >= 2 and (line.strip().split('='))[0].strip() == Entry:
                  myLine = i
               elif mySectionStart >=0 and mySectionEnd == -1 and LineIsSection(line):
                  mySectionEnd = currentLastDataLine

               if not line.isspace() and not len(line.strip()) == 0 and not line.strip()[0] == "#":
                  currentLastDataLine = i
            if mySectionStart >=0 and mySectionEnd == -1:
                mySectionEnd = currentLastDataLine

            logger.debug("CONFIG FILE WRITE ->> mySectionStart = "+str(mySectionStart)+", mySectionEnd = "+str(mySectionEnd)+", myLine = "+str(myLine))
            if mySectionStart == -1:
                raise Exception("NOT ABLE TO FIND SECTION:"+section)

            ConfigFile = open(conf_file,'w')
            for i, line in enumerate(FileList):
                if myLine >= 0 and myLine == i and not remove:      # I found my line, now write new value
                   ConfigFile.write(Entry + " = " + Value + "\n")
                elif myLine == -1 and mySectionEnd == i:            # Here we have to insert the new record...
                   ConfigFile.write(line+"\n")
                   ConfigFile.write(Entry + " = " + Value + "\n")
                else:                                               # Nothing special, just copy the previous line....
                   ConfigFile.write(line+"\n")

            ConfigFile.flush()
            ConfigFile.close()
            # update the read data that is cached
            config[Entry] = Value
        return True

    except Exception as e1:
        logger.critical("Error in WriteValue: " + str(e1))
        return False
        
        
def storePosition(pos, head, feet, tilt, lumbar):
    logger.info(f'Storing Position for {pos}: Head: {head}, Feet: {feet}, Tilt: {tilt}, Lumbar: {lumbar}')
    WriteValue("position"+pos, f'{head}, {feet}, {tilt}, {lumbar}', section="General")
   
###################################################################
###################################################################
## HJC9 Interaction
###################################################################
###################################################################

def ser2wave(data,invert):
    global TX
    wave=[]
    bitlen=int(1e6/config["baudrate"])

    if invert:
        bitMark=(1<<TX, 0, bitlen)
        bitSpace=(0, 1<<TX, bitlen)
    else:
        bitSpace=(1<<TX, 0, bitlen)
        bitMark=(0, 1<<TX, bitlen)

    for c in data:
        wave.append(pigpio.pulse(*bitMark))                             #start bit
        for i in range (8):
            if c & (2**i):
                wave.append(pigpio.pulse(*bitSpace))                    #bit as space
            else:
                wave.append(pigpio.pulse(*bitMark))                     #bit as mark
        wave.append(pigpio.pulse(*bitSpace))                            #stop bit 1
        
    logger.debug("Sending "+str(len(wave))+" Pulses to HJC9. Message: "+data.hex())
    pi.wave_clear()
    pi.wave_add_generic(wave)
    wid = pi.wave_create()
    pi.wave_send_once(wid)
    while pi.wave_tx_busy():
        time.sleep(0.01);
    pi.wave_delete(wid)
    logger.debug("Message Sent")
    
def offLights():
    global svensonState
    
    logger.info("Lights turning off after 5min.")    
    svensonState["light"] = 0;
    updateMQTT()

def terminateContinuousOperation():
    global config
    global svensonContinuous
    global svensonState

    currTime = current_milli_time()
    prevCmd = svensonContinuous["cmd"]

    if (prevCmd in ["headUp", "feetUp", "tiltUp", "lumbarUp", "headDown", "feetDown", "tiltDown", "lumbarDown", "M1", "M2", "TV", "zeroG", "antiSnore", "flat"]):
        for type in ["head", "feet", "tilt", "lumbar"]:
            if (svensonState[type] > svensonContinuous["prevState"][type]):
                svensonState[type] = clamp(svensonContinuous["prevState"][type] + int((currTime-svensonContinuous["startTime"])/15), 10000, 10000+config["max_"+type])
            elif (svensonState[type] < svensonContinuous["prevState"][type]):
                svensonState[type] = clamp(svensonContinuous["prevState"][type] - int((currTime-svensonContinuous["startTime"])/15), 10000, 10000+config["max_"+type])
                
    updateMQTT()
    svensonContinuous["active"] = False


def isContinuousOperationInProgress(cmd):
    global config
    global svensonContinuous
    global svensonState

    currTime = current_milli_time()
    prevCmd = svensonContinuous["cmd"]
    
    if (svensonContinuous["active"] == False):
        return False
    elif (prevCmd == "flat") and (cmd == "light"):
        return False
    elif (prevCmd in ["headUp", "feetUp", "tiltUp", "lumbarUp", "headDown", "feetDown", "tiltDown", "lumbarDown", "M1", "M2", "TV", "zeroG", "antiSnore", "flat"]):
        for type in ["head", "feet", "tilt", "lumbar"]:
            if (abs(svensonState[type] - svensonContinuous["prevState"][type]) > int((currTime-svensonContinuous["startTime"])/15)):
                return True 
        svensonContinuous["active"] = False
        return False
    return True;
    
def getStatus():
    result = {}
    result["headPercent"]   = int(100*clamp(svensonState["head"]-10000, 0, config["max_head"])/(config["max_head"]+1))
    result["feetPercent"]   = int(100*clamp(svensonState["feet"]-10000, 0, config["max_feet"])/(config["max_feet"]+1))
    result["tiltPercent"]   = int(100*clamp(svensonState["tilt"]-10000, 0, config["max_tilt"])/(config["max_tilt"]+1))
    result["lumbarPercent"] = int(100*clamp(svensonState["lumbar"]-10000, 0, config["max_lumbar"])/(config["max_lumbar"]+1))
    result["head"]          = svensonState["head"]-10000
    result["feet"]          = svensonState["feet"]-10000
    result["tilt"]          = svensonState["tilt"]-10000
    result["lumbar"]        = svensonState["lumbar"]-10000
    
    return result


def sendMessageToSvenson(cmd, position = None):
    global cmdTypes
    global svensonState
    global svensonContinuous
    global svensonContinuousCmd
    global uartLock
    global lightTimer
    
    logger.debug("Ensure internal state is updated...")
    
    prevState =  svensonState.copy()

    if (svensonContinuous["active"] == False) and (cmd in ["headUp", "feetUp", "tiltUp", "lumbarUp", "headDown", "feetDown", "tiltDown", "lumbarDown", "M1", "M2", "TV", "zeroG", "antiSnore", "flat"]):
        svensonContinuous = {"active": True, "cmd": cmd, "startTime": current_milli_time(), "prevState": svensonState.copy()}

    if (cmd in svensonContinuousCmd):
        if ("Up" in cmd):
           svensonState[svensonContinuousCmd[cmd]] = position if (position != None) else 10000 + config["max_"+svensonContinuousCmd[cmd]]
           prevState[svensonContinuousCmd[cmd]] = svensonState[svensonContinuousCmd[cmd]]
        elif ("Down" in cmd):
           svensonState[svensonContinuousCmd[cmd]] = position if (position != None) else 10000
           prevState[svensonContinuousCmd[cmd]] = svensonState[svensonContinuousCmd[cmd]]
    elif (cmd == "flat"):
        svensonState["head"]   = 10000
        svensonState["feet"]   = 10000
        svensonState["tilt"]   = 10000
        svensonState["lumbar"] = 10000
    elif (cmd in ["M1", "M2", "TV", "zeroG", "antiSnore"]):
        svensonState["head"]   = int(config["position"+cmd[0].upper() + cmd[1:]].split(', ')[0]) + 10000
        svensonState["feet"]   = int(config["position"+cmd[0].upper() + cmd[1:]].split(', ')[1]) + 10000
        svensonState["tilt"]   = int(config["position"+cmd[0].upper() + cmd[1:]].split(', ')[2]) + 10000
        svensonState["lumbar"] = int(config["position"+cmd[0].upper() + cmd[1:]].split(', ')[3]) + 10000
    elif (cmd in ["storeM1", "storeM2", "storeTV", "storeZeroG", "storeAntiSnore"]):
        storePosition(cmd[5:], clamp(svensonState["head"]-10000, 0, config["max_head"]), clamp(svensonState["feet"]-10000, 0, config["max_feet"]), clamp(svensonState["tilt"]-10000, 0, config["max_tilt"]), clamp(svensonState["lumbar"]-10000, 0, config["max_lumbar"]))
    elif (cmd == "cancel"):
        terminateContinuousOperation()
        prevState =  svensonState.copy()
    elif (cmd == "light"):
       if (svensonState["light"] == 0):
           lightTimer = threading.Timer(300, offLights)
           lightTimer.start()
       else:
           lightTimer.cancel()
    elif (cmd == "massageHead"): 
       if (svensonState["massageHead"] <= 2): 
          svensonState["massageOnOff"] = 1;
       else:   
          if (svensonState["massageFeet"] == 0):
             svensonState["massageOnOff"] = 0;
    elif (cmd == "massageFeet"): 
       if (svensonState["massageFeet"] <= 2): 
          svensonState["massageOnOff"] = 1;
       else:   
          if (svensonState["massageHead"] == 0):
             svensonState["massageOnOff"] = 0;
    elif (cmd == "massageOnOff"):
       if (svensonState["massageOnOff"] == 0):
          svensonState["massageHead"] = 1
          svensonState["massageFeet"] = 1
       else:
          svensonState["massageHead"] = 0
          svensonState["massageFeet"] = 0
       
        
    logger.debug("Start composing message")

    msg_str = "99"
    chksum = 0
    if type(cmdTypes[cmd]) == int:
        msg_str += format(cmdTypes[cmd], '02x')
        chksum = int(cmdTypes[cmd])
    elif type(cmdTypes[cmd]) == list:
        svensonState[cmd] += 1
        if (svensonState[cmd] >= len(cmdTypes[cmd])):
           svensonState[cmd] = 0
        msg_str += format(cmdTypes[cmd][svensonState[cmd]], '02x')
        chksum = int(cmdTypes[cmd][svensonState[cmd]])
        
    for key in ["head", "feet", "tilt", "lumbar"]: 
        chksum  += prevState[key]&(0xff);
        chksum  += (prevState[key]&(0xff00))>>8;
        msg_str += format(prevState[key], '04x') 
    msg_str += format((chksum % 256), '02x')
    msg_str += "BB"
    msg = bytearray.fromhex(msg_str)
    logger.info("Finsihed composing message, now let's send it... Sending message to HJC9: "+" ".join(["{:02x}".format(x) for x in msg]))
    
    uartLock.acquire()
    logger.debug("Acquried syncronized lock. now sending messages")
    pi.write(RTS, 0)
    if (cmd in svensonContinuousCmd):
        ser2wave(msg, True)
        time.sleep(.142)
        ser2wave(msg, True)
    elif (cmd in ["cancel", "storeM1", "storeM2", "storeTV", "storeZeroG", "storeAntiSnore"]):
        ser2wave(msg, True)
    else:
        ser2wave(msg+msg, True)
    time.sleep(.2)
    pi.write(RTS, 1)    
    uartLock.release()
    
    updateMQTT()
    logger.debug("Lock released, message sent HJC9")
    
def clamp(n, minn, maxn):
    return max(min(maxn, n), minn)

def current_milli_time():
    return round(time.time() * 1000)
    
def processMessageFromSvenson(msg):
    global msgTypes
    global svensonState
    global lightTimer
    storeType = {0xa5: "M1", 0xa6: "M2", 0xa1: "TV", 0xa3: "ZeroG", 0xa4: "AntiSnore"} 
    
    if ("{0:#0{1}x}".format(msg[1],4) not in msgTypes.keys()):
       logger.error("Received unknown Message Type: "+"{0:#0{1}x}".format(msg[1],4)+". Message: "+msg.hex())
       return False
    type   = msgTypes["{0:#0{1}x}".format(msg[1],4)]
    head   = int(100*clamp((256*msg[2]+msg[3])-10000, 0, config["max_head"])/(config["max_head"]+1))
    foot   = int(100*clamp((256*msg[4]+msg[5])-10000, 0, config["max_feet"])/(config["max_feet"]+1))
    tilt   = int(100*clamp((256*msg[6]+msg[7])-10000, 0, config["max_tilt"])/(config["max_tilt"]+1))
    lumbar = int(100*clamp((256*msg[8]+msg[9])-10000, 0, config["max_lumbar"])/(config["max_lumbar"]+1))
    chksum = msg[10]
    vrfy   = (msg[1] + msg[2] + msg[3] + msg[4] + msg[5] + msg[6] + msg[7] + msg[8] + msg[9]) % 256
    if (chksum != vrfy):
       logger.error("Checksum failed on message. Expected: "+str(chksum)+" vs Actual: "+str(vrfy)+". Message: "+msg.hex())
       return False
       
    svensonState["head"]   = 256*msg[2]+msg[3]
    svensonState["feet"]   = 256*msg[4]+msg[5]
    svensonState["tilt"]   = 256*msg[6]+msg[7]
    svensonState["lumbar"] = 256*msg[8]+msg[9]
    if (msg[1] in [0x14, 0x15]):
       svensonState["light"]        = cmdTypes["light"].index(msg[1])
       if (msg[1] == 0x14):
           lightTimer = threading.Timer(300, offLights)
           lightTimer.start()
       else:
           lightTimer.cancel()
    elif (msg[1] in [0x36, 0x37]):
       svensonState["massageOnOff"] = cmdTypes["massageOnOff"].index(msg[1])
       if (svensonState["massageOnOff"] == 0):
           svensonState["massageHead"] = 0
           svensonState["massageFeet"] = 0
       else:
          svensonState["massageHead"] = 1
          svensonState["massageFeet"] = 1
    elif (msg[1] in [0x21, 0x1e, 0x1f, 0x20]):
       svensonState["massageHead"]  = cmdTypes["massageHead"].index(msg[1])
       if (svensonState["massageHead"] > 0):
           svensonState["massageOnOff"] = 1
       elif (svensonState["massageFeet"] == 0):
           svensonState["massageOnOff"] = 0
    elif (msg[1] in [0x2b, 0x28, 0x29, 0x2a]):
       svensonState["massageFeet"]  = cmdTypes["massageFeet"].index(msg[1])
       if (svensonState["massageFeet"] > 0):
           svensonState["massageOnOff"] = 1
       elif (svensonState["massageHead"] == 0):
           svensonState["massageOnOff"] = 0
    elif (msg[1] in [0x33, 0x34, 0x35]):
       svensonState["massageMode"]  = cmdTypes["massageMode"].index(msg[1])
    elif (msg[1] in storeType.keys()):
       storePosition(storeType[msg[1]], clamp((256*msg[2]+msg[3])-10000, 0, config["max_head"]), clamp((256*msg[4]+msg[5])-10000, 0, config["max_feet"]), clamp((256*msg[6]+msg[7])-10000, 0, config["max_tilt"]), clamp((256*msg[8]+msg[9])-10000, 0, config["max_lumbar"]))

    updateMQTT()
    logger.info(f'Type: {type}; Head: {head}%; Foot: {foot}%; Tilt: {tilt}%; Lumbar: {lumbar}% ({svensonState["head"]} | {svensonState["feet"]} | {svensonState["tilt"]})')  
    return True

def receiveMessageFromSvenson():
    global RX
   
    twelve_char_time = 100.0 / float(baud)
    if twelve_char_time < 0.1:
       twelve_char_time = 0.1

    while True:
       count = 1
       received_msg=bytearray()
       lt = 0
    
       # print ("READING MESSAGES")
       while count: # read echoed serial data
          (count, data) = pi.bb_serial_read(RX)
          if count:
              received_msg += data
              lt += count
          time.sleep(twelve_char_time) # enough time to ensure more data
    
       while (lt >= 12):
           logger.debug(received_msg.hex())
           if (received_msg[0] == int('0x99', 0)) and (received_msg[11] in [int('0xBB', 0), int('0x3B', 0)]):
               curr_msg = received_msg[0:12]
               logger.debug(" ".join(["{:02x}".format(x) for x in curr_msg]))
               processMessageFromSvenson(curr_msg)
               received_msg = received_msg[12:]
               lt -= 12
           else:
               i = 1
               foundMessage = False
               while (i <= 11) and (foundMessage == False):
                   if (received_msg[i] == int('0x99', 0)) and (received_msg[i+11] in [int('0xBB', 0), int('0x3B', 0)]):
                      # Found good message
                      garbage_msg = received_msg[:(i-1)]
                      readHex = " ".join(["{:02x}".format(x) for x in garbage_msg])
                      logger.warning("Discard partial message: "+garbage_msg)
                      received_msg = received_msg[i:]
                      foundMessage = True
                      lt = lt - i
                   else:
                      i = i + 1
               if (foundMessage == False):
                   readHex = " ".join(["{:02x}".format(x) for x in received_msg])
                   logger.error("unable to find new message in string of 11 characters: "+readHex)
                   logger.error("Discarding entire buffer of messages")
                   received_msg = bytearray()
                   lt = 0


###################################################################
###################################################################
## MQTT
###################################################################
###################################################################

def sendRawMQTT(topic, msg):
    logger.info("PUBLISHING to MQTT: " + topic + " = " + msg)
    t.publish(topic,msg,retain=True)

def sendMQTT(dev_id, device_type, status):
    if (device_type == "switch"):
       logger.info("PUBLISHING to MQTT: home/svenson/switch/state/" + str(dev_id) + " = " + ("ON" if (status == 1) else "OFF"))
       t.publish("home/svenson/switch/state/"+str(dev_id), ("ON" if (status == 1) else "OFF"), retain=True)
    elif (device_type == "number"):
       logger.info("PUBLISHING to MQTT: home/svenson/number/state/" + str(dev_id) + " = " + str(status))
       t.publish("home/svenson/number/state/"+str(dev_id),str(status),retain=True)
    elif (device_type == "select"):
       logger.info("PUBLISHING to MQTT: home/svenson/select/state/" + str(dev_id) + " = " + str(status))
       t.publish("home/svenson/select/state/"+str(dev_id),str(status),retain=True)
    else:
       logger.critical("Exception Occurred in sendMQTT with device type: " + device_type)

def updateMQTT():
    global svensonState
    global mqttState
    global config
    device_id = ''.join(filter(str.isalnum, config["name"]))


    if config["MQTT_Server"] and config["MQTT_Server"].strip():
        for key in mqttState.keys():
           if (mqttState[key] != svensonState[key]):
               if (key in ["head", "feet", "tilt", "lumbar"]):
                   sendMQTT(device_id+"_"+key, "number", getStatus()[key+"Percent"])
               elif (key == "light"):
                   sendMQTT(device_id+"_light", "switch", svensonState["light"])
               elif (key == "massageOnOff"):
                   sendMQTT(device_id+"_massage", "switch", svensonState["massageOnOff"])
               elif (key in ["massageHead", "massageFeet"]):
                   sendMQTT(device_id+"_"+key, "select", "Level "+str(svensonState[key]))
               mqttState[key] = svensonState[key]

def sendStartupInfo():
    global config 
    global svensonState
    device_id = ''.join(filter(str.isalnum, config["name"]))

    if config["EnableDiscovery"]:
        logger.info("Sending Discovery Information....")
        sendStartupState()
        logger.info("Waiting 10 seconds to allow Home Assistant to catch up, otehrwise the availability messages will be ignored")
        time.sleep(10)

    logger.info("Sending Initial Status Messages...")
    sendMQTT(device_id+"_light", "switch", svensonState["light"])
    sendMQTT(device_id+"_massage", "switch", svensonState["massageOnOff"])
    sendMQTT(device_id+"_head", "number", getStatus()["headPercent"])
    sendMQTT(device_id+"_feet", "number", getStatus()["feetPercent"])
    sendMQTT(device_id+"_tilt", "number", getStatus()["tiltPercent"])
    sendMQTT(device_id+"_lumbar", "number", getStatus()["lumbarPercent"])
    sendMQTT(device_id+"_preset", "select", "Select Preset Positions")
    sendMQTT(device_id+"_massageHead", "select", "Level "+str(svensonState["massageHead"]))
    sendMQTT(device_id+"_massageFeet", "select", "Level "+str(svensonState["massageFeet"]))

def on_connect(client, userdata, flags, rc):
    global config

    logger.info("Connected to MQTT with result code "+str(rc))
    t.subscribe("home/svenson/switch/command/+")
    t.subscribe("home/svenson/number/command/+")
    t.subscribe("home/svenson/select/command/+")
    restart_thread = threading.Thread(target=sendStartupInfo)
    restart_thread.daemon = True
    restart_thread.start()
    logger.info("Started Restart Thread to send initialization Information to Home Assistant")

def receiveMessageFromMQTT(client, userdata, message):
    global config
    
    logger.info("starting receiveMessageFromMQTT")
    try:
        msg = str(message.payload.decode("utf-8"))
        topic = message.topic
        logger.info("message received from MQTT: "+topic+" = "+msg)

        type = topic.split("/")[3]
        position = 0
        if type == "command":
            device_id = topic.split("/")[4].split("_")[0]
            command = topic.split("/")[4].split("_")[1]
            if (command == "preset"):
                command = msg  ## This works correct for M1, M2 & TV
                if (msg == "Zero G"):
                  command="zeroG"
                elif (msg == "Anti Snore"):
                  command="antiSnore"
                elif (msg == "Flat"):
                  command="flat"
                sendMQTT(device_id+"_preset", "select", "Select Preset Positions")
            elif (command == "massage"):
                command = "massageOnOff"
            elif (command in ["head", "feet", "tilt", "lumbar"]):
                position = int(10000 + (int(msg)/100*config["max_"+command]))
                if (position >= svensonState[command]):
                   command = command+"Up"
                else:
                   command = command+"Down"
            elif (command == "massageHead"):
                if (int(msg.split(" ")[1]) == 0):
                    svensonState["massageHead"] = 3
                elif (int(msg.split(" ")[1]) == 1):
                    svensonState["massageHead"] = 0
                else:
                    svensonState["massageHead"] = 0
                    sendMessageToSvenson("massageHead")
                    svensonState["massageHead"] = int(msg.split(" ")[1])-1
            elif (command == "massageFeet"):
                if (int(msg.split(" ")[1]) == 0):
                    svensonState["massageFeet"] = 3
                elif (int(msg.split(" ")[1]) == 1):
                    svensonState["massageFeet"] = 0
                else:
                    svensonState["massageFeet"] = 0
                    sendMessageToSvenson("massageFeet")
                    svensonState["massageFeet"] = int(msg.split(" ")[1])-1
            
            try:
                if command in cmdTypes.keys():
                    logger.info("processing Command \"" + command + "\"")
                    if (svensonContinuous["active"] == True):
                        if (svensonContinuous["cmd"] == command):
                            logger.info("Ignore command as it's still active")
                        else:
                            logger.info("Stop Previous command: "+svensonContinuous["cmd"])
                            if (isContinuousOperationInProgress(command)):
                                sendMessageToSvenson("cancel")
                            sendMessageToSvenson(command, None if (command not in svensonContinuousCmd) else position)
                    else:
                        sendMessageToSvenson(command, None if (command not in svensonContinuousCmd) else position)
                elif (command == "Select Preset Positions"):
                   pass
                else:
                   logger.warning("RECEIVED UNKNOWN COMMAND FROM MQTT: " + command + ". Ignoring tis command")
            except Exception as e1:
                logger.error("Error in Process MQTT Command: " + command + ": " + str(e1))


    except Exception as e1:
        logger.critical("Exception Occured: " + str(e1))

    logger.info("finishing receiveMessageFromMQTT")

def sendStartupState():
    global config 
    device_id = ''.join(filter(str.isalnum, config["name"]))

    logger.info("Send Discovery Messages")
    sendRawMQTT("homeassistant/select/"+device_id+"_preset/config", '{"name": "'+config['name']+' Preset Positions", "unique_id": "SvenSon_select_'+device_id+'", "command_topic": "home/svenson/select/command/'+device_id+'_preset", "state_topic": "home/svenson/select/state/'+device_id+'_preset", "options": ["Select Preset Positions", "Flat", "M1", "M2", "TV", "Zero G", "Anti Snore"], "optimistic": false}')
    sendRawMQTT("homeassistant/select/"+device_id+"_massageHead/config", '{"name": "'+config['name']+' Massage Head", "unique_id": "SvenSon_select_'+device_id+'_massageHead", "command_topic": "home/svenson/select/command/'+device_id+'_massageHead", "state_topic": "home/svenson/select/state/'+device_id+'_massageHead", "options": ["Level 0", "Level 1", "Level 2", "Level 3"], "icon": "mdi:vibrate"}')
    sendRawMQTT("homeassistant/select/"+device_id+"_massageFeet/config", '{"name": "'+config['name']+' Massage Feet", "unique_id": "SvenSon_select_'+device_id+'_massageFeet", "command_topic": "home/svenson/select/command/'+device_id+'_massageFeet", "state_topic": "home/svenson/select/state/'+device_id+'_massageFeet", "options": ["Level 0", "Level 1", "Level 2", "Level 3"], "icon": "mdi:vibrate"}')

    sendRawMQTT("homeassistant/switch/"+device_id+"_light/config", '{"name": "'+config['name']+' Light", "unique_id": "SvenSon_switch_'+device_id+'_light", "command_topic": "home/svenson/switch/command/'+device_id+'_light", "state_topic": "home/svenson/switch/state/'+device_id+'_light", "icon": "mdi:lightbulb-on"}')
    sendRawMQTT("homeassistant/switch/"+device_id+"_massage/config", '{"name": "'+config['name']+' Massage", "unique_id": "SvenSon_switch_'+device_id+'_massage", "command_topic": "home/svenson/switch/command/'+device_id+'_massage", "state_topic": "home/svenson/switch/state/'+device_id+'_massage", "icon": "mdi:vibrate"}')

    sendRawMQTT("homeassistant/number/"+device_id+"_head/config", '{"name": "'+config['name']+' Head Level", "unique_id": "SvenSon_number_'+device_id+'_head", "min": 0, "max": 100, "command_topic": "home/svenson/number/command/'+device_id+'_head", "state_topic": "home/svenson/number/state/'+device_id+'_head", "icon": "mdi:swap-vertical"}')
    sendRawMQTT("homeassistant/number/"+device_id+"_feet/config", '{"name": "'+config['name']+' Feet Level", "unique_id": "SvenSon_number_'+device_id+'_feet", "min": 0, "max": 100, "command_topic": "home/svenson/number/command/'+device_id+'_feet", "state_topic": "home/svenson/number/state/'+device_id+'_feet", "icon": "mdi:swap-vertical"}')
    sendRawMQTT("homeassistant/number/"+device_id+"_tilt/config", '{"name": "'+config['name']+' Tilt Level", "unique_id": "SvenSon_number_'+device_id+'_tilt", "min": 0, "max": 100, "command_topic": "home/svenson/number/command/'+device_id+'_tilt", "state_topic": "home/svenson/number/state/'+device_id+'_tilt", "icon": "mdi:swap-vertical"}')
    sendRawMQTT("homeassistant/number/"+device_id+"_lumbar/config", '{"name": "'+config['name']+' Lumbar Level", "unique_id": "SvenSon_number_'+device_id+'_lumbar", "min": 0, "max": 100, "command_topic": "home/svenson/number/command/'+device_id+'_lumbar", "state_topic": "home/svenson/number/state/'+device_id+'_lumbar", "icon": "mdi:swap-vertical"}')



###################################################################
###################################################################
## Web Server
###################################################################
###################################################################
 
def add_header(r):
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, public, max-age=0"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"
    return r

def processCommand(*args, **kwargs):
    global cmdTypes
    global svensonContinuous
    
    logger.info(request.url + " ( "+ request.method + " ): "+ str(args) + " | "+ str(kwargs))
    try:
        command = kwargs['command']
        if command in cmdTypes.keys():
            logger.info("processing Command \"" + command + "\"")
            if (svensonContinuous["active"] == True):
                if (svensonContinuous["cmd"] == command):
                    logger.info("Ignore command as it's still active")
                else:
                    logger.info("Stop Previous command: "+svensonContinuous["cmd"])   
                    if (isContinuousOperationInProgress(command)):
                        sendMessageToSvenson("cancel")
                    sendMessageToSvenson(command)
            else: 
                sendMessageToSvenson(command)
            result = {};
            return Response(json.dumps(result), status=200)
        elif (command == "cancelHold"):
            logger.info("processing Command \"" + command + "\"")

            if (svensonContinuous["active"] == True):    
                sendMessageToSvenson("cancel")
            result = {};
            return Response(json.dumps(result), status=200)
        elif (command == "reset"):
            logger.info("processing Command \"" + command + "\"")

            for type in ["TV", "ZeroG", "AntiSnore", "M1", "M2"]:
                logger.debug("resetting \"" + type + "\"...")
                sendMessageToSvenson("flat")
                time.sleep(30)
                sendMessageToSvenson("headUp", int(config["default"+type].split(', ')[0]) + 10000)
                time.sleep(30)
                sendMessageToSvenson("feetUp", int(config["default"+type].split(', ')[1]) + 10000)
                time.sleep(20)
                sendMessageToSvenson("tiltUp", int(config["default"+type].split(', ')[2]) + 10000)
                time.sleep(15)
                sendMessageToSvenson("store"+type)
            sendMessageToSvenson("flat")
            time.sleep(30)
            result = {};
            return Response(json.dumps(result), status=200)
        elif (command == "status"):
            logger.info("processing Command \"status\"")
            result = getStatus();
            return Response(json.dumps(result), status=200)
        else:
            logger.warning("UNKNOWN COMMAND " + command)
            result = {"Error: Unknown Command: " + command}
            return Response(json.dumps(result), status=400)
    except Exception as e1:
        logger.error("Error in Process Command: " + command + ": " + str(e1))
        result = {"Error: Exception occured"}
        return Response(json.dumps(result), status=400)

def generate_adhoc_ssl_context():
    """Generates an adhoc SSL context for the development server."""
    #        crypto = _get_openssl_crypto_module()
    import tempfile
    import atexit
    from random import random

    cert = crypto.X509()
    cert.set_serial_number(int(random() * sys.maxsize))
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(60 * 60 * 24 * 365)

    subject = cert.get_subject()
    subject.CN = '*'
    subject.O = 'Dummy Certificate'

    issuer = cert.get_issuer()
    issuer.CN = 'Untrusted Authority'
    issuer.O = 'Self-Signed'

    pkey = crypto.PKey()
    pkey.generate_key(crypto.TYPE_RSA, 2048)
    cert.set_pubkey(pkey)
    cert.sign(pkey, 'sha256')

    cert_handle, cert_file = tempfile.mkstemp()
    pkey_handle, pkey_file = tempfile.mkstemp()
    atexit.register(os.remove, pkey_file)
    atexit.register(os.remove, cert_file)

    os.write(cert_handle, crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
    os.write(pkey_handle, crypto.dump_privatekey(crypto.FILETYPE_PEM, pkey))
    os.close(cert_handle)
    os.close(pkey_handle)
    # ctx = ssl.SSLContext(ssl.PROTOCOL_TLS)
    ctx = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
    ctx.load_cert_chain(cert_file, pkey_file)
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

###################################################################
###################################################################
## Main
###################################################################
###################################################################

if __name__ == '__main__':
    LEVELS = {'debug': logging.DEBUG,
              'info': logging.INFO,
              'warning': logging.WARNING,
              'error': logging.ERROR,
              'critical': logging.CRITICAL}

    log_name = curr_name.replace(".py", ".log")
    log_file = curr_path+"/"+log_name
    log_formatter = logging.Formatter('%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s')

    rotatingHandler = RotatingFileHandler(log_file, mode='a', maxBytes=1*1024*1024, backupCount=2, encoding=None, delay=0)
    rotatingHandler.setFormatter(log_formatter)
    rotatingHandler.setLevel(logging.INFO)

    logger = logging.getLogger('root')
    logger.addHandler(rotatingHandler)

    # Make sure we are not running already. Otherwise Exit
    try:
       tmp = logging.getLogger().level
       logging.getLogger().setLevel(logging.CRITICAL) # we do not want to see the warning
       me = singleton.SingleInstance() # will sys.exit(-1) if other instance is running
       logging.getLogger().setLevel(tmp)
    except:
       logging.getLogger().setLevel(logging.INFO)
       logger.info("Another instance is already running. quiting...")
       exit()

    # Now read the config file
    parser = argparse.ArgumentParser(description='Sven & Son (Richmat Qingdao HJC9) to MQTT Integration for Home Assistant.')
    parser.add_argument('-config', '-c', dest='ConfigFile', default=curr_path+'/svenson-mqtt-bridge.conf', help='Name of the Config File (incl full Path)')
    args = parser.parse_args()

    if args.ConfigFile == None:
        conf_name = curr_name.replace(".py", ".conf")
        conf_file = curr_path+"/"+conf_name
    else:
        conf_file = args.ConfigFile

    if not os.path.isfile(conf_file):
        logger.info("Creating new config file : " + conf_file)
        defaultConfigFile = curr_path+'/defaultConfig.conf'
        if not os.path.isfile(defaultConfigFile):
            logger.critical("Failure to create new config file: "+defaultConfigFile)
            sys.exit(1)
        else:
            copyfile(defaultConfigFile, conf_file)

    if not LoadConfig(conf_file):
        logger.critical("Failure to load configuration parameters")
        sys.exit(1)

    level = LEVELS.get(config["DebugLevel"], logging.WARNING)
    logging.getLogger().setLevel(level)
    rotatingHandler.setLevel(level)

    RX=config["RX"]
    TX=config["TX"]
    CTS=config["CTS"]
    RTS=config["RTS"]
    baud=config["baudrate"]

    logger.info("Starting PIGPIOD")
    startPIGPIO()
    logger.info("PIGPIOD started")
    
    pi = pigpio.pi()
    pi.set_mode(TX, pigpio.OUTPUT)

    # fatal exceptions off (so that closing an unopened gpio doesn't error)
    pigpio.exceptions = False
    pi.bb_serial_read_close(RX)
    # fatal exceptions on
    pigpio.exceptions = True
    

    webapp.after_request(add_header)
    kwargs = {}
    if config["UseHttps"]:
       import ssl
       from OpenSSL import crypto
       logger.info("Starting secure WebServer on Port "+str(config["HTTPSPort"]))
       kwargs = {'host':"0.0.0.0", 'port':config["HTTPSPort"], 'threaded':True, 'ssl_context':generate_adhoc_ssl_context(), 'use_reloader':False, 'debug':False}
    else:
       logger.info("Starting WebServer on Port "+str(config["HTTPPort"]))
       kwargs = {'host':"0.0.0.0", 'threaded':True, 'port':config["HTTPPort"], 'use_reloader':False, 'debug':False}
    webapp.add_url_rule('/', 'index', lambda: open(curr_path+"/html/index.html").read().replace('%%NAME%%', config["name"]))
    webapp.add_url_rule('/index.html', 'index_full', lambda: open(curr_path+"/html/index.html").read().replace('%%NAME%%', config["name"]))
    webapp.add_url_rule('/cmd/<command>', 'cmd', processCommand, ['GET', 'POST'])
    log = logging.getLogger('werkzeug')
    log.disabled = True
    ## webapp.logger.removeHandler(default_handler)
    ## webapp.logger.addHandler(rotatingHandler)
    Webserver_thread = threading.Thread(target=webapp.run, daemon=True, kwargs=kwargs).start()
    logger.info("Started WebServer Thread")
    
    # open a gpio to bit bang read the echoed data
    bits = 8
    pi.bb_serial_read_open(RX, baud, bits)
    pi.bb_serial_invert(RX, 1) # Invert line logic.
    
    svensonListener_thread = threading.Thread(target=receiveMessageFromSvenson)
    svensonListener_thread.daemon = True
    svensonListener_thread.start()
    logger.info("Started Listener Thread to listen to messages from Sven&Son HJC9")

    # And connect to MQTT
    if config["MQTT_Server"] and config["MQTT_Server"].strip():
        logger.info("Connecting to MQ....")
        t = paho.Client(client_id="svenson-mqtt-bridge_"+get_mac_address())                           #create client object
        t.username_pw_set(username=config["MQTT_User"],password=config["MQTT_Password"])
        t.on_connect = on_connect
        t.on_message=receiveMessageFromMQTT
        t.connect(config["MQTT_Server"],config["MQTT_Port"])
        logger.info("Connected to MQTT on "+config["MQTT_Server"]+":"+str(config["MQTT_Port"]))

        logger.info("Starting Listener Thread to listen to messages from MQTT")
        t.loop_start()

    while True:
       time.sleep(60)
      
    if config["MQTT_Server"] and config["MQTT_Server"].strip():
        t.loop_stop()
        logger.info("Stop Listener Thread to listen to messages from MQTT")

    # free resources
    
    pi.bb_serial_read_close(RX)
    pi.stop()
    
    sys.exit()

    
    
