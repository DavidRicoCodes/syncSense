#!/usr/bin/env python3

import uhd
import numpy as np
import threading
import time

#############################
# Configuration
#############################

RATE = 30.72e6
GAIN = 40

FREQ0 = 2.5e9
FREQ1 = 3.8e9

NUM_SAMPS = 100000

ARGS = "type=x4xx"

#############################

usrp = uhd.usrp.MultiUSRP(ARGS)

# Shared clock
usrp.set_clock_source("internal")     # or "external"
usrp.set_time_source("internal")      # or "external"

# Reset FPGA time
usrp.set_time_now(uhd.types.TimeSpec(0.0))

#############################
# Configure Channel 0
#############################

usrp.set_rx_rate(RATE, 0)
usrp.set_rx_freq(FREQ0, 0)
usrp.set_rx_gain(GAIN, 0)

#############################
# Configure Channel 1
#############################

usrp.set_rx_rate(RATE, 1)
usrp.set_rx_freq(FREQ1, 1)
usrp.set_rx_gain(GAIN, 1)

#############################

st_args = uhd.usrp.StreamArgs("fc32", "sc16")
st_args.channels = [0, 1]

rx_stream = usrp.get_rx_stream(st_args)

metadata = uhd.types.RXMetadata()

buffer = np.zeros((2, NUM_SAMPS), dtype=np.complex64)

cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
cmd.stream_now = False
cmd.time_spec = usrp.get_time_now() + uhd.types.TimeSpec(0.1)

rx_stream.issue_stream_cmd(cmd)

while True:

    nsamps = rx_stream.recv(buffer, metadata)

    if metadata.error_code != uhd.types.RXMetadataErrorCode.none:
        print(metadata.strerror())
        continue

    timestamp = metadata.time_spec.get_real_secs()

    print(
        f"Timestamp = {timestamp:.9f} s   Samples = {nsamps}"
    )

    ch0 = buffer[0, :nsamps]
    ch1 = buffer[1, :nsamps]

    # Save or process here
