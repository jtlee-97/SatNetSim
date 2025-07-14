import math
import simpy
import json # [추가] JSON 모듈을 사용하기 위해 추가
from Base import *
from config import *

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

        self.serving_satellite = serving_satellite

        # Logic Initialization
        self.timestamps = []

        self.messageQ = simpy.Store(env)
        self.cpus = simpy.Resource(env, UE_CPU)
        self.state = ACTIVE
        self.satellites = None

        self.previous_serving_sat_id = None
        self.targetID = None
        self.retransmit_counter = 0

        # Running Process
        env.process(self.init())
        env.process(self.handle_messages())
        env.process(self.action_monitor())

    # =================== UE functions ======================
    def handle_messages(self):
        while True:
            msg = yield self.messageQ.get()
            print(f"{self.type} {self.identity} start handling msg:{msg} at time {self.env.now}")
            data = json.loads(msg)
            self.env.process(self.cpu_processing(data))

    def cpu_processing(self, msg):
        with self.cpus.request() as request:
            task = msg['task']
            if task == RRC_RECONFIGURATION:
                yield request
                satid = msg['from']
                # TODO one error raised for serveing satellite is none
                # TODO the suspect reason is synchronization issue with "switch to inactive"
                # TODO Note that the UE didn't wait for the latest response for retransmission.
                if self.state == WAITING_RRC_CONFIGURATION and satid == self.serving_satellite.identity:
                    # get candidate target
                    targets = msg['targets']
                    # choose target
                    self.targetID = targets[0]
                    self.state = RRC_CONFIGURED # RRC CONFIGURED 상태로 변경 (HO COMMAND 수신 후, UE의 RRC 구성 완료 단계)
                    self.previous_serving_sat_id = self.serving_satellite.identity
                    self.retransmit_counter = 0
                    print(f"{self.type} {self.identity} receives the configuration at {self.env.now}")
                    self.timestamps[-1]['timestamp'].append(self.env.now)
                    self.timestamps[-1]['isSuccess'] = True
            elif task == RRC_RECONFIGURATION_COMPLETE_RESPONSE:
                yield request
                satid = msg['from']
                satellite = self.satellites[satid]
                if self.covered_by(satid):
                    self.serving_satellite = satellite
                    self.state = ACTIVE
                    print(f"{self.type} {self.identity} finished handover at {self.env.now}")

    # =================== Monitoring Process ======================
    # UE.py의 action_monitor 메소드는 UE의 상태를 모니터링하고, 필요한 경우 메시지를 전송하는 역할을 수행
    # action_monitor 메소드의 loop는 주기적으로 send_request_condition() 함수를 호출
    # send_request_condition() 함수는 UE가 현재 위치에서 RRC 구성 요청을 보낼 수 있는지 여부를 판단 (조건 만족 시, True 반환)
    def action_monitor(self):
        while True:
            # send measurement report
            if self.state == ACTIVE and self.send_request_condition(): # UE가 통신상태인 ACTIVE인지, 그리고 핸드오버 조건이 만족하는지 확인
                candidates = [] # 핸드오버 대상 위성 후보군을 저장할 빈 리스트 생성
                for satid in self.satellites: # 모든 위성을 순회하며, UE가 커버리지 내 있는 위성들에 대해서 candidates 리스트에 추가
                    if self.covered_by(satid) and satid != self.serving_satellite.identity: # 조건: 위성 커버리지 내 존재, 접속하고 있는 위성이 아님
                        candidates.append(satid)
                data = {
                    "task": MEASUREMENT_REPORT,
                    "candidate": candidates,
                } # MEASUREMENT_REPORT 메시지에 후보 위성군 추가
                
                # Simpy 환경 기반으로 메시지를 보내는 작업을 독립적 프로세스로 등록해 실행
                if len(candidates) != 0:
                    self.env.process(
                        self.send_message(
                            delay=self.satellite_ground_delay, # 지연시간
                            msg=data, # 상기 data 딕셔너리: 메시지는 MEASUREMENT_REPORT이며, 후보 위성 목록이 존재
                            Q=self.serving_satellite.messageQ, # 메시지 수신 대상의 메지시 Queue를 지정 (serving_satellite의 messageQ)
                            to=self.serving_satellite # 메시지 수신 대상 (serving_satellite)
                        )
                    )
                    self.timestamps.append({'timestamp' : [self.env.now]}) # timestamp 리시트에 현 시간 추가
                    self.timestamps[-1]['from'] = self.serving_satellite.identity # 현재 UE가 접속하고 있는 위성의 ID를 기록
                    self.timer = self.env.now # 재전송 타이머: 다음 RETRANSMIT 로직에서 해당 시간 기반으로 확인 후 재전송 여부를 결정
                    self.state = WAITING_RRC_CONFIGURATION # UE 상태를 WAITING_RRC_CONFIGURATION으로 변경
                    
            # Retransmit (재전송)
            if RETRANSMIT and self.state == WAITING_RRC_CONFIGURATION and self.env.now - self.timer > RETRANSMIT_THRESHOLD and self.retransmit_counter < MAX_RETRANSMIT:
                # <조건>
                # config.py: RETRANSMIT 상태 True (재전송 기능 활성화)
                # self.state: WAITING_RRC_CONFIGURATION
                # self.env.now - self.timer: 현재 시간과 마지막 재전송 시각의 차이가 RETRANSMIT_THRESHOLD를 초과
                # self.retransmit_counter < MAX_RETRANSMIT: 재전송 횟수가 최대 재전송 횟수 미만 (사전 설정: MAX_RETRANSMIT)
                
                self.timer = self.env.now # 재전송 타이머 (시간) 초기화
                self.timestamps[-1]['timestamp'].append(self.env.now) # retransmission time
                candidates = []
                for satid in self.satellites:
                    if self.covered_by(satid) and satid != self.serving_satellite.identity:
                        candidates.append(satid)
                data = {
                    "task": RETRANSMISSION,
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
                    self.retransmit_counter += 1
                    
            # STEP: RANDOM ACCESS
            if self.state == RRC_CONFIGURED:  # When the UE has the configuration
                if self.targetID and self.covered_by(self.targetID):  # RA 시도 전, 2가지 점검 실시 (targetID가 존재하는지, 그리고 UE가 타겟 위성 커버리지 내에 있는지)
                    target = self.satellites[self.targetID] # 전체 위성 중 targetID에 해당하는 위성 객체를 가져옴
                    data = {
                        "task": RRC_RECONFIGURATION_COMPLETE, # RRC RECONFIGURATION COMPLETE 메시지 생성
                        "previous_id": self.previous_serving_sat_id, # 이전 서빙 위성 ID를 포함 (target 위성이 source 위성에게 UE Context Release 보낼 때 필요)
                    }
                    # Target Satellite에게 RRC RECONFIGURATION COMPLETE 메시지 전송
                    self.env.process(
                        self.send_message(
                            delay=self.satellite_ground_delay,
                            msg=data,
                            Q=target.messageQ,
                            to=target
                        )
                    )
                    # 최종 완료 응답 대기 상태로 변경 (RRC RECONFIGURATION COMPLETE RESPONSE 대기 상태)
                    self.state = WAITING_RRC_RECONFIGURATION_COMPLETE_RESPONSE
                    
            # switch to inactive: UE가 현 서비스 Satellite의 커버리지를 벗어나는 경우
            if self.serving_satellite is not None and self.outside_coverage():
                print(
                    f"UE {self.identity} lost connection at time {self.env.now} from satellite {self.serving_satellite.identity}")
                self.serving_satellite = None
                # 연결 상실 시, UE가 WAITING_RRC_CONFIGURATION 상태이거나 ACTIVE 상태인 경우는 HO 실패로 간주
                # RLF와 HOF state 절차로 변경: TODO
                if self.state == ACTIVE or self.state == WAITING_RRC_CONFIGURATION:
                    if self.state == WAITING_RRC_CONFIGURATION:
                        print(f"UE {self.identity} handover failure at time {self.env.now}")
                        self.timestamps[-1]['timestamp'].append(self.env.now)
                        self.timestamps[-1]['isSuccess'] = False
                    self.state = INACTIVE # 최종적으로 INACTIVE 상태로 변경
            
            # SimPy의 구조상, Coorperative 방식으로, 제어권을 내려야 다른 프로세스가 돌아감: 강제 휴식 없이는 action_monitor의 while True 루프가 무한 루프에 빠짐
            yield self.env.timeout(1)
            

    # ==================== Utils (Not related to Simpy) ==============
    def covered_by(self, satelliteID): # UE가 특정 위성 커버리지 내에 있는가 확인하는 함수
        satellite = self.satellites[satelliteID] # satelliteID에 해당하는 위성 객체를 가져옴
        # 두 점 (UE, 위성) 사이의 거리를 피타고라스 정리를 사용하여 계산 (직선거리)
        d = math.sqrt(((self.position_x - satellite.position_x) ** 2) + (
                (self.position_y - satellite.position_y) ** 2)) 
        return d <= SATELLITE_R # Ture/False 반환

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

    def outside_coverage(self):
        p = (self.position_x, self.position_y)
        d1_serve = math.dist(p, (self.serving_satellite.position_x, self.serving_satellite.position_y))
        # TODO We may want to remove the second condition someday...
        return d1_serve >= SATELLITE_R and self.position_x < self.serving_satellite.position_x
