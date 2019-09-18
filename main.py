import time
import pigpio
from threading import Condition
from mdb import MDBHandler, MDBState

RXD =  4 # number of GPIO
TXD = 17 # number of GPIO

pi = pigpio.pi()

if not pi.connected:
    print('pigpio not connected!')
    exit(0)

condition = Condition()

mdb = MDBHandler(pi, RXD, TXD, condition)

pigpio.exceptions = True

finished = False
stop = time.time() + 20.0 # recording 20.0 seconds
state = MDBState.RESET
sendSessionCloseTime = None
sessionCloseSent = False

print('MDBHandler: start!')

mdb.start()

try:
    with condition:
        while not finished and time.time() < stop:
            condition.wait()
            newState = mdb.get_state()

            if newState != state:
                print('===== MDBState changed from ' + str(state) + ' to ' + str(newState) + ' =====')
                if newState == MDBState.ENABLED:
                    if sessionCloseSent:
                        finished = True
                    else:
                        mdb.open_session()
                        # mdb.open_session(b'Sorry, heute'.center(16) +
                        #     (b'kein Freibier!').center(16), 5000)
                        # sendSessionCloseTime = time.time() + 6
                elif newState == MDBState.SESSION_IDLE:
                    mdb.update_display(b'Sorry, heute'.center(16) +
                            (b'kein Freibier!').center(16))
                    sendSessionCloseTime = time.time() + 6
                state = newState

            if newState == MDBState.SESSION_IDLE and time.time() >= sendSessionCloseTime and not sessionCloseSent:
                mdb.close_session()
                sessionCloseSent = True
except KeyboardInterrupt:
    print("== Stopping due to user request! ==")

mdb.stop()
mdb.join()

print('MDBHandler: stopped!')

pi.stop()
