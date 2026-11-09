import numpy as np

# -----------------------------
# Wi-Fi OFDM Parameters
# -----------------------------
FFT_SIZE = 64               # 20 MHz Wi-Fi
CP_LEN = 16
NUM_DATA_SUBCARRIERS = 52

NUM_LTF = 8                 # Repeated LTFs for sensing
NUM_DATA_SYMBOLS = 20

# -----------------------------
# OFDM Modulator
# -----------------------------
def ofdm_symbol(freq):
    time = np.fft.ifft(np.fft.ifftshift(freq))
    cp = time[-CP_LEN:]
    return np.concatenate((cp, time))

# -----------------------------
# Generate STF (simplified)
# -----------------------------
def generate_stf():
    freq = np.zeros(FFT_SIZE, dtype=complex)

    active = np.r_[6:32,33:59]
    freq[active] = 1

    return ofdm_symbol(freq)

# -----------------------------
# Generate LTF
# -----------------------------
def generate_ltf():

    freq = np.zeros(FFT_SIZE, dtype=complex)

    active = np.r_[6:32,33:59]

    bpsk = np.random.choice([-1,1],len(active))

    freq[active] = bpsk

    return ofdm_symbol(freq)

# -----------------------------
# Generate Known OFDM Data
# -----------------------------
def generate_known_symbol():

    freq = np.zeros(FFT_SIZE,dtype=complex)

    active = np.r_[6:32,33:59]

    qpsk = (2*np.random.randint(0,2,len(active))-1) \
          +1j*(2*np.random.randint(0,2,len(active))-1)

    qpsk /= np.sqrt(2)

    freq[active]=qpsk

    return ofdm_symbol(freq)

# -----------------------------
# Build sensing PPDU
# -----------------------------
frame = []

# STF
frame.append(generate_stf())

# LTFs
for _ in range(NUM_LTF):
    frame.append(generate_ltf())

# Known data symbols
for _ in range(NUM_DATA_SYMBOLS):
    frame.append(generate_known_symbol())

frame = np.concatenate(frame)

print("Frame length:",len(frame))
print("Complex samples:",frame.shape)

# Save IQ samples
frame.astype(np.complex64).tofile("wifi_bistatic_frame.cf32")

print("Saved to wifi_bistatic_frame.cf32")
