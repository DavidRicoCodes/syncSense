import uhd
import numpy as np


# Connect to X410
usrp = uhd.usrp.MultiUSRP(
    "type=x4xx,addr=192.168.10.2"
)

# Clock and timestamp configuration
usrp.set_clock_source("internal")
usrp.set_time_source("internal")
usrp.set_time_now(uhd.types.TimeSpec(0.0))


# Receiver configuration
RATE = 30.72e6
GAIN = 40

usrp.set_rx_rate(RATE, 0)
usrp.set_rx_rate(RATE, 1)

usrp.set_rx_freq(2.5e9, 0)
usrp.set_rx_freq(3.8e9, 1)

usrp.set_rx_gain(GAIN, 0)
usrp.set_rx_gain(GAIN, 1)


# Create multi-channel streamer
stream_args = uhd.usrp.StreamArgs(
    "fc32",
    "sc16"
)

stream_args.channels = [0, 1]

rx_stream = usrp.get_rx_stream(stream_args)


# Buffer
NSAMPLES = 100000

samples = np.zeros(
    (2, NSAMPLES),
    dtype=np.complex64
)

metadata = uhd.types.RXMetadata()


# Start streaming at known FPGA time
cmd = uhd.types.StreamCMD(
    uhd.types.StreamMode.start_cont
)

cmd.stream_now = False

cmd.time_spec = (
    usrp.get_time_now()
    + uhd.types.TimeSpec(0.5)
)

rx_stream.issue_stream_cmd(cmd)


print("Receiving...")


while True:

    n = rx_stream.recv(
        samples,
        metadata
    )

    if metadata.error_code != uhd.types.RXMetadataErrorCode.none:
        print(metadata.strerror())
        continue

    timestamp = metadata.time_spec.get_real_secs()

    print(
        f"time={timestamp:.9f}s samples={n}"
    )
