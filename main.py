import time
import pigpio
from MDBHandler import MDBHandler, MDBState

RXD =  4 # number of GPIO
TXD = 17 # number of GPIO

pi = pigpio.pi()

if not pi.connected:
    print('pigpio not connected!')
    exit(0)

mdb = MDBHandler(pi, RXD, TXD)

pigpio.exceptions = True

finished = False
stop = time.time() + 10.0 # recording 10.0 seconds
state = MDBState.RESET
sendSessionCloseTime = None
sessionCloseSent = False

print('MDBHandler: start!')

while not finished and time.time() < stop:
    mdb.run()

mdb.stop()

print('MDBHandler: stopped!')

pi.stop()
