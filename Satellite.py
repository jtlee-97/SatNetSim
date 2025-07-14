import simpy

from Base import *
from config import *
import random

# 메지시의 개수를 카운트하는 클래스
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
                 velocity,
                 satellite_ground_delay,
                 ISL_delay,
                 core_delay,
                 AMF,
                 env):

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
        self.messageQ = simpy.Store(env) # 위성 개별 메시지 큐
        self.AMF = AMF
        self.UEs = None
        self.satellites = None
        self.cpus = simpy.PriorityResource(env, capacity=SATELLITE_CPU)  # simpy.PriorityResource: CPU 리소스, capacity는 위성 CPU 코어 수
        self.counter = cumulativeMessageCount() # 메시지 카운트 객체 초기화

        # Running process (시뮬레이션 동안 처리되는 프로세스)
        self.env.process(self.init())  # 위성 배치에 대한 초기 정보 출력
        self.env.process(self.update_position()) # 시뮬레이션 진행 동안, 위성 위치를 지속 업데이트 
        self.env.process(self.handle_messages()) # 메시지 큐를 감시, 들어오는 메시지를 처리

    def handle_messages(self):
        # 위성 메시지 수신 및 분배 센터 (message Queue에서 작업을 가져와 CPU 처리 프로세스를 시작)
        """ Get the task from message Q and start a CPU processing process """
        while True: # 위성이 꺼지지 않으면 지속 수행하도록 무한 루프
            msg = yield self.messageQ.get() # self.messageQ에서 메시지가 들어올 때까지 무한정 대기, 도착 시 msg 변수에 저장
            # msg 형식인 json을 파이썬 딕셔너리로 변환해 data 변수에 저장
            data = json.loads(msg) 
            task = data['task']
            # Measure the message count: task 종류에 따라 메시지 카운터 증가
            if task == MEASUREMENT_REPORT: self.counter.increment_UE_measurement()
            if task == RETRANSMISSION: self.counter.increment_UE_retransmit()
            if task == HANDOVER_ACKNOWLEDGE: self.counter.increment_satellite()
            if task == HANDOVER_REQUEST: self.counter.increment_satellite()
            if task == RRC_RECONFIGURATION_COMPLETE: self.counter.increment_UE_RA()
            if task == AMF_RESPONSE: self.counter.increment_AMF()

            # 핸드오버 시작 신호인 MR, 재전송에 대해서는 특정 처리를 위한 IF 조건문
            if task == MEASUREMENT_REPORT or task == RETRANSMISSION:
                # CPU 큐에 대기 중인 작업이 QUEUED_SIZE 미만인 경우에만 처리
                # self.cpus.queue는 현재 CPU에 대기 중인 작업의 큐
                # CPU 대기 줄의 길이가 미리 설정된 최대치보다 작을 때만 메시지를 처리 (최대 처리량 초과 시 메시지 드롭)
                if len(self.cpus.queue) < QUEUED_SIZE: # message accept 시
                    print(f"{self.type} {self.identity} accepted msg:{msg} at time {self.env.now}") # 메시지 수용 로그 출력
                    self.env.process(self.cpu_processing(msg=data, priority=2)) # 메시지 처리 프로세스를 시작, 우선순위는 2로 설정
                else: # message drop 시
                    self.counter.increment_dropped() # message drop 카운트 증가
                    print(f"{self.type} {self.identity} dropped msg:{msg} at time {self.env.now}") # 메시지 드롭 로그 출력
            else: # 핸드오버 관련 메시지 처리 (MR. 재전송 외 다른 메시지 처리 시)
                print(f"{self.type} {self.identity} accepted msg:{msg} at time {self.env.now}") # 메시지 수용 로그 출력
                self.env.process(self.cpu_processing(msg=data, priority=1)) # 혼잡 문제 없이 항상 수용, 높은 우선순위로 취급

    # =================== Satellite functions ======================
    # CPU에서 메시지 처리
    def cpu_processing(self, msg, priority):
        """ Process the message with CPU """
        # 위성의 CPU 자원을 할당받기 위한 요청: self.cpus.request(priority=priority)는 handle_messages에서 지정한 priority 값에 따라 CPU 자원을 요청
        with self.cpus.request(priority=priority) as request:
            # CPU 자원 할당이 완료될 때까지 대기 (yield 기반의 Cooperative multitasking)
            yield request
            
            print(f"{self.type} {self.identity} handling msg:{msg} at time {self.env.now}") # CPU 처리 로그 출력
            # msg['task']에서 task 종류를 가져와 task 변수에 저장
            task = msg['task']
            # config.py에서 정의된 PROCESSING_TIME 딕셔너리에서 task에 해당하는 고정 소요 시간 (처리 시간)을 가져옴
            processing_time = PROCESSING_TIME[task]

            # MEASUREMENT REPORT / RETRANSMISSION을 수신
            if task == MEASUREMENT_REPORT or task == RETRANSMISSION:
                ueid = msg['from']
                candidates = msg['candidate']
                UE = self.UEs[ueid]
                # UE가 현재 위성과 연결되어 있는지 확인 후, processing_time 만큼 CPU 처리 시간을 시뮬레이션에 반영
                if self.connected(UE):
                    yield self.env.timeout(processing_time)
                
                if self.connected(UE):
                    # send the response to UE
                    data = {
                        "task": HANDOVER_REQUEST,
                        "ueid": ueid
                    }
                    # for now, just random. TODO
                    """ 수정이 필요한 단계: 핸드오버를 아무 위성에게 무작위로 진행 (2.0 버전까지 미적용)"""
                    target_satellite_id = random.choice(candidates) 
                    target_satellite = self.satellites[target_satellite_id]
                    self.env.process(
                        # 메시지를 타겟 위성에게 전송하는 프로세스 시작
                        self.send_message(
                            delay=self.ISL_delay,
                            msg=data,
                            Q=target_satellite.messageQ,
                            to=target_satellite
                        )
                    )
            
            # HANDOVER ACKNOWLEDGE을 수신
            elif task == HANDOVER_ACKNOWLEDGE:
                satellite_id = msg['from']
                ueid = msg['ueid']
                UE = self.UEs[ueid]
                # UE 연결 상태 확인, CPU 처리시간 처리
                if self.connected(UE):
                    yield self.env.timeout(processing_time)
                # HO COMMAND 생성 (RRC RECONFIGURATION)
                if self.connected(UE):
                    data = {
                        # RRC RECONFIGURATION (HO COMMAND) 메시지를 전송
                        "task": RRC_RECONFIGURATION,
                        "targets": [satellite_id],
                    }
                    self.env.process(
                        self.send_message(
                            delay=self.satellite_ground_delay,
                            msg=data,
                            Q=UE.messageQ,
                            to=UE
                        )
                    )
            
            # HANDOVER REQUEST를 수신
            elif task == HANDOVER_REQUEST:
                satellite_id = msg['from']
                ueid = msg['ueid']
                yield self.env.timeout(processing_time) # CPU 처리 시간 반영
                # HANDOVER REQUEST ACKNOWLEDGE 메시지 생성
                data = {
                    "task": HANDOVER_ACKNOWLEDGE,
                    "ueid": ueid
                }
                # SOURCE SATELLITE에게 HANDOVER REQUEST ACKNOWLEDGE 메시지를 전송
                source_satellite = self.satellites[satellite_id]
                self.env.process(
                    self.send_message(
                        delay=self.ISL_delay,
                        msg=data,
                        Q=source_satellite.messageQ,
                        to=source_satellite
                    )
                )
            
            # RRC RECONFIGURATION COMPLETE을 수신 (Target SATELLITE 대상)    
            elif task == RRC_RECONFIGURATION_COMPLETE:
                ue_id = msg['from']
                UE = self.UEs[ue_id]
                yield self.env.timeout(processing_time)
                # DATA 1: UE에게 보내는 HANDOVER RECONFIGURATION COMPLETE RESPONSE 메시지
                data = {
                    "task": RRC_RECONFIGURATION_COMPLETE_RESPONSE,
                }
                self.env.process(
                    self.send_message(
                        delay=self.satellite_ground_delay,
                        msg=data,
                        Q=UE.messageQ,
                        to=UE
                    )
                )
                # DATA 2: AMF에게 보내는 PATH SHIFT REQUEST 메시지
                data2 = {
                    "task": PATH_SHIFT_REQUEST,
                    "previous_id": msg['previous_id']
                }
                self.env.process(
                    self.send_message(
                        delay=self.core_delay,
                        msg=data2,
                        Q=self.AMF.messageQ,
                        to=self.AMF
                    )
                )
            
            # AMF RESPONSE을 수신 (Path Shift 완료)
            elif task == AMF_RESPONSE:
                yield self.env.timeout(processing_time)
            print(f"{self.type} {self.identity} finished processing msg:{msg} at time {self.env.now}")

    def update_position(self):
        """ Continuous updating the object location. """
        while True:
            # print((len(self.messageQ.items)))
            yield self.env.timeout(1)  # Time between position updates
            # Update x and y based on velocity
            # Calculate time ratio
            ratio = 1 / 1000
            # direction set to right
            self.position_x += self.velocity * ratio

    # ==================== Utils (Not related to Simpy) ==============
    # Check if the UE is connected to this satellite
    # UE가 현재 위성과 연결되어 있는지 확인하는 함수
    # UE가 현재 위성과 연결되어 있으면 True, 아니면 False 반환
    def connected(self, UE):
        if UE.serving_satellite is None:
            return False
        else:
            return UE.serving_satellite.identity == self.identity
