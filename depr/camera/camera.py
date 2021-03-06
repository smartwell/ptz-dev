from threading import Thread
from threading import Lock
import time
from time import sleep, time
import cv2
import re
import binascii
import socket
import fcntl
import os, sys
import errno
import numpy as np

class Camera:

    def __init__(self, pan_controller, tilt_controller, zoom_controller, usbdevnum=0, width=1280, height=720, host='192.168.2.42', tcp_port=5678, udp_port=1259):

        # Camera params
        self.width = width
        self.height = height
        
        # Open video stream as CV camera
        self.cvcamera = cv2.VideoCapture(usbdevnum)
        self.cvcamera.set(3, width)
        self.cvcamera.set(4, height)
        self.cvreader = CameraReaderAsync(self.cvcamera)

        # Connect to PTZOptics camera for controls
        self.ptz = PTZOptics20x(host=host, tcp_port=5678, udp_port=1259).init()
        self.pan_controller = pan_controller
        self.tilt_controller = tilt_controller
        self.zoom_controller = zoom_controller

    def stop(self):
        self.cvreader.Stop()
        self.cvcamera.release()
        self.ptz.stop()

    def control(self, pan_error, tilt_error):

        dur = 0.001

        pan_command = self.pan_controller.compute(pan_error) # positive means turn left
        tilt_command = self.tilt_controller.compute(tilt_error) # positive means move up

        pan_speed = self.limit(pan_command, 24)  # max speed for pan is 24
        tilt_speed = self.limit(tilt_command, 18)  # max speed for titlt is 18

        if pan_speed == 0 and tilt_speed == 0:
            self.ptz.stop()
            sleep(dur)

        elif pan_speed == 0:
            if tilt_command == abs(tilt_command):
                self.ptz.up(tilt_speed)
                sleep(dur)
            else:
                self.ptz.down(tilt_speed)
                sleep(dur)
        elif tilt_speed == 0:
            if pan_command == abs(pan_command):
                self.ptz.left(pan_speed)
                sleep(dur)
            else:
                self.ptz.right(pan_speed)
                sleep(dur)

        elif abs(pan_command) == pan_command and abs(tilt_command) == tilt_command:
            self.ptz.left_up(pan_speed, tilt_speed)
            sleep(dur)

        elif abs(pan_command) == pan_command and abs(tilt_command) != tilt_command:
            self.ptz.left_down(pan_speed, tilt_speed)
            sleep(dur)

        elif abs(pan_command) != pan_command and abs(tilt_command) == tilt_command:
            self.ptz.right_up(pan_speed, tilt_speed)
            sleep(dur)

        elif abs(pan_command) != pan_command and abs(tilt_command) != tilt_command:
            self.ptz.right_down(pan_speed, tilt_speed)
            sleep(dur)

        return pan_speed, tilt_speed

    @staticmethod
    def errors_pt(center, width, height):
        return (width//2 - center[0])/50, (height//2 - center[1])/30 # Pos. pan error: right. Pos. tilt error: down

    @staticmethod
    def limit(val, max):
        return max if abs(val) > max else abs(int(val))

    def control_zoom(self, error):

        dur = 0.001

        zoom_command = self.zoom_controller.compute(error) # positive means zoom in
        zoom_speed = self.limit(zoom_command, 1)

        if not zoom_speed:
            self.ptz.zoomstop()
            sleep(dur)

        if zoom_command > 0:
            self.ptz.zoomin(zoom_speed)
            sleep(dur)
        elif zoom_command < 0:
            self.ptz.zoomout(zoom_speed)
            sleep(dur)

        return zoom_speed

    @staticmethod
    def error_zoom(size, height):
        target_size = float(height)/3.0  # no specific reason for 3
        return (target_size - size)/30  # no specific reason for 30


class CameraReaderAsync:

    class WeightedFramerateCounter:
        smoothing = 0.95
        startTime = 0
        framerate = 0

        def start(self):
            self.startTime = time()
            self.framerate = 0

        def tick(self):
            timeNow = time()
            if self.startTime == 0:
                self.startTime = timeNow
                return
            elapsed = 1.0 / (timeNow - self.startTime)
            self.startTime = timeNow
            self.framerate = (self.framerate * self.smoothing) + (elapsed * (1.0 - self.smoothing))

        def getFramerate(self):
            return self.framerate

    def __init__(self, videoSource):
        self.__lock = Lock()
        self.__source = videoSource
        self.Start()

    def __ReadAsync(self):
        while True:
            if self.__stopRequested:
                return
            validFrame, frame = self.__source.read()
            if validFrame:
                try:
                    self.__lock.acquire()
                    self.fps.tick()
                    self.__frame = frame
                    self.__lastFrameRead = False
                finally:
                    self.__lock.release()

    def Start(self):
        self.__lastFrameRead = False
        self.__frame = None
        self.__stopRequested = False
        self.__validFrame = False
        self.fps = CameraReaderAsync.WeightedFramerateCounter()
        Thread(target=self.__ReadAsync).start()

    def Stop(self):
        self.__stopRequested = True

    # Return a frame if we have a new frame since this was last called.
    # If there is no frame or if the frame is not new, return None.
    def Read(self):
        try:
            self.__lock.acquire()
            if not self.__lastFrameRead:
                frame = self.__frame
                self.__lastFrameRead = True
                return frame

            return None
        finally:
            self.__lock.release()

    # Return the last frame read even if it has been retrieved before.
    # Will return None if we never read a valid frame from the source.
    def ReadLastFrame(self):
        return self.__frame

class TCPCamera(object):


    def __init__(self, host, tcp_port=5678, udp_port=1259):
        """PTZOptics VISCA control class.

        :param host: TCP control host.
        :type host: str
        :param port: TCP control port.
        :type port: int
        """
        self._host = host
        self._tcp_port = tcp_port
        self._udp_port = udp_port

    def init(self):
        """Initializes camera object by establishing TCP control session.

        :return: Camera object.
        :rtype: TCPCamera
        """
        print("Connecting to camera...")
        # self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # self._socket.setblocking(0)
        self._tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._tcp_socket.settimeout(0.6)
        self._udp_socket.settimeout(0.6)
        try:
            self._tcp_socket.connect((self._host, self._tcp_port))
        except:
            print("Could not connect to camera on tcp channel")
            return None
        try:
            self._udp_socket.connect((self._host, self._udp_port))
        except:
            print("Could not connect to camera on udp channel")
            return None
        print("Camera connected")
        self._tcp_socket.settimeout(0.2)
        self._udp_socket.settimeout(0.2)
        return self

    def command(self, com, channel):
        """Sends hexadecimal string to TCP control socket.

        :param com: Command string. Hexadecimal format.
        :type com: str
        :return: Success.
        :rtype: bool
        """
        if channel == "udp":
            try:
                self._udp_socket.send(binascii.unhexlify(com))
                return True
            except Exception as e:
                print(com, e)
                return False
        if channel == "tcp":
            try:
                self._tcp_socket.send(binascii.unhexlify(com))
                return True
            except Exception as e:
                print(com, e)
                return False

    def read(self, amount=1):
        total = ""
        while True:
            try:
                msg = binascii.hexlify(self._tcp_socket.recv(amount))
            except socket.timeout:
                print("No data from camera socket")
                break
            except socket.error:
                print("Camera socket read error.")
                break
            total = total + msg.decode("utf-8")
            if total.endswith("ff"):
                break
        return total

    def end(self):
        self._tcp_socket.close()
        self._udp_socket.close()

class PTZOptics20x(TCPCamera):
    """PTZOptics VISCA control class.

    Tested with USB 20X model.
    """
    # Pan/tilt continuing motion
    _ptContinuousMotion = False
    # Continuous zoom change initiated
    _zContinuous = False

    def __init__(self, host, tcp_port=5678, udp_port=1259):
        """Sony VISCA control class.

        :param host: TCP control host or IP address
        :type host: str
        :param port: TCP control port
        :type port: int
        """
        super(self.__class__, self).__init__(host, tcp_port=tcp_port, udp_port=udp_port)

    def init(self):
        """Initializes camera object by connecting to TCP control socket.

        :return: Camera object.
        :rtype: TCPCamera
        """
        if super(self.__class__, self).init() is None:
            return None
        print("Camera controller initialized")
        return self

    def panTiltOngoing(self):
        return True if self._ptContinuousMotion else False

    def zoomOngoing(self):
        return True if self._zContinuous else False

    def comm(self, com, channel):
        """Sends hexadecimal string to control socket.

        :param com: Command string. Hexadecimal format.
        :type com: str
        :return: Success.
        :rtype: bool
        """
        super(self.__class__, self).command(com, channel)

    @staticmethod
    def multi_replace(text, rep):
        """Replaces multiple parts of a string using regular expressions.

        :param text: Text to be replaced.
        :type text: str
        :param rep: Dictionary of key strings that are replaced with value strings.
        :type rep: dict
        :return: Replaced string.
        :rtype: str
        """
        rep = dict((re.escape(k), v) for k, v in rep.iteritems())
        pattern = re.compile("|".join(rep.keys()))
        return pattern.sub(lambda m: rep[re.escape(m.group(0))], text)

    def get_zoom_position(self):
        """Retrieves current zoom position.
        Zoom is 0 to 16384

        :return: Zoom distance
        :rtype: int
        """
        self.comm('81090447FF', 'tcp')
        msg = self.read()[4:-2]
        r = ""
        if len(msg) == 8:
            for x in range(1, 9, 2):
                r += msg[x]
            x = int(r, 16)
            return x, True
        return -1, False

    def get_pan_tilt_position(self):
        """Retrieves current pan/tilt position.
        Pan is 0 at home. Right is positive, max 2448. Left ranges from full left 63088 to 65555 before home.
        Tilt is 0 at home. Up is positive, max 1296. Down ranges from fully depressed at 65104 to 65555 before home.

        :return: pan position
        :rtype: int
        :return: tilt position
        :rtype: int
        """
        self.comm('81090612FF', 'tcp')
        msg = self.read()[4:-2]
        r = ""
        if len(msg) == 16:
            for x in range(1, 9, 2):
                r += msg[x]
            pan = int(r, 16)
            r = ""
            for x in range(9, 17, 2):
                r += msg[x]
            tilt = int(r, 16)
            return pan, tilt, True
        return -1,-1, False

    def home(self):
        """Moves camera to home position.

        :return: True if successful, False if not.
        :rtype: bool
        """
        # Since home is not continuing motion, we'll call it a stop
        self._ptContinuousMotion = False
        return self.comm('81010604FF', 'udp')

    def reset(self):
        """Resets camera.

        :return: True if successful, False if not.
        :rtype: bool
        """
        self._ptContinuousMotion = False
        self._zContinuous = False
        return self.comm('81010605FF', 'udp')

    def stop(self):
        """Stops camera movement (pan/tilt).

        :return: True if successful, False if not.
        :rtype: bool
        """
        self._ptContinuousMotion = False
        return self.comm('8101060115150303FF', 'udp')

    def cancel(self):
        """Cancels current command.

        :return: True if successful, False if not.
        :rtype: bool
        """
        self._ptContinuousMotion = False
        self._zContinuous = False
        return self.comm('81010001FF', 'udp')

    def _move(self, string, a1, a2):
        h1 = "%X" % a1
        h1 = '0' + h1 if len(h1) < 2 else h1

        h2 = "%X" % a2
        h2 = '0' + h2 if len(h2) < 2 else h2
        self._ptContinuousMotion = True
        return self.comm(string.replace('VV', h1).replace('WW', h2), 'udp')

    def goto(self, pan, tilt, speed=5):
        """Moves camera to absolute pan and tilt coordinates.

        :param speed: Speed (0-24)
        :param pan: numeric pan position
        :param tilt: numeric tilt position
        :return: True if successful, False if not.
        :rtype: bool
        """
        speed_hex = "%X" % speed
        speed_hex = '0' + speed_hex if len(speed_hex) < 2 else speed_hex

        pan_hex = "%X" % pan
        pan_hex = pan_hex if len(pan_hex) > 3 else ("0" * (4 - len(pan_hex))) + pan_hex
        pan_hex = "0" + "0".join(pan_hex)

        tilt_hex = "%X" % tilt
        tilt_hex = tilt_hex if len(tilt_hex) > 3 else ("0" * (4 - len(tilt_hex))) + tilt_hex
        tilt_hex = "0" + "0".join(tilt_hex)

        s = '81010602VVWWYYYYZZZZFF'.replace(
            'VV', speed_hex).replace(
            'WW', speed_hex).replace(
            'YYYY', pan_hex).replace(
            'ZZZZ', tilt_hex)

        # Not in continuing motion
        self._ptContinuousMotion = False

        return self.comm(s, 'udp')

    def gotoIncremental(self, pan, tilt, speed=5):
        """Moves camera to relative pan and tilt coordinates.

        :param speed: Speed (0-24)
        :param pan: numeric pan adjustment
        :param tilt: numeric tilt adjustment
        :return: True if successful, False if not.
        :rtype: bool
        """
        speed_hex = "%X" % speed
        speed_hex = '0' + speed_hex if len(speed_hex) < 2 else speed_hex

        pan_hex = "%X" % pan
        pan_hex = pan_hex if len(pan_hex) > 3 else ("0" * (4 - len(pan_hex))) + pan_hex
        pan_hex = "0" + "0".join(pan_hex)

        tilt_hex = "%X" % tilt
        tilt_hex = tilt_hex if len(tilt_hex) > 3 else ("0" * (4 - len(tilt_hex))) + tilt_hex
        tilt_hex = "0" + "0".join(tilt_hex)

        s = '81010603VVWWYYYYZZZZFF'.replace(
            'VV', speed_hex).replace(
            'WW', speed_hex).replace(
            'YYYY', pan_hex).replace(
            'ZZZZ', tilt_hex)

        # Not in continuing motion
        self._ptContinuousMotion = False

        return self.comm(s, 'udp')

    def zoomstop(self):
        """Halt the zoom motor

        :return: True on success, False on failure
        :rtype: bool
        """
        s = '8101040700FF'
        self._zContinuous = False
        return self.comm(s, 'udp')

    def zoomin(self, speed=0):
        """Initiate tele zoom at speed range 0-7

        :param speed: zoom speed, 0-7
        :return: True on success, False on failure
        :rtype: bool
        """
        if speed < 0 or speed > 7:
            return False
        s = '810104072pFF'.replace(
            'p', "{0:1s}".format(str(speed)))
        # print("zoomin comm string: " + s)
        self._zContinuous = True
        return self.comm(s, 'udp')

    def zoomout(self, speed=0):
        """Initiate tele zoom at speed range 0-7

        :param speed: zoom speed, 0-7
        :return: True on success, False on failure
        :rtype: bool
        """
        if speed < 0 or speed > 7:
            return False
        s = '810104073pFF'.replace(
            'p', "{0:1s}".format(str(speed)))
        # print("zoomout comm string: " + s)
        self._zContinuous = True
        return self.comm(s, 'udp')

    def zoomto(self, zoom):
        """Moves camera to absolute zoom setting.

        :param zoom: numeric zoom position
        :return: True if successful, False if not.
        :rtype: bool
        """
        zoom_hex = "%X" % zoom
        zoom_hex = zoom_hex if len(zoom_hex) > 3 else ("0" * (4 - len(zoom_hex))) + zoom_hex
        zoom_hex = "0" + "0".join(zoom_hex)


        s = '81010447pqrsFF'.replace(
            'pqrs', zoom_hex)
        return self.comm(s, 'udp')

    def left(self, amount=5):
        """Modifies pan speed to left.

        :param amount: Speed (0-24)
        :return: True if successful, False if not.
        :rtype: bool
        """
        hex_string = "%X" % amount
        hex_string = '0' + hex_string if len(hex_string) < 2 else hex_string
        s = '81010601VVWW0103FF'.replace('VV', hex_string).replace('WW', str(15))
        self._ptContinuousMotion = True
        return self.comm(s, 'udp')

    def right(self, amount=5):
        """Modifies pan speed to right.

        :param amount: Speed (0-24)
        :return: True if successful, False if not.
        """
        hex_string = "%X" % amount
        hex_string = '0' + hex_string if len(hex_string) < 2 else hex_string
        s = '81010601VVWW0203FF'.replace('VV', hex_string).replace('WW', str(15))
        self._ptContinuousMotion = True
        return self.comm(s, 'udp')

    def up(self, amount=5):
        """Modifies tilt speed to up.

        :param amount: Speed (0-24)
        :return: True if successful, False if not.
        """
        hs = "%X" % amount
        hs = '0' + hs if len(hs) < 2 else hs
        s = '81010601VVWW0301FF'.replace('VV', str(15)).replace('WW', hs)
        self._ptContinuousMotion = True
        return self.comm(s, 'udp')

    def down(self, amount=5):
        """Modifies tilt to down.

        :param amount: Speed (0-24)
        :return: True if successful, False if not.
        """
        hs = "%X" % amount
        hs = '0' + hs if len(hs) < 2 else hs
        s = '81010601VVWW0302FF'.replace('VV', str(15)).replace('WW', hs)
        self._ptContinuousMotion = True
        return self.comm(s, 'udp')

    def left_up(self, pan, tilt):
        return self._move('81010601VVWW0101FF', pan, tilt)

    def right_up(self, pan, tilt):
        return self._move('81010601VVWW0201FF', pan, tilt)

    def left_down(self, pan, tilt):
        return self._move('81010601VVWW0102FF', pan, tilt)

    def right_down(self, pan, tilt):
        return self._move('81010601VVWW0202FF', pan, tilt)


class PIDController:
    def __init__(self, kp, kd, ki, T, omega_c):
        self.past = {
            "err": 0.0,
            "diff": 0.0,
            "filt": 0.0,
            "integ": 0.0
        }
        self.kp = kp
        self.kd = kd
        self.ki = ki
        self.T = T
        self.omega_c = omega_c

        self.minval = -24
        self.maxval = 24

    # Controls equations - bilinear approximation
    @staticmethod
    def _df_bil(diff_1, err, err_1, T):
        return -diff_1 + (err - err_1) * (2 / T)
    @staticmethod
    def _filt_bil(filt_1, diff, diff_1, T, omega_c):
        return ((2 - T*omega_c)/(2 + T*omega_c))*filt_1 + T*omega_c*(diff + diff_1)/(2+T*omega_c)
    @staticmethod
    def _integ_bil(integ_1, err, err_1, T):
        return integ_1 + (err + err_1)*(T/2)

    def compute(self, err):
        diff = self._df_bil(self.past["diff"], err, self.past["err"], self.T)
        filt = self._filt_bil(self.past["filt"], diff, self.past["diff"], self.T, self.omega_c)
        integ = self._integ_bil(self.past["integ"], err, self.past["err"], self.T)

        pid_out = self.kp*err + self.kd*filt + self.ki*integ

        self.past["err"] = err
        self.past["diff"] = diff
        self.past["filt"] = filt

        if (pid_out > self.minval and pid_out < self.maxval):
            self.past["integ"] = integ

        return pid_out
