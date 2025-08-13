import sys
import  os
import shutil
import utils
from AMF import *
from Satellite import *
from UE import *
import math
import random

# Config Random Seed
random.seed(SEED)

# 결과물 저장 경로 설정
dir = "defaultres"
if len(sys.argv) != 1: # This is for automation
    dir = sys.argv[1] 
    SATELLITE_CPU = int(sys.argv[2])
    SATELLITE_GROUND_DELAY = int(sys.argv[3])
    # NOTE: Python 명령 인자
    # sys.argv[1]: 결과 디렉토리
    # sys.argv[2]: 위성 CPU 수
    # sys.argv[3]: 위성-지상 지연시간

# 결과물 저장 디렉토리 설정 / 오류해결, 실행 시 디렉토리 초기화
file_path = f"SatNetSim/res/{dir}"
if os.path.exists(file_path):
    try:
        shutil.rmtree(file_path)
    except OSError as e:
        print(f"Error: {e.strerror} - {file_path}")
        print("Please close any programs that may be using files in this directory.")
        sys.exit(1)

for id in POS_SATELLITES:
    os.makedirs(file_path + "/graph_data/sat_" + str(id), exist_ok=True)
os.makedirs(file_path + "/graph", exist_ok=True)

# 현재 실험 설정을 텍스트 파일로 저장
file = open(file_path + "/config_res.txt", "w")
# Close the file
file.write("System Configuration:\n")
file.write(f"  #Satellite Radius: {SATELLITE_R} m\n")
file.write(f"  #Satellite speed: {SATELLITE_V} m/s\n")
file.write(f"  #Number of UEs: {NUMBER_UE}\n")
file.write(f"  #Satellite CPU number: {SATELLITE_CPU}\n")
file.write(f"  #Satellite to ground delay: {SATELLITE_GROUND_DELAY} ms\n")
file.write(f"  #Inter Satellite delay: {SATELLITE_SATELLITE_DELAY} ms\n")

# # NOTE: Simulation 시작 전, 이론적 핸드오버 발생 예상 저장 (현 불필요로 주석처리)
#[예측 1]
t = 1 # 초 마다
d = SATELLITE_V * t # 위성이 이동하는 거리 계산
number_handover = utils.handout(SATELLITE_R, NUMBER_UE, d) # utils.py의 handout 함수로 대략 핸드오버 예상 횟수를 이론적 계산
file.write(f"  #Example: approximate {number_handover} need to be handed over within {t} seconds\n")
#[예측 2]
t = 0.001
d = SATELLITE_V * t
number_handover = utils.handout(SATELLITE_R, NUMBER_UE, d)
file.write(f"  #Example: approximate {number_handover} need to be handed over within {t} seconds\n")
file.close()

# ===================== UE POSITION CONFIG =============================
# NOTE: Simulation UE initial Position Config

# (1) 위성에 커버리지가 겹치는 지역에만 UE를 배치
if len(POS_SATELLITES) < 4:
    ylim_intersect = math.sqrt(SATELLITE_R ** 2 - (HORIZONTAL_DISTANCE / 2) ** 2) - 500
    ylim = (ylim_intersect // GROUP_AREA_L - 1) * GROUP_AREA_L
else:
    ylim_half = VERTICAL_DISTANCE / 2 - 200
    ylim = (ylim_half // GROUP_AREA_L - 1) * GROUP_AREA_L
POSITIONS = utils.generate_points_with_ylim(NUMBER_UE, SATELLITE_R - 100, 0, 0, ylim)

# (2) 위성 영역 내 랜덤 배치
#POSITIONS = utils.generate_points(NUMBER_UE, SATELLITE_R - 1 * 1000, 0, 0)


# ===================== Running Experiment =============================
# This is simply for tracing TIME STAMP in Terminal
def monitor_timestamp(env):
    while True:
        print(f"Simulation Time {env.now}", file=sys.stderr)
        yield env.timeout(1)


# SCREENSHOT: The function draws screenshot of global Status. As drawing takes time, the timestep has to be big.
def global_stats_collector_draw_middle(env, UEs, satellites, timestep):
    while True:
        active_UE_positions = []
        requesting_UE_positions = []
        inactive_positions = []
        for ue_id in UEs:
            ue = UEs[ue_id]
            pos = (ue.position_x, ue.position_y)
            if ue.state == ACTIVE:  # success
                active_UE_positions.append(pos)
            elif ue.state == INACTIVE:
                inactive_positions.append(pos)
            else:
                requesting_UE_positions.append(pos)
        satellite_positions = {}
        for s_id, s in satellites.items():
            satellite_positions[s_id] = (s.position_x, s.position_y)
        
        # [수정] ID 정보가 포함된 딕셔너리를 draw_from_positions 함수로 전달
        utils.draw_from_positions(inactive_positions, active_UE_positions, requesting_UE_positions, env.now,
                                  file_path + "/graph", satellite_positions, SATELLITE_R)
        yield env.timeout(timestep)
        # satellite_positions = []
        # for s_id in satellites:
        #     s = satellites[s_id]
        #     satellite_positions.append((s.position_x, s.position_y))
        # utils.draw_from_positions(inactive_positions, active_UE_positions, requesting_UE_positions, env.now,
        #                           file_path + "/graph", satellite_positions, SATELLITE_R)
        # yield env.timeout(timestep)


# Logging Text: This function collects information but draws(LOG) in the end of the simulation.
def global_stats_collector_draw_final(env, data, UEs, satellites, timestep):
    while True:
        data.x.append(env.now)
        for id in satellites:
            satellite = satellites[id]
            counter = satellite.counter
            if id not in data.numberUnProcessedMessages:
                data.numberUnProcessedMessages[id] = []
                data.cumulative_total_messages[id] = []
                data.cumulative_message_from_UE_measurement[id] = []
                data.cumulative_message_from_UE_retransmit[id] = []
                data.cumulative_message_from_UE_RA[id] = []
                data.cumulative_message_from_satellite[id] = []
                data.cumulative_message_from_dropped[id] = []
                data.cumulative_message_from_AMF[id] = []
            data.numberUnProcessedMessages[id].append(len(satellite.cpus.queue))
            data.cumulative_total_messages[id].append(counter.total_messages)
            data.cumulative_message_from_UE_measurement[id].append(counter.message_from_UE_measurement)
            data.cumulative_message_from_UE_retransmit[id].append(counter.message_from_UE_retransmit)
            data.cumulative_message_from_UE_RA[id].append(counter.message_from_UE_RA)
            data.cumulative_message_from_satellite[id].append(counter.message_from_satellite)
            data.cumulative_message_from_dropped[id].append(counter.message_dropped)
            data.cumulative_message_from_AMF[id].append(counter.message_from_AMF)
        numberUEWaitingRRC = 0
        for id in UEs:
            UE = UEs[id]
            if UE.state == WAITING_RRC_CONFIGURATION:
                numberUEWaitingRRC += 1
        data.numberUEWaitingResponse.append(numberUEWaitingRRC)
        yield env.timeout(timestep)


# ===================== ENTITIES SETUP, CONNECTION, SIMULATION CONFIG and START =============================
env = simpy.Environment() # Simpy Setting

# Generate AMF Entity
amf = AMF(core_delay=CORE_DELAY, env=env)

# Generate Dictionary (UE, Satellites)
UEs = {}
satellites = {}

# Deploying UEs following POS_SATELLITES(ID/POS) in config.py
for sat_id in POS_SATELLITES:
    pos = POS_SATELLITES[sat_id]
    satellites[sat_id] = Satellite(
        identity=sat_id,
        position_x=pos[0],
        position_y=pos[1],
        velocity=SATELLITE_V,
        satellite_ground_delay=SATELLITE_GROUND_DELAY,
        ISL_delay=SATELLITE_SATELLITE_DELAY,
        core_delay=CORE_DELAY,
        AMF=amf,
        env=env)

# Deploying UEs following randomly generated positions
# main 상단부, UE 좌표 설정 기반
for index, position in enumerate(POSITIONS, start=1):
    # Find the closest satellite for the initial connection
    closest_sat_id = -1
    min_dist = float('inf')
    for sat_id, sat in satellites.items():
        dist = math.dist(position, (sat.position_x, sat.position_y))
        if dist < min_dist:
            min_dist = dist
            closest_sat_id = sat_id
            
    UEs[index] = UE(
        identity=index,
        position_x=position[0],
        position_y=position[1],
        #serving_satellite=satellites[1],
        serving_satellite=satellites[closest_sat_id],
        satellite_ground_delay=SATELLITE_GROUND_DELAY,
        env=env)

# Connecting objects (각 객체간 연동, 객체정보 공유)
for identity in satellites:
    satellites[identity].UEs = UEs
    satellites[identity].satellites = satellites
for identity in UEs:
    UEs[identity].satellites = satellites
amf.satellites = satellites

# Process Regist to Simpy Enviornment
env.process(monitor_timestamp(env)) # Monitoring Process
env.process(global_stats_collector_draw_middle(env, UEs, satellites, 200)) # Screenshot Process (200 ms)
data = utils.DataCollection(file_path + "/graph_data") # data collection, data 객체 생성
env.process(global_stats_collector_draw_final(env, data, UEs, satellites, 1)) # stats collector Process (1 ms)

# --- Simulation Start ---
print('==========================================')
print('============= Experiment Log =============')
print('==========================================')
env.run(until=DURATION)
print('==========================================')
print('============= Experiment Ends =============')
print('==========================================')

# HO Timestamps를 data 객체에 전달
data.read_UEs(UEs)

# draw from data
data.draw()
data.save_to_csv(file_path + "/simulation_log.csv")

# Generate Animation
# os.system(f"python src/animation.py {file_path}/graph")