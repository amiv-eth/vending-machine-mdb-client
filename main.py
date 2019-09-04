import time
import pigpio
from MDBHandler import MDBHandler

RXD=4 # number of GPIO

pi = pigpio.pi()

if not pi.connected:
    print('pigpio not connected!')
    exit(0)

mdb = MDBHandler(pi, RXD)

pigpio.exceptions = True

stop = time.time() + 10.0 # recording 10.0 seconds

print('MDBHandler: start!')

while time.time() < stop:
    mdb.run()

mdb.stop()

print('MDBHandler: stopped!')

pi.stop()
