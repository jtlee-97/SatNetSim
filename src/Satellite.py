import simpy
import random

# Base, config 상속
from Base import *
from config import *

# Message 통계 수집 객체
class cumulativeMessageCount:
    def __init__(self):
        self.total_messages = 0
        self.message_from_UE_measurement = 0
        self.message_from_UE_retransmit = 0
        self.message_from_UE_RA = 0
        self.message_from_satellite = 0
        self.message_dropped = 0
        self.message_from_AMF = 0

    def increment_UE_measurement(self):
        self.total_messages += 1
        self.message_from_UE_measurement += 1

    def increment_UE_retransmit(self):
        self.total_messages += 1
        self.message_from_UE_retransmit += 1

    def increment_satellite(self):
        self.total_messages += 1
        self.message_from_satellite += 1

    def increment_UE_RA(self):
        self.total_messages += 1
        self.message_from_UE_RA += 1

    def increment_AMF(self):
        self.total_messages += 1
        self.message_from_AMF += 1

    def increment_dropped(self):
        self.message_dropped += 1

# 위성 객체의 속성/동작 정의
class Satellite(Base):
    def __init__(self,
                 identity,
                 position_x,
                 position_y,
                 velocity, # 위치 업데이트를 위한 속도
                 satellite_ground_delay, # 위성-지상간의 통신지연 시간
                 ISL_delay, # ISL(X2 link) 통신 지연
                 core_delay, # CPU 리소스 풀
                 AMF,
                 env):

        # Base 객체 초기화
        Base.__init__(self,
                      identity=identity,
                      position_x=position_x,
                      position_y=position_y,
                      env=env,
                      satellite_ground_delay=satellite_ground_delay,
                      object_type="satellite")

        # Config Initialization
        self.ISL_delay = ISL_delay
        self.velocity = velocity
        self.core_delay = core_delay

        # Logic Initialization: 동작에 필요한 내부 변수 설정
        self.messageQ = simpy.Store(env) # message Queue (infinite size)
        self.AMF = AMF
        self.UEs = None
        self.satellites = None
        
        # simpy.PriorityResource: where queueing processes are sorted by priority(우선순위)
        # capacity = CPU Resource (in config.py)
        self.cpus = simpy.PriorityResource(env, capacity=SATELLITE_CPU) 
        self.counter = cumulativeMessageCount() # 메시지 카운트 객체 초기화

        # Running process(SimPy>Env>process): Satellite에 Process를 정의 (To Do List 입력)
        # env.process에 동시수행 process 리스트를 입력
        self.env.process(self.init()) # Init process
        self.env.process(self.update_position()) # Positioning Process
        self.env.process(self.handle_messages()) # Message Queue Process


    # =================== Message Process ======================
        """
        MessageQ(simpy:Store) > handle_messages() > Message Type Check (Accept/Drop) > if accept: CPU processing() / if drop: handle_messages()
        
        """
    # Message Process Main Loop (simpy.env.process)
    def handle_messages(self):
        while True:
            # message Queue (infinite size): msg 대기 > self.messageQ에서 get()
            # msg (json) > python dictionary 변환 >> data에 저장
            msg = yield self.messageQ.get()
            data = json.loads(msg) 
                        
            # 메시지 타입 추출 후, Measure the message count: task 종류에 따라 메시지 카운터 증가
            task = data['task']
            if task == MEASUREMENT_REPORT: self.counter.increment_UE_measurement()
            if task == RETRANSMISSION: self.counter.increment_UE_retransmit()
            if task == HANDOVER_REQUEST_ACKNOWLEDGE: self.counter.increment_satellite()
            if task == HANDOVER_REQUEST: self.counter.increment_satellite()
            if task == RRC_RANDOM_ACCESS: self.counter.increment_UE_RA()
            if task == RRC_RECONFIGURATION_COMPLETE: self.counter.increment_UE_RA()
            if task == AMF_RESPONSE: self.counter.increment_AMF()

            # Measurement Report, Re-transmission
            if task == MEASUREMENT_REPORT or task == RETRANSMISSION:
                # Queue 대기 작업이 QUEUED_SIZE 미만인 경우에만 처리
                if len(self.cpus.queue) < QUEUED_SIZE:
                    print(f"{self.type} {self.identity} accepted msg:{msg} at time {self.env.now:.3f}") # Logging
                    self.env.process(self.cpu_processing(msg=data, msg_priority=2)) # Message Processing (priority second)
                else: # Message Drop
                    self.counter.increment_dropped() # message drop 카운트 증가
                    print(f"{self.type} {self.identity} dropped msg:{msg} at time {self.env.now:.3f}") # Logging
            else: # HO ACK, HO Request. RRC RC, AMF Response
                print(f"{self.type} {self.identity} accepted msg:{msg} at time {self.env.now:.3f}") # Logging
                self.env.process(self.cpu_processing(msg=data, msg_priority=1)) # Message Processing (priority first)


    # =================== Satellite functions ======================
    # Message 선별 후, 해당하는 Message Type에 따라 cpu_processing Start
    def cpu_processing(self, msg, msg_priority):
        # Priority 기반 CPU 요청
        with self.cpus.request(priority=msg_priority) as request:
            # simpy > request: 객체, request 객체 내 priority 할당
            # with ~ as: Context Manager 문법, 사용 완료 시 객체를 자동으로 release(close)
            
            yield request # Processing Pause
            
            # Processing Start
            print(f"{self.type} {self.identity} handling msg:{msg} at time {self.env.now:.3f}") # CPU 처리 Logging
            
            task = msg['task'] # msg 내 task 종류 확인
            processing_time = PROCESSING_TIME[task] # task time (in config.py > PROCESSING_TIME)

            # (Serving Satellite) Message Type: MEASUREMENT REPORT / RETRANSMISSION
            if task == MEASUREMENT_REPORT:
                yield request
                processing_time = 1  # 메시지 처리 시간 1ms 가정
                
                ueid = msg['from']
                # UE가 보낸 상세 측정 정보 리스트를 가져옵니다.
                # UE.py에서 "candidate_measurements" 키를 사용했으므로 여기서도 맞춰줍니다.
                candidate_measurements = msg['candidate_measurements']
                UE = self.UEs[ueid]

                if self.connected(UE):
                    yield self.env.timeout(processing_time)

                    if self.connected(UE):
                        # --- 최적 타겟 선정 로직 시작 ---
                        best_target_id = -1
                        best_target_sinr = -float('inf')

                        # UE가 보낸 측정 정보 리스트를 순회하며 SINR이 가장 높은 위성을 찾습니다.
                        for report in candidate_measurements:
                            if report['sinr'] > best_target_sinr:
                                best_target_sinr = report['sinr']
                                best_target_id = report['id'] # 딕셔너리에서 'id' 값 추출
                        # --- 최적 타겟 선정 로직 끝 ---

                        if best_target_id != -1:
                            print(f"--- Satellite {self.identity} chose target {best_target_id} for UE {ueid} (Best SINR: {best_target_sinr:.2f} dB) ---")
                            target_satellite = self.satellites[best_target_id]
                            
                            data = {
                                "task": HANDOVER_REQUEST,
                                "ueid": ueid
                            }
                            
                            self.env.process(self.send_message(delay=self.ISL_delay, msg=data, Q=target_satellite.messageQ, to=target_satellite))
                        else:
                            print(f"Satellite {self.identity} could not find a suitable HO target for UE {ueid}.")
            
            if task == RETRANSMISSION:
                ueid = msg['from'] # Message를 전송한 UE ID
                candidates = msg['candidate'] # 핸드오버 후보 위성 목록
                UE = self.UEs[ueid] # UE ID를 활용해 UE 객체 호출
                
                # 위성과 UE의 연결 상태 확인
                if self.connected(UE):
                    yield self.env.timeout(processing_time) # 해당 시, 메시지 처리 시간 반영 (sim time 소모)
                
                # 메시지 처리 후에도 연결 상태 다시 확인 (도중 연결 손실 시 다음절차 진행 X)
                if self.connected(UE):
                    # Candidate Satellite에게 HO Request 준비
                    data = {
                        "task": HANDOVER_REQUEST, # Message 생성
                        "ueid": ueid # 대상 UE ID 설정
                    }
                    
                    # TODO for now, just random
                    """ 
                    현 단계: target 위성 랜덤 선택
                    향후 추진: 핸드오버 조건식에 대한 판별 구현 필요 
                    """
                    target_satellite_id = random.choice(candidates) 
                    target_satellite = self.satellites[target_satellite_id]
                    
                    # 선택된 Target 위성에게 Handover Request message 전송 프로세스 시작
                    self.env.process(
                        self.send_message(
                            delay=self.ISL_delay,
                            msg=data,
                            Q=target_satellite.messageQ,
                            to=target_satellite
                        )
                    )
            
            
            # (Candidate Satellite) Message Type: HANDOVER REQUEST
            elif task == HANDOVER_REQUEST:
                satellite_id = msg['from']
                ueid = msg['ueid']
                
                yield self.env.timeout(processing_time) # Handover Request Message 처리
                
                # HANDOVER REQUEST ACKNOWLEDGE 메시지 생성
                data = {
                    "task": HANDOVER_REQUEST_ACKNOWLEDGE,
                    "ueid": ueid
                }
                source_satellite = self.satellites[satellite_id]
                self.env.process(
                    self.send_message(
                        delay=self.ISL_delay,
                        msg=data,
                        Q=source_satellite.messageQ,
                        to=source_satellite
                    )
                )
            
            
            # (Serving Satellite) Message Type: HANDOVER_REQUEST_ACKNOWLEDGE
            elif task == HANDOVER_REQUEST_ACKNOWLEDGE:
                satellite_id = msg['from']
                ueid = msg['ueid']
                UE = self.UEs[ueid]
                
                # UE 연결 상태 확인, CPU 처리시간 처리
                if self.connected(UE):
                    yield self.env.timeout(processing_time) # Handover Acknowledge Message 처리
                    
                # HO COMMAND(RRC RECONFIGURATION) 생성
                if self.connected(UE):
                    data = {
                        # HO COMMAND(RRC RECONFIGURATION) 메시지를 전송
                        "task": HO_COMMAND,
                        "targets": [satellite_id], # Target 위성 ID 전달
                    }
                    self.env.process(
                        self.send_message(
                            delay=self.satellite_ground_delay,
                            msg=data,
                            Q=UE.messageQ,
                            to=UE
                        )
                    )
            
            
            # (Target Satellite) Message Type: RANDOM_ACCESS
            elif task == RRC_RANDOM_ACCESS:
                ue_id = msg['from']
                UE = self.UEs[ue_id]
                yield self.env.timeout(processing_time)
                data = {
                    "task": RRC_ULGRANT,
                }
                self.env.process(
                    self.send_message(
                        delay=self.satellite_ground_delay,
                        msg=data,
                        Q=UE.messageQ,
                        to=UE
                    )
                )
            
            
            # (Target Satellite) Message Type: RRC RECONFIGURATION COMPLETE
            elif task == RRC_RECONFIGURATION_COMPLETE:
                ue_id = msg['from']
                UE = self.UEs[ue_id]
                yield self.env.timeout(processing_time)
                
                # BHO:: UE: ULGRANT 수신 후 RRC RECONFIGURATION COMPLETE 이후, 추가 message X
                # # DATA 1: (to UE) HANDOVER RECONFIGURATION COMPLETE RESPONSE Message
                # data = {
                #     "task": RRC_RECONFIGURATION_COMPLETE_RESPONSE,
                # }
                # self.env.process(
                #     self.send_message(
                #         delay=self.satellite_ground_delay,
                #         msg=data,
                #         Q=UE.messageQ,
                #         to=UE
                #     )
                # )
                # DATA 2: (to AMF) PATH SHIFT REQUEST Message
                data2 = {
                    "task": PATH_SHIFT_REQUEST,
                    "previous_id": msg['previous_id'] # 이전 Satellite ID 전달
                }
                self.env.process(
                    self.send_message(
                        delay=self.core_delay,
                        msg=data2,
                        Q=self.AMF.messageQ,
                        to=self.AMF
                    )
                )
            
            
            # Message Type: AMF RESPONSE을 수신 (AMF의 Path Shift 완료)
            elif task == AMF_RESPONSE:
                yield self.env.timeout(processing_time)
            print(f"{self.type} {self.identity} finished processing msg:{msg} at time {self.env.now:.3f}")


    # Continuous updating the object location.
    # env.process(self.update_position() 등록, Simulation 시작시 동시 실행)
    def update_position(self):
        while True:
            yield self.env.timeout(1) # 위치 업데이트 주기 (ms)
            ratio = 1 / 1000 # Calculate time ratio (7.56*1000 m/s > 1ms)
            self.position_x += self.velocity * ratio # moving to x axis

    # ==================== Utils (Not related to Simpy) ==============
    # Check if the UE is connected to this satellite
    # UE가 현재 위성과 연결되어 있는지 확인하는 함수
    # UE가 현재 위성과 연결되어 있으면 True, 아니면 False 반환
    def connected(self, UE):
        if UE.serving_satellite is None:
            return False
        else:
            return UE.serving_satellite.identity == self.identity # UE의 서빙위성 ID와 자기(위성)의 ID를 비교
