import json
import random

"""
[Base 클래스]: ID 관리, 위치 추적, 구성 가능한 지연을 통한 메시지 전송 기능 등 Simulation Entities에서 상속된 기본 기능 제공
    - identity: 각 entities의 고유 식별자
    - position_x/y: 2D 좌표계에서의 위치 정보
    - satellite_ground_delay: 위성-지상간의 통신지연 시간
    - object_type: entities type ("satellite", "UE", "AMF")
    - env: SimPy 시뮬레이션 환경 객체
"""

# 모든 객체(UE, 위성 등)의 기본이 되는 클래스
class Base:
    # 객체가 처음 생성될 때 실행되는 함수 (초기 설정)
    def __init__(self,
                 identity,
                 position_x,
                 position_y,
                 satellite_ground_delay,
                 object_type,
                 env
                 ):
        # --- 각 객체가 가지는 기본 정보들 ---
        self.type = object_type  # 객체의 종류 ("UE", "satellite" 등)
        self.identity = identity  # 객체의 고유 ID 번호
        self.position_x = position_x  # 2D 맵에서의 x 좌표
        self.position_y = position_y  # 2D 맵에서의 y 좌표
        self.env = env  # 시뮬레이션의 시간과 이벤트를 관리하는 환경(SimPy Env.)
        self.satellite_ground_delay = satellite_ground_delay # 지상-위성 간 신호 지연 시간
        #self.type = object_type # 객체 종류를 다시 한번 저장

    # 객체가 시뮬레이션에 처음 배치될 때 실행되는 함수
    def init(self):
        # 객체가 언제, 어디에 배치되었는지 화면에 출력
        print(f"{self.type} {self.identity} deployed at time {self.env.now}, positioned at ({self.position_x},{self.position_y})")
        # 시뮬레이션에서 1ms 동안 잠시 대기 (다른 프로세스가 실행되도록 양보)
        yield self.env.timeout(1)

    # 다른 객체에게 메시지를 보내는 함수
    def send_message(self, delay, msg, Q, to):
        """
        Args:
            delay: 메시지가 전달되는 데 걸리는 시간 (전파 지연)
            msg: 보낼 메시지 내용 (JSON 객체)
            Q: 메시지를 받을 상대방의 메시지 큐 (우체통 역할)
            to: 메시지를 받을 상대방 객체
        """
        # 메시지 헤더(송/수신 ID) 자동 추가, JSON 형식으로 메시지 변환
        msg['from'] = self.identity
        msg['to'] = to.identity
        msg = json.dumps(msg)
        
        # Logging
        print(f"{self.type} {self.identity} sends {to.type} {to.identity} the message {msg} at {self.env.now}")
        
        # 전파지연 시간만큼 메시지 수신을 대기 (+ 작은 무작위 시간 0~1ms 추가, Jitter 효과)
        yield self.env.timeout(delay + random.random() / 1000)
        
        # delay 후 받는 대상의 메시지 Queue에 메시지 추가: (handle_message에서 yield self.self/messageQ.get()으로 메시지 수신)
        Q.put(msg)