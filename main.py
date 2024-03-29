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
stop = time.time() + 30.0 # recording 10.0 seconds
state = MDBState.RESET
sendSessionCloseTime = None
sessionCloseSent = False

print('MDBHandler: start!')

while not finished and time.time() < stop:
    mdb.run()

    newState = mdb.get_state()

    if newState != state:
        if newState == MDBState.ENABLED:
            if sessionCloseSent:
                finished = True
            else:
                mdb.session_open()
        elif newState == MDBState.SESSION_IDLE:
            mdb.session_display_request(b'\x00')
            sendSessionCloseTime = time.time() + 6
        state = newState

    if newState == MDBState.SESSION_IDLE and time.time() >= sendSessionCloseTime and not sessionCloseSent:
        mdb.session_close()
        sessionCloseSent = True

mdb.stop()

print('MDBHandler: stopped!')

pi.stop()
