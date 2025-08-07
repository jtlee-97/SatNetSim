import math
import simpy
import json # [추가] JSON 모듈
from Base import *
from config import *

"""
[UE State]
ACTIVE:                                         위성 연결, 정상 작동 상태
WAITING_RRC_CONFIGURATION:                      Serving 위성으로부터 HO COMMAND 대기 상태
RRC_CONFIGURED:                                 HO COMMAND 수신함, Target 위성과 연결을 시작하는 상태
WAITING_RRC_RECONFIGURATION_COMPLETE_RESOPNSE:  RRCRC 대기상태
INACITVE:                                       연결 없음
"""

class UE(Base):
    def __init__(self,
                 identity,
                 position_x,
                 position_y,
                 satellite_ground_delay,
                 serving_satellite,
                 env):

        # Config Initialization
        Base.__init__(self,
                      identity=identity,
                      position_x=position_x,
                      position_y=position_y,
                      env=env,
                      satellite_ground_delay=satellite_ground_delay,
                      object_type="UE")

        # UE 고유 속성 설정 - 초기 serving 위성
        self.serving_satellite = serving_satellite

        # Logic Initialization
        self.timestamps = []

        self.messageQ = simpy.Store(env)
        self.cpus = simpy.Resource(env, UE_CPU)
        self.state = ACTIVE # 초기 상태: ACTIVE
        self.satellites = None 

        self.previous_serving_sat_id = None
        self.targetID = None
        self.retransmit_counter = 0

        # Running Process
        env.process(self.init())
        env.process(self.handle_messages())
        env.process(self.action_monitor())


    # =================== UE functions ======================
    # handle messages: satellite와 동일
    # 수신 메시지의 종류를 구분하거나, 처리 카운팅을 하는 것에 대해 구분되지 않음: UE에게는 그정도로 필요가 없음
    def handle_messages(self):
        while True:
            msg = yield self.messageQ.get()
            print(f"{self.type} {self.identity} start handling msg:{msg} at time {self.env.now}")
            data = json.loads(msg)
            self.env.process(self.cpu_processing(data))

    def cpu_processing(self, msg):
        with self.cpus.request() as request:
            task = msg['task']
            
            # Message Type: HO COMMAND
            # CURRENT UE STATE: WAITING_RRC_CONFIGURATION
            if task == HO_COMMAND:
                yield request # 대기
                satid = msg['from'] # Satellite ID CHECK
                
                # FIXME one error raised for serveing satellite is none, the suspect reason is synchronization issue with "switch to inactive"
                # Note that the UE didn't wait for the latest response for retransmission.
                # 명령 처리 중 연결이 끊켜 self.serving_satellite가 제거되는 경우, 문제가 발생할 수 있는 단계
                
                # WAITING_RRC_CONFIGURATION (HO CMD 대기상태) + 메시지 발신 위성이 기존 서빙 위성과 동일
                if self.state == WAITING_RRC_CONFIGURATION and satid == self.serving_satellite.identity:
                    targets = msg['targets'] # candidate satellite list
                    
                    # choose target
                    # TODO 최종 위성을 리스트의 첫번째 위성으로 선택 중 (현단계)
                    self.targetID = targets[0]
                    
                    self.state = RRC_CONFIGURED # State Change
                    self.previous_serving_sat_id = self.serving_satellite.identity # 이전 서빙 위성 ID 보관
                    self.retransmit_counter = 0 # 재전송 횟수 초기화
                    print(f"{self.type} {self.identity} receives the configuration at {self.env.now}") # Logging
                    
                    # 현재 시간, HO 성공 여부 기록
                    self.timestamps[-1]['timestamp'].append(self.env.now)
                    self.timestamps[-1]['isSuccess'] = True
                    
            # # Message Type: RRC RECONFIGURATION COMPLETE RESPONSE 
            # # CURRENT UE STATE: RRC_CONFIGURED
            # elif task == RRC_RECONFIGURATION_COMPLETE_RESPONSE:
            #     yield request
            #     satid = msg['from']
            #     satellite = self.satellites[satid] # all Satellite list check
                
            #     # TODO satid가 target cell인지 검증하는 절차가 확인으로 추가가 필요함
            #     if self.covered_by(satid): # using coverd_by function
            #         self.serving_satellite = satellite # msg trans. satellite
            #         self.state = ACTIVE # State Change
            #         self.timestamps[-1]['timestamp'].append(self.env.now) # Adding: for MIT
            #         print(f"{self.type} {self.identity} finished handover at {self.env.now}")
            
            # 여기서 task = ULGRANT가 될때로 변경?
            elif task == RRC_ULGRANT:
                yield request
                satid = msg['from']
                target_satellite = self.satellites[satid] # all Satellite list check
                
                # TODO satid가 target cell인지 검증하는 절차가 확인으로 추가가 필요함
                if self.covered_by(satid): # using coverd_by function
                    self.serving_satellite = target_satellite # msg trans. satellite
                    self.state = ACTIVE # State Change
                    self.timestamps[-1]['timestamp'].append(self.env.now) # Adding: for MIT
                    print(f"{self.type} {self.identity} finished handover at {self.env.now}")
                    data = {
                        "task": RRC_RECONFIGURATION_COMPLETE, # RRC RECONFIGURATION COMPLETE 메시지 생성
                        "previous_id": self.previous_serving_sat_id,
                    }
                    self.env.process(
                        self.send_message(
                            delay=self.satellite_ground_delay,
                            msg=data,
                            Q=self.serving_satellite.messageQ,
                            to=self.serving_satellite
                        )
                    )
                    print('Send RRC_RECONFIGURATION_COMPLETE message')

    # =================== Monitoring Process ======================
    # UE 상태를 모니터링, 필요한 경우 메시지를 전송
    # 주기적으로 send_request_condition() 함수(UE가 현재 위치에서 RRC 구성 요청을 보낼 수 있는지 여부를 판단)를 호출
    def action_monitor(self):
        while True:
            # --- ACTION: Send Measurement Report --- 
            if self.state == ACTIVE and self.send_request_condition(): # Condition
                candidates = []
                for satid in self.satellites: # 모든 위성 순회
                    if self.covered_by(satid) and satid != self.serving_satellite.identity: # Condition: in Cover + not Serving Satellite
                        candidates.append(satid)
                # Prepare Measurement Report message
                data = {
                    "task": MEASUREMENT_REPORT,
                    "candidate": candidates,
                }
                
                # Case: Candidate Satellite List Not Empty (at lease 1 over)
                if len(candidates) != 0:
                    # Message Send Protocol Start
                    self.env.process(
                        self.send_message(
                            delay=self.satellite_ground_delay,
                            msg=data,
                            Q=self.serving_satellite.messageQ,
                            to=self.serving_satellite
                        )
                    )
                    self.timestamps.append({'timestamp' : [self.env.now]}) # Logging
                    self.timestamps[-1]['from'] = self.serving_satellite.identity # Logging
                    self.timer = self.env.now # RE-TRANSMIT TIMER START
                    self.state = WAITING_RRC_CONFIGURATION # UE STATE CHANGE
                                    
            # --- ACTION: Trigger retransmission if conditions are met ---
            if RETRANSMIT and self.state == WAITING_RRC_CONFIGURATION \
            and (self.env.now - self.timer) > RETRANSMIT_THRESHOLD \
            and self.retransmit_counter < MAX_RETRANSMIT:
                # NOTE: Retransmission conditions
                # 1. RETRANSMIT enabled (see config.py)
                # 2. UE state is WAITING_RRC_CONFIGURATION
                # 3. Timer exceeded threshold: now - timer > RETRANSMIT_THRESHOLD
                # 4. Retransmission attempts < MAX_RETRANSMIT

                self.timer = self.env.now # retransmit timer reset
                self.timestamps[-1]['timestamp'].append(self.env.now) # Logging
                # NOTE: 현시점 Re-transmit +1회 실시
                
                # Message Send Restart
                candidates = []
                for satid in self.satellites:
                    if self.covered_by(satid) and satid != self.serving_satellite.identity:
                        candidates.append(satid)
                data = {
                    "task": RETRANSMISSION, # message type은 MR이 아닌 재전송으로 변경
                    "candidate": candidates
                }
                if len(candidates) != 0:
                    self.env.process(
                        self.send_message(
                            delay=self.satellite_ground_delay,
                            msg=data,
                            Q=self.serving_satellite.messageQ,
                            to=self.serving_satellite
                        )
                    )
                    self.retransmit_counter += 1 # counter add
            
                    
            # --- ACTION: RANDOM ACCESS Procedure --- 
            if self.state == RRC_CONFIGURED:  # Condition: RRC_CONFIGURED (HO CMD 수신 상태)
                if self.targetID and self.covered_by(self.targetID): # CHECK
                    target = self.satellites[self.targetID]
                    data = {
                        "task": RRC_RANDOM_ACCESS, # RRC RECONFIGURATION COMPLETE 메시지 생성
                    }
                    self.env.process(
                        self.send_message(
                            delay=self.satellite_ground_delay,
                            msg=data,
                            Q=target.messageQ,
                            to=target
                        )
                    )
                    self.state = WAITING_RRC_ULGRANT # STATE CHANGE
            
            # # --- ACTION: RANDOM ACCESS Procedure --- 
            # if self.state == RRC_WAITING_RRC_ULGRANT:  # Condition: RRC_CONFIGURED (HO CMD 수신 상태)
            #     if self.targetID and self.covered_by(self.targetID): # CHECK
            #         target = self.satellites[self.targetID]
            #         data = {
            #             "task": RRC_RECONFIGURATION_COMPLETE, # RRC RECONFIGURATION COMPLETE 메시지 생성
            #         }
            #         self.env.process(
            #             self.send_message(
            #                 delay=self.satellite_ground_delay,
            #                 msg=data,
            #                 Q=target.messageQ,
            #                 to=target
            #             )
            #         )
            #         self.state = WAITING_RRC_RECONFIGURATION_COMPLETE_RESPONSE # STATE CHANGE
                    
            
            # if self.state == RRC_CONFIGURED:  # Condition: RRC_CONFIGURED (HO CMD 수신 상태)
            #     if self.targetID and self.covered_by(self.targetID): # CHECK
            #         target = self.satellites[self.targetID]
            #         data = {
            #             "task": RRC_RECONFIGURATION_COMPLETE, # RRC RECONFIGURATION COMPLETE 메시지 생성
            #             "previous_id": self.previous_serving_sat_id, # 이전 서빙 위성 ID를 포함 (for PATH SWITCHING)
            #         }
            #         self.env.process(
            #             self.send_message(
            #                 delay=self.satellite_ground_delay,
            #                 msg=data,
            #                 Q=target.messageQ,
            #                 to=target
            #             )
            #         )
            #         self.state = WAITING_RRC_RECONFIGURATION_COMPLETE_RESPONSE # STATE CHANGE
                    
            # Switch to INACTIVE State
            if self.serving_satellite is not None and self.outside_coverage():
                # TODO: RLF, HOF 등이 발생하는 경우가 outside_coverage()가 되야함
                print(f"UE {self.identity} lost connection at time {self.env.now} from satellite {self.serving_satellite.identity}") # Logging
                self.serving_satellite = None # serv_idx none
                
                # ACTIVE/HO CMD 대기 상태에서
                if self.state == ACTIVE or self.state == WAITING_RRC_CONFIGURATION:
                    if self.state == WAITING_RRC_CONFIGURATION:
                        print(f"UE {self.identity} handover failure at time {self.env.now}") # Logging
                        self.timestamps[-1]['timestamp'].append(self.env.now) # Logging
                        self.timestamps[-1]['isSuccess'] = False # Logging
                    self.state = INACTIVE # STATE CHANGE
                                
            # 1ms 대기: 1회의 action_monitor 이후, 제어권 인계 (1ms 주기의 모니터링 주기)
            yield self.env.timeout(1)
            

    # ==================== Utils (Not related to Simpy) ==============
    def covered_by(self, satelliteID): # UE가 특정 위성 커버리지 내에 있는가 확인하는 함수
        satellite = self.satellites[satelliteID] # satelliteID에 해당하는 위성 객체를 가져옴
        # 두 점 (UE, 위성) 사이의 거리를 피타고라스 정리를 사용하여 계산 (직선거리)
        d = math.sqrt(((self.position_x - satellite.position_x) ** 2) + (
                (self.position_y - satellite.position_y) ** 2)) 
        return d <= SATELLITE_R # Ture/False 반환

    # 전체 위성 군 중, Request가 가능한 위성 탐색
    def send_request_condition(self):
        p = (self.position_x, self.position_y) # UE의 현재 위치 p 변수에 저장
        d1_serve = math.dist(p, (self.serving_satellite.position_x, self.serving_satellite.position_y)) # d1_serve 변수에 현재 UE와 서비스 중인 위성 간의 거리 저장
        for satid in self.satellites: # 모든 위성을 히나씩 순회하며 검사 시작
            satellite = self.satellites[satid] # 현재 순회 대상이된 위성 객체 전체를 satellite 변수에 저장
            d = math.dist(p, (satellite.position_x, satellite.position_y)) # 순회 대상 위성 객체와 현재 UE 간의 거리 계산
            # 조건문 실행 (타겟 위성 검토)
            # 이웃 위성까지의 거리 d가 현재 서빙 위성거라 d1_serve보다 100미터 이상 작을 경우, True 반환
            # 100 미터는 offset으로 적용된 값으로, 이웃 위성까지의 거리와 현재 서빙 위성까지의 거리가 충분히 멀어야 한다는 의미 (PP 방지)
            if d + 100 < d1_serve:
                return True
        return False

    # TODO: RLF를 기반으로 outside_coverage를 구성해야함 (단순 영역을 벗어나는 것이 아님)
    def outside_coverage(self):
        p = (self.position_x, self.position_y)
        d1_serve = math.dist(p, (self.serving_satellite.position_x, self.serving_satellite.position_y))
        # TODO We may want to remove the second condition someday...
        return d1_serve >= SATELLITE_R and self.position_x < self.serving_satellite.position_x

    
# # [추가] RSRP 계산에 필요한 유틸리티 함수
# # utils.py에 넣어도 되지만, 편의를 위해 여기에 직접 추가합니다.
# def calculate_rsrp(distance_km, tx_power_dbm, freq_mhz, sat_gain_dbi, ue_gain_dbi):
#     """거리를 기반으로 RSRP를 계산하는 함수 (단위: dBm)"""
#     if distance_km == 0:
#         return tx_power_dbm # 거리가 0이면 최대 전력으로 간주
#     # 자유 공간 경로 손실 (FSPL) 계산
#     fspl = 20 * math.log10(distance_km) + 20 * math.log10(freq_mhz) + 32.45
#     rsrp = tx_power_dbm + sat_gain_dbi + ue_gain_dbi - fspl
#     return rsrp