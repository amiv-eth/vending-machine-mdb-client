import time
import pigpio # http://abyz.me.uk/rpi/pigpio/python.html

RXD=4 # number of GPIO

pi = pigpio.pi()

if not pi.connected:
    exit(0)

pigpio.exceptions = False # Ignore error if already set as bit bang read.

handle = pi.file_open("/home/pi/Documents/bit_bang_output.txt",pigpio.FILE_WRITE) #assuming that the file /opt/pigpio/access (yes without extension) contains a line /home/pi/Domcuments/* w

pi.bb_serial_read_open(RXD, 9600,9) # Set baud rate and number of data bits here. Reading 9 data bits will read the parity bit.

pigpio.exceptions = True

stop = time.time() + 10.0 # recording 10.0 seconds

while time.time() < stop:
    (count, data) = pi.bb_serial_read(RXD)
    if count:
        # print(data.hex(),end="")
        print(data.hex())
        # pi.file_write(handle, data.hex())

pi.bb_serial_read_close(RXD)

pi.stop()
