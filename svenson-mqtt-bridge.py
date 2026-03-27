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
import json, threading, argparse
import paho.mqtt.client as paho
import difflib
import pigpio
import signal
import hmac
import html as html_mod
import secrets

from shutil import copyfile
from tendo import singleton
from flask import Flask, render_template, request, Response, jsonify, redirect, make_response, render_template_string, url_for
from flask.logging import default_handler
from logging.handlers import RotatingFileHandler
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix

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
# curr_path = os.path.dirname(__file__)
curr_path = os.path.dirname(os.path.abspath(__file__))
curr_name = os.path.basename(__file__)
uartLock = threading.Lock()
lightTimer = threading.Timer(300, None)
server_public_ip = None
server_public_ip_last_check = 0
auth_sessions = {}  # token -> {"expires": timestamp, "csrf": csrf_token}
login_attempts = {}  # ip -> {"count": int, "lockout_until": timestamp}

# webapp = Flask(import_name="WebServer", static_url_path="", static_folder=curr_path+'/html')
webapp = Flask(
    __name__,
    static_url_path='',
    static_folder=os.path.join(curr_path, 'html'),
    template_folder=os.path.join(curr_path, 'html')  # Optional, if you use templates
)
webapp.wsgi_app = ProxyFix(webapp.wsgi_app, x_for=1)
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

    parameters = {'DebugLevel': str, 'name': str, 'baudrate': int, 'RX': int, 'TX': int, 'CTS': int, 'RTS': int, 'MQTT_Server': str, 'MQTT_Port': int, 'MQTT_User': str, 'MQTT_Password': str, 'EnableDiscovery': bool, 'WebURL': str, 'max_head': int, 'max_feet': int, 'max_tilt': int, 'max_lumbar': int, 'HttpLocalOnly': bool, 'Password': str, 'BypassAuthForOwnNetwork': bool, 'UseHttps': bool, 'HTTPPort': int, 'HTTPSPort': int, 'positionM1': str, 'positionM2': str, 'positionTV': str, 'positionZeroG': str, 'positionAntiSnore': str, 'defaultM1': str, 'defaultM2': str, 'defaultTV': str, 'defaultZeroG': str, 'defaultAntiSnore': str}

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
    elif (msg[1] in [0x0b]):
       svensonState["head"]   = 10000
       svensonState["feet"]   = 10000
       svensonState["tilt"]   = 10000
       svensonState["lumbar"] = 10000
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
                      logger.warning("Discard partial message: "+readHex)
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
    t.publish(topic, msg, retain=True)

def get_device_id():
    return ''.join(filter(str.isalnum, config["name"]))

def get_web_url():
    if config.get("WebURL", "").strip():
        return config["WebURL"].strip()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((config["MQTT_Server"], 1))
        local_ip = s.getsockname()[0]
        s.close()
        scheme = "https" if config.get("UseHttps") else "http"
        port = config.get("HTTPSPort", 443) if config.get("UseHttps") else config.get("HTTPPort", 80)
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            return "{}://{}".format(scheme, local_ip)
        return "{}://{}:{}".format(scheme, local_ip, port)
    except Exception:
        return None

def sendMQTT(entity, value):
    device_id = get_device_id()
    topic = "svenson/{}/{}/state".format(device_id, entity)
    logger.info("PUBLISHING to MQTT: " + topic + " = " + str(value))
    t.publish(topic, str(value), retain=True)

def updateMQTT():
    global svensonState
    global mqttState
    global config

    if config["MQTT_Server"] and config["MQTT_Server"].strip():
        for key in mqttState.keys():
            if mqttState[key] != svensonState[key]:
                if key in ["head", "feet", "tilt", "lumbar"]:
                    sendMQTT(key, getStatus()[key + "Percent"])
                elif key == "light":
                    sendMQTT("light", "ON" if svensonState["light"] else "OFF")
                elif key == "massageOnOff":
                    sendMQTT("massage", "ON" if svensonState["massageOnOff"] else "OFF")
                elif key in ["massageHead", "massageFeet"]:
                    sendMQTT(key, "Level " + str(svensonState[key]))
                mqttState[key] = svensonState[key]

def sendStartupInfo():
    global config
    global svensonState
    device_id = get_device_id()

    if config["EnableDiscovery"]:
        logger.info("Sending Discovery Information....")
        sendStartupState()
        logger.info("Waiting 10 seconds to allow Home Assistant to catch up")
        time.sleep(10)

    # Publish availability
    sendRawMQTT("svenson/{}/availability".format(device_id), "online")

    logger.info("Sending Initial Status Messages...")
    sendMQTT("light", "ON" if svensonState["light"] else "OFF")
    sendMQTT("massage", "ON" if svensonState["massageOnOff"] else "OFF")
    sendMQTT("head", getStatus()["headPercent"])
    sendMQTT("feet", getStatus()["feetPercent"])
    sendMQTT("tilt", getStatus()["tiltPercent"])
    sendMQTT("lumbar", getStatus()["lumbarPercent"])
    sendMQTT("preset", "Select Preset Positions")
    sendMQTT("massageHead", "Level " + str(svensonState["massageHead"]))
    sendMQTT("massageFeet", "Level " + str(svensonState["massageFeet"]))

def on_connect(client, userdata, flags, rc):
    global config
    device_id = get_device_id()

    logger.info("Connected to MQTT with result code " + str(rc))
    t.subscribe("svenson/{}/+/command".format(device_id))
    t.subscribe("homeassistant/status")
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
        logger.info("message received from MQTT: " + topic + " = " + msg)

        # Handle HA birth message
        if topic == "homeassistant/status" and msg == "online":
            logger.info("Home Assistant birth message received, re-announcing...")
            restart_thread = threading.Thread(target=sendStartupInfo)
            restart_thread.daemon = True
            restart_thread.start()
            return

        # Parse topic: svenson/{device_id}/{entity}/command
        parts = topic.split("/")
        position = 0
        if len(parts) == 4 and parts[0] == "svenson" and parts[3] == "command":
            device_id = get_device_id()
            rcvd_device_id = parts[1]
            command = parts[2]

            if device_id != rcvd_device_id:
                pass  # Not for me!
            elif command == "preset":
                command = msg  # Works for M1, M2 & TV
                if msg == "Zero G":
                    command = "zeroG"
                elif msg == "Anti Snore":
                    command = "antiSnore"
                elif msg == "Flat":
                    command = "flat"
                sendMQTT("preset", "Select Preset Positions")
            elif command == "massage":
                command = "massageOnOff"
            elif command in ["head", "feet", "tilt", "lumbar"]:
                position = int(10000 + (int(msg) / 100 * config["max_" + command]))
                if position >= svensonState[command]:
                    command = command + "Up"
                else:
                    command = command + "Down"
            elif command == "massageHead":
                if int(msg.split(" ")[1]) == 0:
                    svensonState["massageHead"] = 3
                elif int(msg.split(" ")[1]) == 1:
                    svensonState["massageHead"] = 0
                else:
                    svensonState["massageHead"] = 0
                    sendMessageToSvenson("massageHead")
                    svensonState["massageHead"] = int(msg.split(" ")[1]) - 1
            elif command == "massageFeet":
                if int(msg.split(" ")[1]) == 0:
                    svensonState["massageFeet"] = 3
                elif int(msg.split(" ")[1]) == 1:
                    svensonState["massageFeet"] = 0
                else:
                    svensonState["massageFeet"] = 0
                    sendMessageToSvenson("massageFeet")
                    svensonState["massageFeet"] = int(msg.split(" ")[1]) - 1

            try:
                if (device_id == rcvd_device_id) and (command in cmdTypes.keys()):
                    logger.info("processing Command \"" + command + "\"")
                    if svensonContinuous["active"] == True:
                        if svensonContinuous["cmd"] == command:
                            logger.info("Ignore command as it's still active")
                        else:
                            logger.info("Stop Previous command: " + svensonContinuous["cmd"])
                            if isContinuousOperationInProgress(command):
                                sendMessageToSvenson("cancel")
                            sendMessageToSvenson(command, None if (command not in svensonContinuousCmd) else position)
                    else:
                        sendMessageToSvenson(command, None if (command not in svensonContinuousCmd) else position)
                elif command == "Select Preset Positions":
                    pass
                else:
                    logger.warning("RECEIVED UNKNOWN COMMAND FROM MQTT: " + command + ". Ignoring this command")
            except Exception as e1:
                logger.error("Error in Process MQTT Command: " + command + ": " + str(e1))

    except Exception as e1:
        logger.critical("Exception Occurred: " + str(e1))

    logger.info("finishing receiveMessageFromMQTT")

def sendStartupState():
    global config
    device_id = get_device_id()
    mac = get_mac_address()
    web_url = get_web_url()

    logger.info("Send Discovery Messages")

    # Build device discovery payload
    payload = {
        "dev": {
            "ids": [mac],
            "name": config["name"],
            "mf": "Sven & Son / Richmat",
            "mdl": "HJC9",
            "sw": "2.0"
        },
        "o": {
            "name": "svenson-mqtt-bridge",
            "sw": "2.0"
        },
        "avty": {
            "t": "svenson/{}/availability".format(device_id)
        },
        "cmps": {
            "head": {
                "p": "number",
                "name": "Head",
                "uniq_id": "svenson_{}_head".format(mac),
                "min": 0,
                "max": 100,
                "cmd_t": "svenson/{}/head/command".format(device_id),
                "stat_t": "svenson/{}/head/state".format(device_id),
                "ic": "mdi:swap-vertical"
            },
            "feet": {
                "p": "number",
                "name": "Feet",
                "uniq_id": "svenson_{}_feet".format(mac),
                "min": 0,
                "max": 100,
                "cmd_t": "svenson/{}/feet/command".format(device_id),
                "stat_t": "svenson/{}/feet/state".format(device_id),
                "ic": "mdi:swap-vertical"
            },
            "tilt": {
                "p": "number",
                "name": "Tilt",
                "uniq_id": "svenson_{}_tilt".format(mac),
                "min": 0,
                "max": 100,
                "cmd_t": "svenson/{}/tilt/command".format(device_id),
                "stat_t": "svenson/{}/tilt/state".format(device_id),
                "ic": "mdi:swap-vertical"
            },
            "lumbar": {
                "p": "number",
                "name": "Lumbar",
                "uniq_id": "svenson_{}_lumbar".format(mac),
                "min": 0,
                "max": 100,
                "cmd_t": "svenson/{}/lumbar/command".format(device_id),
                "stat_t": "svenson/{}/lumbar/state".format(device_id),
                "ic": "mdi:swap-vertical"
            },
            "light": {
                "p": "switch",
                "name": "Light",
                "uniq_id": "svenson_{}_light".format(mac),
                "cmd_t": "svenson/{}/light/command".format(device_id),
                "stat_t": "svenson/{}/light/state".format(device_id),
                "ic": "mdi:lightbulb-on"
            },
            "massage": {
                "p": "switch",
                "name": "Massage",
                "uniq_id": "svenson_{}_massage".format(mac),
                "cmd_t": "svenson/{}/massage/command".format(device_id),
                "stat_t": "svenson/{}/massage/state".format(device_id),
                "ic": "mdi:vibrate"
            },
            "preset": {
                "p": "select",
                "name": "Preset",
                "uniq_id": "svenson_{}_preset".format(mac),
                "cmd_t": "svenson/{}/preset/command".format(device_id),
                "stat_t": "svenson/{}/preset/state".format(device_id),
                "ops": ["Select Preset Positions", "Flat", "M1", "M2", "TV", "Zero G", "Anti Snore"],
                "optimistic": False
            },
            "massage_head": {
                "p": "select",
                "name": "Massage Head",
                "uniq_id": "svenson_{}_massageHead".format(mac),
                "cmd_t": "svenson/{}/massageHead/command".format(device_id),
                "stat_t": "svenson/{}/massageHead/state".format(device_id),
                "ops": ["Level 0", "Level 1", "Level 2", "Level 3"],
                "ic": "mdi:vibrate"
            },
            "massage_feet": {
                "p": "select",
                "name": "Massage Feet",
                "uniq_id": "svenson_{}_massageFeet".format(mac),
                "cmd_t": "svenson/{}/massageFeet/command".format(device_id),
                "stat_t": "svenson/{}/massageFeet/state".format(device_id),
                "ops": ["Level 0", "Level 1", "Level 2", "Level 3"],
                "ic": "mdi:vibrate"
            }
        }
    }

    if web_url:
        payload["dev"]["cu"] = web_url

    # Publish device discovery
    sendRawMQTT("homeassistant/device/{}/config".format(device_id), json.dumps(payload))

    # Clean up old per-entity discovery topics
    old_entities = [
        "select/{}_preset", "select/{}_massageHead", "select/{}_massageFeet",
        "switch/{}_light", "switch/{}_massage",
        "number/{}_head", "number/{}_feet", "number/{}_tilt", "number/{}_lumbar"
    ]
    for entity in old_entities:
        sendRawMQTT("homeassistant/{}/config".format(entity.format(device_id)), "")



###################################################################
###################################################################
## Web Server
###################################################################
###################################################################
 
def add_header(r):
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, public, max-age=0"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"
    if config.get("UseHttps"):
        r.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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
            return Response(json.dumps(result), status=200, content_type='application/json')
        elif (command == "cancelHold"):
            logger.info("processing Command \"" + command + "\"")

            if (svensonContinuous["active"] == True):    
                sendMessageToSvenson("cancel")
            result = {};
            return Response(json.dumps(result), status=200, content_type='application/json')
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
            return Response(json.dumps(result), status=200, content_type='application/json')
        elif (command == "status"):
            logger.info("processing Command \"status\"")
            result = getStatus();
            return Response(json.dumps(result), status=200, content_type='application/json')
        else:
            logger.warning("UNKNOWN COMMAND " + command)
            result = {"error": "Unknown Command: " + command}
            return Response(json.dumps(result), status=400, content_type='application/json')
    except Exception as e1:
        logger.error("Error in Process Command: " + command + ": " + str(e1))
        result = {"error": "Exception occurred"}
        return Response(json.dumps(result), status=400, content_type='application/json')

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
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS)
    ctx.load_cert_chain(cert_file, pkey_file)
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def render_index():
    try:
        with open(os.path.join(curr_path, "html", "index.html"), "r", encoding="utf-8") as f:
            content = f.read()
        safe_name = html_mod.escape(config["name"])
        csrf_token = ""
        token = request.cookies.get('auth_token')
        if token and token in auth_sessions:
            csrf_token = auth_sessions[token]["csrf"]
        content = content.replace("%%NAME%%", safe_name)
        content = content.replace("%%CSRF_TOKEN%%", csrf_token)
        return content
    except Exception as e:
        logger.error(f"Error loading index: {e}")
        return Response("Internal server error", status=500)
        
def cleanup_sessions():
    now = time.time()
    expired = [k for k, v in auth_sessions.items() if v["expires"] < now]
    for k in expired:
        del auth_sessions[k]
    expired_ips = [ip for ip, v in login_attempts.items() if v["lockout_until"] < now]
    for ip in expired_ips:
        del login_attempts[ip]

def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        global server_public_ip, server_public_ip_last_check
        remote_ip = request.remote_addr
        logger.info(f"[AUTH] Remote IP: {remote_ip}, Server IP: {server_public_ip}")

        # Refresh public IP only if remote IP doesn't match what we believe is our own
        if config["BypassAuthForOwnNetwork"]:
            if remote_ip != server_public_ip:
                now = time.time()
                if now - server_public_ip_last_check > 300:
                    server_public_ip_last_check = now
                    try:
                        server_public_ip = requests.get("https://ifconfig.me", timeout=2).text.strip()
                        logger.info(f"[AUTH] Refreshed public IP: {server_public_ip}")
                    except Exception as e:
                        logger.warning(f"[AUTH] Could not refresh public IP: {e}")
            
            if remote_ip == server_public_ip:
                logger.info("[AUTH] Bypassing auth: client IP matches current public IP.")
                return f(*args, **kwargs)

        cleanup_sessions()
        token = request.cookies.get('auth_token')
        if not token or token not in auth_sessions:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return "Unauthorized", 401
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return wrapper

def require_csrf(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.cookies.get('auth_token')
        if token and token in auth_sessions:
            csrf_sent = request.headers.get('X-CSRF-Token', '')
            csrf_expected = auth_sessions[token]["csrf"]
            if not hmac.compare_digest(csrf_sent, csrf_expected):
                logger.warning("[CSRF] Token mismatch")
                return Response(json.dumps({"error": "CSRF token invalid"}), status=403, content_type='application/json')
        return f(*args, **kwargs)
    return wrapper

def require_ajax(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
            return Response(json.dumps({"error": "Invalid request"}), status=403, content_type='application/json')
        return f(*args, **kwargs)
    return wrapper

            
def login():
    """Display login form and handle password submission."""
    if request.method == 'POST':
        remote_ip = request.remote_addr
        now = time.time()
        if remote_ip in login_attempts and login_attempts[remote_ip]["lockout_until"] > now:
            error = "Too many attempts. Please wait 60 seconds."
            return render_template('login.html', error=error), 429
        entered_password = request.form.get('password', '')
        if hmac.compare_digest(entered_password, config["Password"]):
            login_attempts.pop(remote_ip, None)
            redirect_target = request.args.get('next', '')
            if not redirect_target or not redirect_target.startswith('/') or redirect_target.startswith('//'):
                redirect_target = url_for('index')
            resp = make_response(redirect(redirect_target))
            token = secrets.token_hex(32)
            csrf_token = secrets.token_hex(32)
            auth_sessions[token] = {"expires": time.time() + 86400, "csrf": csrf_token}
            cleanup_sessions()
            resp.set_cookie('auth_token', token, httponly=True, secure=True, samesite='Strict', max_age=86400)
            return resp
        else:
            attempts = login_attempts.get(remote_ip, {"count": 0, "lockout_until": 0})
            attempts["count"] += 1
            if attempts["count"] >= 5:
                attempts["lockout_until"] = now + 60
                attempts["count"] = 0
            login_attempts[remote_ip] = attempts
            error = "Invalid password, please try again."
    else:
        error = None
    return render_template('login.html', error=error)

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
    except (singleton.SingleInstanceException, SystemExit):
       logging.getLogger().setLevel(logging.INFO)
       logger.info("Another instance is already running. quiting...")
       exit()

    # Now read the config file
    parser = argparse.ArgumentParser(description='Sven & Son (Richmat Qingdao HJC9) to MQTT Integration for Home Assistant.')
    parser.add_argument('-config', '-c', dest='ConfigFile', default=curr_path+'/svenson-mqtt-bridge.conf', help='Name of the Config File (incl full Path)')
    args = parser.parse_args()

    if args.ConfigFile is None:
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
       if config["HttpLocalOnly"]:
           kwargs = {'host':"127.0.0.1", 'threaded':True, 'port':config["HTTPPort"], 'use_reloader':False, 'debug':False}
       else:
           kwargs = {'host':"0.0.0.0", 'threaded':True, 'port':config["HTTPPort"], 'use_reloader':False, 'debug':False}
       
    if (len(config["Password"]) > 2):
        webapp.add_url_rule('/login', 'login', login, methods=['GET', 'POST'])
        webapp.add_url_rule('/', 'index', require_auth(render_index))
        webapp.add_url_rule('/index.html', 'index_full', require_auth(render_index))       
        webapp.add_url_rule('/cmd/<command>', 'cmd', require_ajax(require_csrf(require_auth(processCommand))), methods=['POST'])
    else:
        webapp.add_url_rule('/', 'index', render_index)
        webapp.add_url_rule('/index.html', 'index_full', render_index)       
        webapp.add_url_rule('/cmd/<command>', 'cmd', require_ajax(processCommand), methods=['POST'])
    
        

    log = logging.getLogger('werkzeug')
    log.disabled = True
    ## webapp.logger.removeHandler(default_handler)
    ## webapp.logger.addHandler(rotatingHandler)
    # Webserver_thread = threading.Thread(target=webapp.run, daemon=True, kwargs=kwargs).start()
    # Start Flask in a thread (only if integrating into a larger app)
    def run_webserver():
       webapp.run(**kwargs)        
       
    Webserver_thread = threading.Thread(target=run_webserver, daemon=True)
    Webserver_thread.start()
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
        t = paho.Client(client_id="svenson-mqtt-bridge_"+get_mac_address())
        t.username_pw_set(username=config["MQTT_User"],password=config["MQTT_Password"])
        t.will_set("svenson/{}/availability".format(get_device_id()), "offline", retain=True)
        t.on_connect = on_connect
        t.on_message=receiveMessageFromMQTT
        t.connect(config["MQTT_Server"],config["MQTT_Port"])
        logger.info("Connected to MQTT on "+config["MQTT_Server"]+":"+str(config["MQTT_Port"]))

        logger.info("Starting Listener Thread to listen to messages from MQTT")
        t.loop_start()

    def shutdown(signum, frame):
        logger.info("Received signal %s, shutting down..." % signum)
        if config["MQTT_Server"] and config["MQTT_Server"].strip():
            sendRawMQTT("svenson/{}/availability".format(get_device_id()), "offline")
            t.disconnect()
            t.loop_stop()
            logger.info("Stop Listener Thread to listen to messages from MQTT")
        pi.bb_serial_read_close(RX)
        pi.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
       time.sleep(60)

    
    