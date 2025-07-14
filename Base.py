import json
import random


class Base:
    def __init__(self,
                 identity,
                 position_x,
                 position_y,
                 satellite_ground_delay,
                 object_type,
                 env
                 ):
        self.type = object_type
        self.identity = identity
        self.position_x = position_x
        self.position_y = position_y
        self.env = env
        self.satellite_ground_delay = satellite_ground_delay
        self.type = object_type

    def init(self):
        print(
            f"{self.type} {self.identity} deployed at time {self.env.now}, positioned at ({self.position_x},{self.position_y})")
        yield self.env.timeout(1)

    def send_message(self, delay, msg, Q, to):
        """ Send the message with delay simulation

        Args:
            delay: The message propagation delay
            msg: the json object needs to be sent
            Q: the Q of the receiver
            to: the receiver object

        """
        msg['from'] = self.identity
        msg['to'] = to.identity
        msg = json.dumps(msg) # JSON 형식으로 메시지 변환
        print(f"{self.type} {self.identity} sends {to.type} {to.identity} the message {msg} at {self.env.now}") # 메시지 로그 출력 (보내는 순간 기준)
        yield self.env.timeout(delay + random.random() / 1000) # 전파지연 시간만큼 대기 (작은 무작위 시간 0~1ms 추가, Jitter 효과)
        Q.put(msg) # delay 후 받는 대상의 메시지 Queue에 메시지 추가: (handle_message에서 yield self.self/messageQ.get()으로 메시지 수신)