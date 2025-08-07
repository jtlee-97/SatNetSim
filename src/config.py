import math

# NOTE: SIMULATION CONFIG
SEED = 10 # Random Seed
DURATION = 100

# NOTE: ENTITIES CONFIG
NUMBER_UE = 1 # UE 단말 수
SATELLITE_R = 25 * 1000 # 위성 커버리지 반경 (m)
SATELLITE_V = 7.56 * 1000 # 위성 이동속도 (m/s)

# NOTE: NETWORK DELAY CONFIG
SATELLITE_GROUND_DELAY = 3 # Propagation Delay (ms)
SATELLITE_SATELLITE_DELAY = 1 # ISL Delay (ms)
CORE_DELAY = 10 # Core Network Delay (ms)

# NOTE: RE-TRANSMITION CONFIG
RETRANSMIT = True # Enable/Disable
RETRANSMIT_THRESHOLD = SATELLITE_GROUND_DELAY * 2 + SATELLITE_SATELLITE_DELAY * 2 + 22 # 재전송 임계값: 왕복지연 고려
MAX_RETRANSMIT = 15 # 최대 재전송 수

# NOTE: CPU CONFIG
QUEUED_SIZE = 500 # Satellite messageQ 최대 크기
SATELLITE_CPU = 4 # Satellite CPU 리소스 수
UE_CPU = 4 # UE CPU 리소스 수

# CHECK
GROUP_AREA_L = 1 * 1000 # This is to compare with group handover

# NOTE: Initial Position of Satellites
# TODO: 위성 시나리오에 맞게 수정해야함
HORIZONTAL_DISTANCE = 1.25 * SATELLITE_R # 위성 간 수평거리 (1.25 * 위성 반경)
VERTICAL_DISTANCE = 1.25 * SATELLITE_R # 위성간 수직거리 (1.25 * 위성 반경)
POS_SATELLITES = {
     1: (-2*SATELLITE_R, 0),
     2: (-2*SATELLITE_R - HORIZONTAL_DISTANCE , 0),
     3: (-2*SATELLITE_R - 2*HORIZONTAL_DISTANCE, 0),
    # 4: (-2*SATELLITE_R, VERTICAL_DISTANCE),
    # 5: (-2*SATELLITE_R - HORIZONTAL_DISTANCE, VERTICAL_DISTANCE),
    # 6: (-2*SATELLITE_R - 2*HORIZONTAL_DISTANCE, VERTICAL_DISTANCE),
    # 7: (-2*SATELLITE_R, -VERTICAL_DISTANCE),
    # 8: (-2*SATELLITE_R - HORIZONTAL_DISTANCE, -VERTICAL_DISTANCE),
    # 9: (-2*SATELLITE_R - 2*HORIZONTAL_DISTANCE, -VERTICAL_DISTANCE),
 }

# NOTE: MESSAGE TYPE DEFINITION
MEASUREMENT_REPORT = "MEASUREMENT_REPORT"
HANDOVER_REQUEST = "HANDOVER_REQUEST"
HANDOVER_ACKNOWLEDGE = "HANDOVER_ACKNOWLEDGE"
HO_COMMAND = "RRC_RECONFIGURATION"
RRC_RECONFIGURATION_COMPLETE = "RRC_RECONFIGURATION_COMPLETE"
RRC_RECONFIGURATION_COMPLETE_RESPONSE = "RRC_RECONFIGURATION_COMPLETE_RESPONSE"
PATH_SHIFT_REQUEST = "PATH_SHIFT_REQUEST"
RETRANSMISSION = "RETRANSMISSION"
AMF_RESPONSE = "AMF_RESPONSE"

CPU_SCALE = 1
PROCESSING_TIME = {
    MEASUREMENT_REPORT: 0.35 * CPU_SCALE,
    HANDOVER_REQUEST: 0.3 * CPU_SCALE,
    HANDOVER_ACKNOWLEDGE: 0.3 * CPU_SCALE,
    RRC_RECONFIGURATION_COMPLETE: 0.3 * CPU_SCALE,
    PATH_SHIFT_REQUEST: 0.3,
    RETRANSMISSION: 0.35 * CPU_SCALE,
    AMF_RESPONSE: 0.15 * CPU_SCALE,
}
    # NOTE "CONSIDER DELAY TIME"
    # physical layer: 0.05 ms (물리계층)
    # encryption: 0.1 ms (암호화)
    # decryption: 0.1 ms (복호화)
    # logic: 0.05 ms (로직처리)
    # hash: 0.05 ms (해쉬)

# NOTE: UE STATE DEFINITION
ACTIVE = "ACTIVE"
WAITING_RRC_CONFIGURATION = "WAITING_RRC_CONFIGURATION"
INACTIVE = "INACTIVE"
RRC_CONFIGURED = "RRC_CONFIGURED"
WAITING_RRC_RECONFIGURATION_COMPLETE_RESPONSE = "WAITING_RRC_RECONFIGURATION_COMPLETE_RESPONSE"

# NOTE: General Parameters
LIGHT_SPEED = 299792458             # 빛의 속도 (m/s)
BOLTZMANN_CONSTANT = 1.380649e-23   # 볼츠만 상수

# NOTE: 3GPP System Level Simulation Parameters
# ㄴ Study Case 9: LEO-600 Satellite Parameters
SC9_CARRIER_FREQUENCY_HZ = 2e9                   # [Hz], DL carrier frequency (2 GHz)
SC9_BANDWIDTH_HZ = 20e6                          # [Hz], DL bandwidth (20 MHz)
SC9_BANDWIDTH_MHZ = SC9_BANDWIDTH_HZ / 1e6       # [MHz], for EIRP density calculation
SC9_SATELLITE_EIRP_DENSITY = 34                  # [dBW/MHz], satellite EIRP density

SC9_SATELLITE_TXPW_dBm = SC9_SATELLITE_EIRP_DENSITY + 10 * math.log10(SC9_BANDWIDTH_MHZ * 1e-6)
SC9_SATELLITE_TXPW_mW = 10 ** (SC9_SATELLITE_TXPW_dBm / 10)
SC9_SATELLITE_TXPW_W  = SC9_SATELLITE_TXPW_mW / 1000

SC9_SATELLITE_TXGAIN = 30                   # DL: Satellite Tx Max Gain (dBi)
SC9_SATELLITE_ALTITUDE = 600000             # DL: Satellite Altitude (m)
SC9_SATELLITE_BEAM_DIAMETER = 50000         # DL: Satellite Beam Diameter (m)
SC9_SATELLITE_3dB_BEAMWIDTH = 4.4127        # DL: Satellite 3dB Beamwidth (degree)
SC9_SATELLITE_ANTENNA_APERTURE = 2          # DL: Satellite Antenna Aperture (m)
SC9_G_OVER_T = 1.1                          # UL: G/T (dB K^(-1))
SC9_SATELLITE_RXGAIN = 30                   # UL: Satellite Rx Max Gain (dBi)
# ㄴ Study Case 9: Handheld Parameters
SC9_HANDHELD_RXGAIN = 0                     # Handheld(UE) Rx Max Gain (dBi)
SC9_HANDHELD_ANTENNA_TEMPERATURE = 290      # Handheld(UE) Antenna Temperature (K) 
SC9_HANDHELD_NOISE_FIGURE = 7               # Handheld(UE) Noise Figure (dB)
SC9_HANDHELD_TXGAIN = 0                     # Handheld(UE) Tx Max Gain (dBi)
SC9_HANDHELD_TXPW_mW = 200                 # Handheld(UE) Tx Power (mW)
SC9_HANDHELD_TXPW_dBm = 23                 # Handheld(UE) Tx Power (dBm)

# --- Handover Trigger Parameters ---
A3_OFFSET = 3  # Event A3 트리거 오프셋 (dB)
TIME_TO_TRIGGER = 0.04 # 트리거 유지 시간 (40ms)










# ------------------------------------------------------------------------------
# Parameters
#TODO
# 1. The UEs will perform random access only the first time, which means the satellites will first goes to the massive UEs.
# 1.1 If restricting only one random access is weird, we can assign UEs during configuration.
# 2. Handover Decision should be set too.

# TODO Under change, the message and Queue size.
'''
The satellite will handle inter-satellite tasks and Random access request at first priority
Random Access and inter-satellite messages will not be restricted to Queue Size.
because that's not the beginning of a handover.
'''
'''
The below constants defined the state machine of UE
'''
# The UE is actively communicating with source base station
# and the UE has not made any action
# The UE sent the measurement report and waiting for configuration
# The UE lost the connection without being RRC configured
# MEANING that the UE failed to be handoff.
# The UE has received the RRC configuration message
# The UE has sent the random access request with RRC_RECONFIGURATION_COMPLETE

# config.py 에 추가할 파라미터 예시

