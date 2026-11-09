#!/usr/bin/env python3

import uhd
import numpy as np
import time

# --------------------------------------------------
# Configuration
# --------------------------------------------------
CENTER_FREQ = 5.18e9          # Example: Wi-Fi channel 36
SAMPLE_RATE = 20e6
TX_GAIN = 30
ANTENNA = "TX/RX"

IQ_FILE = "wifi_bistatic_frame.cf32"

# --------------------------------------------------
# Create USRP
# --------------------------------------------------
usrp = uhd.usrp.MultiUSRP()

usrp.set_tx_rate(SAMPLE_RATE)
usrp.set_tx_freq(CENTER_FREQ)
usrp.set_tx_gain(TX_GAIN)
usrp.set_tx_antenna(ANTENNA)

print("Actual rate:", usrp.get_tx_rate())
print("Actual freq:", usrp.get_tx_freq())
print("Actual gain:", usrp.get_tx_gain())

# --------------------------------------------------
# TX Stream
# --------------------------------------------------
st_args = uhd.usrp.StreamArgs("fc32", "sc16")
tx_stream = usrp.get_tx_stream(st_args)

metadata = uhd.types.TXMetadata()
metadata.start_of_burst = True
metadata.end_of_burst = False
metadata.has_time_spec = False

# --------------------------------------------------
# Load waveform
# --------------------------------------------------
waveform = np.fromfile(IQ_FILE, dtype=np.complex64)

print("Loaded", len(waveform), "samples")

# --------------------------------------------------
# Continuous transmission
# --------------------------------------------------
print("Starting transmission...")

while True:

    tx_stream.send(waveform, metadata)

    metadata.start_of_burst = False
